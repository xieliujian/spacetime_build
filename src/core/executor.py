"""确定性同步任务执行器：按规划层执行并收集产物。

本模块提供 ``TaskExecutor`` 与 ``ExecutionResult``。执行器按 ``PlannedBuild``
的确定性层序单线程调用 ``BuildTask.execute``，仅把显式上游依赖的产物组成
``ArtifactCollection`` 传入，并在登记前严格校验 ``TaskResult`` 输出路径集合与
``TaskSpec.outputs`` 完全相等。任务异常包装为 ``ToolExecutionError`` 后停止新
调度，不递归重启流水线。恢复跳过仅消费 ``VerifiedFrontier``。导入本模块不执
行构建，也不访问外部系统。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from core.artifacts import LogicalArtifact
from core.errors import ArtifactValidationError, ToolExecutionError
from core.frontier import VerifiedFrontier
from core.planner import PlannedBuild
from core.tasks import (
    ArtifactCollection,
    BuildContext,
    BuildTask,
    TaskResult,
)


def _validate_task_outputs(
    task_name: str,
    expected_outputs: frozenset[str],
    outputs: tuple[LogicalArtifact, ...],
) -> None:
    """校验 TaskResult 输出路径集合与 TaskSpec 声明完全一致。

    参数：
        task_name: 任务逻辑名，用于错误消息。
        expected_outputs: ``TaskSpec.outputs`` 声明的逻辑路径集合。
        outputs: ``TaskResult.outputs`` 元组。

    返回：
        无；校验通过时静默返回。

    异常：
        存在重复路径，或实际路径集合与声明不完全相等时，抛出
        ``ArtifactValidationError``；消息列出 missing、undeclared、duplicates。

    约束与副作用：
        必须在写入 registry 或调度下游前调用；纯校验，无 I/O。
    """
    actual_paths = [artifact.logical_path for artifact in outputs]
    # 先拒绝重复路径，避免 frozenset 比较掩盖重复冲突。
    seen: set[str] = set()
    duplicates: list[str] = []
    for path in actual_paths:
        if path in seen:
            if path not in duplicates:
                duplicates.append(path)
        else:
            seen.add(path)

    actual_set = frozenset(actual_paths)
    missing = sorted(expected_outputs - actual_set, key=lambda p: p.encode("utf-8"))
    undeclared = sorted(actual_set - expected_outputs, key=lambda p: p.encode("utf-8"))
    # 稳定列出 duplicates，保证错误消息与输入顺序无关。
    duplicates_sorted = sorted(duplicates, key=lambda p: p.encode("utf-8"))

    if duplicates_sorted or missing or undeclared:
        raise ArtifactValidationError(
            f"任务 {task_name} 的 TaskResult.outputs 违反输出契约："
            f"missing={missing}；undeclared={undeclared}；"
            f"duplicates={duplicates_sorted}"
        )


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """单次同步执行完成后的不可变结果。

    职责：
        承载执行（及经验证跳过）产生的全部 ``LogicalArtifact`` 元组，供上层
        组装 BuildManifest 或继续发布聚合。

    参数：
        artifacts: 全部逻辑产物的不可变元组；顺序不作为协议契约，仅保证完整。

    返回：
        无；本类为不可变数据载体。

    异常：
        无；非法结果由执行器在构造前拒绝。

    约束与副作用：
        ``frozen=True, slots=True``；不写盘，无外部副作用。
    """

    artifacts: tuple[LogicalArtifact, ...]


class TaskExecutor:
    """按规划层单线程同步执行任务的参考执行器。

    职责：
        消费 ``PlannedBuild`` 与任务映射，按层与层内稳定顺序调用
        ``BuildTask.execute``，只传入显式上游产物，校验输出契约后登记，并
        收集全部 ``LogicalArtifact``；可选跳过 ``VerifiedFrontier`` 中已验证节点。

    参数：
        无构造参数；通过 ``execute`` 接收全部输入。

    返回：
        ``execute`` 返回 ``ExecutionResult``。

    异常：
        ``verified_frontier`` 类型非法时抛出 ``TypeError``；输出契约违例时抛出
        ``ArtifactValidationError``；任务执行失败时抛出 ``ToolExecutionError``。

    约束与副作用：
        单线程、无并行；只消费 ``VerifiedFrontier``；失败即停止，不递归重启
        完整流水线；无 I/O。
    """

    def execute(
        self,
        planned_build: PlannedBuild,
        tasks: Mapping[str, BuildTask],
        context: BuildContext,
        verified_frontier: VerifiedFrontier | None = None,
    ) -> ExecutionResult:
        """按规划层执行任务并收集全部逻辑产物。

        参数：
            planned_build: 已分层的 ``PlannedBuild``。
            tasks: 任务名到 ``BuildTask`` 的映射；须覆盖规划中全部节点。
            context: 共享 ``BuildContext``。
            verified_frontier: 可选 ``VerifiedFrontier``；``None`` 表示全量执行。
                运行时仅接受 ``VerifiedFrontier`` 或 ``None``，拒绝未经验证的
                原始 frontier 类型或完成记录实例。

        返回：
            含全部 ``LogicalArtifact`` 的 ``ExecutionResult``（含跳过节点注入）。

        异常：
            ``verified_frontier`` 类型非法时抛出 ``TypeError``；任务缺失时抛出
            ``KeyError``；输出契约违例时抛出 ``ArtifactValidationError``；任务
            ``execute`` 抛出的异常包装为 ``ToolExecutionError``（保留 cause）。

        约束与副作用：
            单线程同步；每个节点只收到 ``spec.dependencies`` 对应上游产物；
            已验证节点不调用 ``execute``，只注入 frontier 输出；执行结果须与
            ``TaskSpec.outputs`` 路径集合完全相等才登记；任一节点失败即停止新
            调度，不递归重启、不重跑已成功节点；重试留给后续适配器。
        """
        # 运行时拒绝非 VerifiedFrontier，避免把未校验的 Frontier/记录误当恢复输入。
        frontier_obj = cast(object, verified_frontier)
        if frontier_obj is not None and not isinstance(frontier_obj, VerifiedFrontier):
            raise TypeError(
                "verified_frontier 必须是 VerifiedFrontier 或 None，"
                f"实际类型：{type(frontier_obj).__name__}"
            )

        verified_names: frozenset[str] = (
            frozenset() if verified_frontier is None else verified_frontier.task_names
        )
        registry: dict[str, tuple[LogicalArtifact, ...]] = {}
        collected: list[LogicalArtifact] = []

        for layer in planned_build.layers:
            for task_name in layer:
                plan = planned_build.graph.plan_of(task_name)
                if task_name in verified_names:
                    # 只消费已冻结的 VerifiedFrontier，不在此复判身份或 Blob 哈希。
                    assert verified_frontier is not None
                    outputs = verified_frontier.outputs[task_name]
                    registry[task_name] = outputs
                    collected.extend(outputs)
                    continue

                task = tasks[task_name]
                # 只组装显式上游依赖的产物，避免把无关 registry 条目泄漏给节点。
                upstream_artifacts: list[LogicalArtifact] = []
                for dep_name in plan.spec.dependencies:
                    upstream_artifacts.extend(registry[dep_name])
                inputs = ArtifactCollection.from_artifacts(upstream_artifacts)
                try:
                    result: TaskResult = task.execute(context, inputs)
                except Exception as exc:
                    # 捕获单节点失败并退出当前执行；禁止递归重启完整流水线。
                    raise ToolExecutionError(f"任务 {task_name} 执行失败：{exc}") from exc
                # 登记前严格校验：缺失、未声明、重复路径均拒绝。
                _validate_task_outputs(task_name, plan.spec.outputs, result.outputs)
                registry[task_name] = result.outputs
                collected.extend(result.outputs)

        return ExecutionResult(artifacts=tuple(collected))
