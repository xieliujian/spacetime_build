"""验证 application 请求身份与统一运行状态机。"""

import pytest

from core.errors import ConfigurationError
from core.platforms import BuildPlatform
from application.model import (
    ApplicationRequest,
    RunResult,
    RunState,
    can_transition,
    transition_run_state,
)


def test_application_request_is_immutable_and_keeps_fixed_identity_fields() -> None:
    """Given 固定 revision 请求，When 构造，Then 字段保持不可变且不混用其他 ID。"""
    request = ApplicationRequest(
        run_id="run-001",
        profile="release",
        source_revision="12345",
        platform=BuildPlatform.ANDROID,
        dry_run=True,
    )

    assert request.run_id == "run-001"
    assert request.source_revision == "12345"
    assert request.platform is BuildPlatform.ANDROID
    with pytest.raises((AttributeError, TypeError)):
        request.run_id = "run-002"  # type: ignore[misc]


@pytest.mark.parametrize("revision", ("HEAD", "head", "latest", "", "1\n2"))
def test_application_request_rejects_unfixed_revision(revision: str) -> None:
    """Given 浮动或非法 revision，When 构造，Then 在进入用例前失败。"""
    with pytest.raises(ConfigurationError):
        ApplicationRequest(
            run_id="run-001",
            profile="release",
            source_revision=revision,
            platform=BuildPlatform.IOS,
            dry_run=False,
        )


def test_run_state_transitions_are_explicit_and_terminal_states_cannot_move() -> None:
    """Given 生命周期状态，When 请求转移，Then 只允许声明过的边。"""
    assert can_transition(RunState.CREATED, RunState.PREFLIGHTED)
    assert can_transition(RunState.PREFLIGHTED, RunState.PLANNED)
    assert can_transition(RunState.RUNNING, RunState.CANCEL_REQUESTED)
    assert transition_run_state(RunState.CREATED, RunState.PREFLIGHTED) is RunState.PREFLIGHTED

    for state in (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED, RunState.CONFLICTED):
        assert not can_transition(state, RunState.RUNNING)
        with pytest.raises(ConfigurationError):
            transition_run_state(state, RunState.RUNNING)


def test_run_result_requires_a_terminal_state_and_stable_identifiers() -> None:
    """Given 运行结果，When 构造，Then 只允许终态并冻结产物 ID 顺序。"""
    result = RunResult(
        run_id="run-001",
        state=RunState.SUCCEEDED,
        record_locator="runs/run-001/record.json",
        artifact_ids=("manifest-1", "bundle-1"),
    )
    assert result.artifact_ids == ("manifest-1", "bundle-1")

    with pytest.raises(ConfigurationError):
        RunResult("run-001", RunState.RUNNING, "runs/run-001/record.json", ())
