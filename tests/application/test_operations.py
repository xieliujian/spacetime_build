"""验证状态、取消、恢复和回滚薄用例不递归重启。"""

import pytest

from application.model import RunState
from application.operations import OperationError, RunOperations


def test_cancel_is_idempotent_and_terminal_run_is_rejected() -> None:
    """Given 可取消运行，When 重复取消，Then 第二次无副作用且终态拒绝。"""
    operations = RunOperations()
    first = operations.cancel("run-1", RunState.RUNNING)
    second = operations.cancel("run-1")

    assert first.state is RunState.CANCEL_REQUESTED
    assert second.idempotent is True
    with pytest.raises(OperationError):
        operations.cancel("run-1", RunState.SUCCEEDED)


def test_resume_calls_recovery_action_once_without_recursive_restart() -> None:
    """Given 失败运行，When resume，Then 只调用一次显式恢复 action。"""
    operations = RunOperations()
    calls: list[str] = []

    result = operations.resume("run-2", RunState.FAILED, lambda: calls.append("resume") or "ok")

    assert result.value == "ok"
    assert calls == ["resume"]
