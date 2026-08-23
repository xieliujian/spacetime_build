"""验证分支计划的受控 SVN 写入适配器。"""

from __future__ import annotations

from pathlib import Path

from branch.model import BranchCopy, BranchSource, BranchStatus, BranchTarget, PropertyChange
from branch.planner import BranchPlan
from branch.apply import SvnBranchMutationApplier
from ports.process import ProcessOutcome, ProcessRequest, ProcessResult


class _Runner:
    """保存进程请求并返回预置的进程结果。"""

    def __init__(self, result: ProcessResult) -> None:
        """保存测试用进程结果。"""
        self.result = result
        self.requests: list[ProcessRequest] = []

    def run(self, request: ProcessRequest, cancellation: object = None) -> ProcessResult:
        """记录请求并返回预置结果。"""
        self.requests.append(request)
        return self.result


def _result(tmp_path: Path, *, exit_code: int = 0) -> ProcessResult:
    """创建测试用完成结果。"""
    return ProcessResult(
        outcome=ProcessOutcome.COMPLETED,
        exit_code=exit_code,
        duration_seconds=0.01,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
        stdout_bytes=0,
        stderr_bytes=0,
    )


def _plan() -> BranchPlan:
    """创建包含 copy 和属性写入的固定计划。"""
    source = BranchSource("https://svn.example/repo/trunk", "repo-1", 42)
    target = BranchTarget("https://svn.example/repo/branches/release", "repo-1")
    return BranchPlan(
        plan_id="plan-1",
        source_revision=42,
        expected_repository_revision=99,
        source_root=source.url,
        target_root=target.url,
        copies=(BranchCopy(source, target, "client", "client"),),
        property_changes=(
            PropertyChange("client", "svn:externals", "sha256:old", "https://secret.example/path"),
            PropertyChange("tools", "svn:ignore", None, None),
        ),
        repository_uuid="repo-1",
    )


def test_apply_builds_deterministic_svnmucc_request_and_transitions_result(tmp_path: Path) -> None:
    """成功执行时应提交固定 revision、属性动作和脱敏索引。"""
    runner = _Runner(_result(tmp_path))
    applier = SvnBranchMutationApplier(
        executable=tmp_path / "svnmucc.exe",
        process_runner=runner,
        working_directory=tmp_path,
        log_root=tmp_path / "logs",
    )

    applied = applier.apply(_plan(), message="release branch")

    assert applied.result.status is BranchStatus.APPLIED
    assert applied.process_result.exit_code == 0
    assert applied.command_digest
    request = runner.requests[0]
    assert request.arguments == (
        "--non-interactive",
        "--no-auth-cache",
        "-r",
        "99",
        "cp",
        "42",
        "https://svn.example/repo/trunk/client",
        "https://svn.example/repo/branches/release/client",
        "propset",
        "svn:externals",
        "https://secret.example/path",
        "https://svn.example/repo/branches/release/client",
        "propdel",
        "svn:ignore",
        "https://svn.example/repo/branches/release/tools",
        "-m",
        "release branch",
    )
    assert request.redacted_argument_indexes == frozenset({10})
    assert request.stdout_path.parent == tmp_path / "logs"


def test_apply_nonzero_exit_is_a_terminal_failed_branch_result(tmp_path: Path) -> None:
    """SVN 返回非零退出码时不得误报成功。"""
    runner = _Runner(_result(tmp_path, exit_code=1))
    applier = SvnBranchMutationApplier(
        executable=tmp_path / "svnmucc.exe",
        process_runner=runner,
        working_directory=tmp_path,
        log_root=tmp_path / "logs",
    )

    applied = applier.apply(_plan(), message="release branch")

    assert applied.result.status is BranchStatus.FAILED
    assert "exit code 1" in applied.result.message


def test_apply_rejects_empty_or_controlled_commit_message(tmp_path: Path) -> None:
    """提交消息必须可审计且不能制造空提交或日志注入。"""
    runner = _Runner(_result(tmp_path))
    applier = SvnBranchMutationApplier(
        executable=tmp_path / "svnmucc.exe",
        process_runner=runner,
        working_directory=tmp_path,
        log_root=tmp_path / "logs",
    )

    for message in ("", "  ", "release\nbranch"):
        try:
            applier.apply(_plan(), message=message)
        except ValueError:
            continue
        raise AssertionError(f"消息未被拒绝: {message!r}")

    assert runner.requests == []
