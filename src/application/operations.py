"""application 状态、取消、恢复和回滚薄用例。

本模块不重新实现资源 Frontier 或发布 Recovery/Rollback 规则。它只维护当前进程中
请求取消的单向令牌，并把恢复、回滚委托给已有纯领域 planner 或显式注入的执行器。
恢复 action 只调用一次，禁止通过递归方式重启完整流水线。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from application.model import RunState, transition_run_state
from core.errors import BuildError
from ports.process import CancellationToken
from release.activation import VerifiedReleaseBundle
from release.recovery import RecoveryDecision, ReleaseRecoveryPlanner, ReleaseRecoveryState
from release.rollback import ReleaseRollbackPlan, ReleaseRollbackPlanner
from release.upload_plan import UploadPlan


class OperationError(BuildError):
    """表示运行操作的身份、状态或委托参数非法。"""


_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class OperationResult:
    """取消/恢复/回滚操作的稳定结果摘要。"""

    run_id: str
    state: RunState
    idempotent: bool
    value: object | None


class RunOperations:
    """提供幂等取消、显式恢复和发布回滚委托。"""

    def __init__(self) -> None:
        """创建空的进程内运行索引和取消令牌表。"""
        self._states: dict[str, RunState] = {}
        self._tokens: dict[str, CancellationToken] = {}

    def cancel(self, run_id: str, current_state: RunState | None = None) -> OperationResult:
        """请求停止一个运行，重复请求不再次产生副作用。

        参数：
            run_id: 已知运行身份。
            current_state: 首次调用时可显式提供的持久化状态；后续从本地索引读取。

        返回：
            首次成功请求为 ``CANCEL_REQUESTED``；重复请求标记 ``idempotent``。

        异常：
            未知 run、终态取消或状态类型非法时抛 ``OperationError``。

        约束与副作用：
            只设置协作取消令牌，不终止任意进程；进程终止由 ProcessRunner 适配器执行。
        """
        if not isinstance(run_id, str) or not run_id:
            raise OperationError("run_id 必须是非空字符串")
        if current_state is not None:
            if not isinstance(current_state, RunState):
                raise OperationError("current_state 必须是 RunState")
            known_state = self._states.get(run_id)
            if known_state is not None and known_state is not current_state:
                raise OperationError("run 状态与持久化状态不一致")
            self._states.setdefault(run_id, current_state)
        state = self._states.get(run_id)
        if state is None:
            raise OperationError(f"未知 run: {run_id}")
        if state in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED, RunState.CONFLICTED}:
            raise OperationError(f"终态 run 不可取消: {state.value}")
        if state is RunState.CANCEL_REQUESTED:
            return OperationResult(run_id, state, True, self._tokens[run_id])
        target = transition_run_state(state, RunState.CANCEL_REQUESTED)
        token = self._tokens.setdefault(run_id, CancellationToken())
        token.cancel()
        self._states[run_id] = target
        return OperationResult(run_id, target, False, token)

    def resume(
        self,
        run_id: str,
        state: RunState,
        action: Callable[[], _T],
    ) -> OperationResult:
        """对可恢复状态调用一次外部恢复 action，不递归重启全流程。

        参数：
            run_id: 运行身份。
            state: 调用方已读取的当前状态。
            action: 一个显式恢复步骤；由调用方负责幂等和记录新状态。

        返回：
            携带 action 返回值的 ``OperationResult``。

        异常：
            终态、身份或 action 类型非法时抛 ``OperationError``；action 异常原样传播。

        约束与副作用：
            只调用 action 一次，不在内部循环或递归启动完整构建。
        """
        if not isinstance(run_id, str) or not run_id:
            raise OperationError("run_id 必须是非空字符串")
        if not isinstance(state, RunState):
            raise OperationError("state 必须是 RunState")
        if state in {RunState.SUCCEEDED, RunState.CANCELLED, RunState.CONFLICTED}:
            raise OperationError(f"状态不可恢复: {state.value}")
        if not callable(action):
            raise OperationError("action 必须是可调用对象")
        return OperationResult(run_id, state, False, action())

    def release_recovery(
        self,
        state: ReleaseRecoveryState,
        *,
        objects_verified: bool,
    ) -> RecoveryDecision:
        """委托既有发布恢复 planner 返回下一阶段，不直接执行。"""
        return ReleaseRecoveryPlanner.decide(state, objects_verified=objects_verified)

    def rollback(
        self,
        current_bundle_id: str,
        target_bundle_id: str,
        target_plan: UploadPlan,
        verification: VerifiedReleaseBundle,
        activator: object,
    ) -> OperationResult:
        """创建历史 Bundle 回滚计划并调用一次显式激活器。"""
        plan: ReleaseRollbackPlan = ReleaseRollbackPlanner.create(
            current_bundle_id,
            target_bundle_id,
            target_plan,
            verification,
        )
        activate = getattr(activator, "activate", None)
        if not callable(activate):
            raise OperationError("activator 必须提供 activate 方法")
        receipt = activate(plan.target_plan, plan.verification)
        return OperationResult(target_bundle_id, RunState.SUCCEEDED, False, receipt)


__all__ = ["OperationError", "OperationResult", "RunOperations"]
