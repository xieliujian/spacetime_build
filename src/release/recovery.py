"""发布中断后的下一阶段恢复决策。

本模块只把持久状态映射为下一步纯数据决策，不启动任务、不递归重试、不上传对象。
调用方必须复核结果包、远端对象和 CAS 当前代际后，显式调用对应服务。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReleaseRecoveryState(Enum):
    """可恢复与终态发布状态。"""

    RESERVED = "reserved"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    VERIFIED = "verified"
    PUBLISHED = "published"
    CANCELLED = "cancelled"


class RecoveryStage(Enum):
    """恢复决策下一阶段。"""

    UPLOAD = "upload"
    VERIFY = "verify"
    ACTIVATE = "activate"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    """不执行副作用的恢复决策。"""

    stage: RecoveryStage
    execute: bool
    reason: str


class ReleaseRecoveryPlanner:
    """根据已持久化发布状态返回下一阶段。"""

    @staticmethod
    def decide(
        state: ReleaseRecoveryState,
        *,
        objects_verified: bool,
    ) -> RecoveryDecision:
        """为非终态返回显式下一阶段，不自动执行。

        参数：
            state: 已持久化发布状态。
            objects_verified: 调用方是否已有完整远端对象证明。

        返回：
            ``execute=False`` 的下一阶段决策。

        异常：
            取消、已发布或状态类型非法时抛出 ``ValueError``。

        约束与副作用：
            纯函数；不读取对象、不启动上传、不改变状态。
        """
        if not isinstance(state, ReleaseRecoveryState):
            raise TypeError("state 必须是 ReleaseRecoveryState")
        if not isinstance(objects_verified, bool):
            raise TypeError("objects_verified 必须是 bool")
        if state in {ReleaseRecoveryState.CANCELLED, ReleaseRecoveryState.PUBLISHED}:
            raise ValueError(f"状态 {state.value} 不允许自动恢复")
        if state in {ReleaseRecoveryState.RESERVED, ReleaseRecoveryState.UPLOADING}:
            return RecoveryDecision(RecoveryStage.UPLOAD, False, "需要复核并继续不可变对象上传")
        if state is ReleaseRecoveryState.UPLOADED:
            return RecoveryDecision(RecoveryStage.VERIFY, False, "需要重新验证远端对象")
        if objects_verified:
            return RecoveryDecision(
                RecoveryStage.ACTIVATE, False, "已有远端证明，等待显式 CAS 激活"
            )
        raise ValueError("VERIFIED 状态必须携带远端对象证明")
