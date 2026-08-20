"""发布恢复决策的纯函数测试。"""

import pytest

from release.recovery import (
    RecoveryStage,
    ReleaseRecoveryPlanner,
    ReleaseRecoveryState,
)


@pytest.mark.parametrize(
    "state,stage",
    [
        (ReleaseRecoveryState.RESERVED, RecoveryStage.UPLOAD),
        (ReleaseRecoveryState.UPLOADING, RecoveryStage.UPLOAD),
        (ReleaseRecoveryState.UPLOADED, RecoveryStage.VERIFY),
        (ReleaseRecoveryState.VERIFIED, RecoveryStage.ACTIVATE),
    ],
)
def test_recovery_returns_next_stage_without_running_it(
    state: ReleaseRecoveryState, stage: RecoveryStage
) -> None:
    """验证每个可恢复状态只产生下一阶段决策。"""
    decision = ReleaseRecoveryPlanner.decide(
        state, objects_verified=state is ReleaseRecoveryState.VERIFIED
    )
    assert decision.stage is stage
    assert decision.execute is False


def test_recovery_rejects_cancelled_or_terminal_state() -> None:
    """验证取消和终态不会被当作可自动恢复。"""
    with pytest.raises(ValueError):
        ReleaseRecoveryPlanner.decide(ReleaseRecoveryState.CANCELLED, objects_verified=False)
    with pytest.raises(ValueError):
        ReleaseRecoveryPlanner.decide(ReleaseRecoveryState.PUBLISHED, objects_verified=True)
