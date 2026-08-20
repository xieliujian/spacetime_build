"""验证 iOS Xcode 工程计划的安全应用边界。

测试只检查 workspace 内请求文件、固定 Ruby 入口参数和进程结果处理。Ruby/Xcode
在 Windows 环境不执行，外部进程统一由 fake ``ProcessRunner`` 替代。
"""

from pathlib import Path

import pytest

from observability.failures import ErrorCode
from package.platforms.ios.xcode_apply import (
    XcodeProjectApplyCancelled,
    XcodeProjectApplyConflict,
    XcodeProjectApplyError,
    XcodeProjectPlanApplier,
)
from package.platforms.ios.xcode_project import (
    XcodeBuildSetting,
    XcodeProjectPlan,
    XcodeProjectPlanner,
    XcodeTargetPlan,
)
from ports.process import CancellationToken, ProcessOutcome, ProcessRequest, ProcessResult


class _Runner:
    """记录 Ruby 入口请求并返回预置结果的进程替身。"""

    def __init__(self, result: ProcessResult) -> None:
        """保存结果并初始化请求记录。"""
        self.result = result
        self.requests: list[ProcessRequest] = []

    def run(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        """记录结构化 Ruby 请求并返回 fake 结果。"""
        self.requests.append(request)
        return self.result


def _plan(application_id: str = "com.example.game") -> XcodeProjectPlan:
    """创建一个最小且确定性的 Xcode 工程计划。"""
    return XcodeProjectPlanner.plan(
        "Game.xcodeproj",
        targets=(
            XcodeTargetPlan(
                "Game",
                build_settings=(XcodeBuildSetting("PRODUCT_BUNDLE_IDENTIFIER", application_id),),
            ),
        ),
    )


def _result(root: Path, *, success: bool = True, cancelled: bool = False) -> ProcessResult:
    """构造成功、失败或取消的进程结果。"""
    if success:
        return ProcessResult(
            ProcessOutcome.COMPLETED,
            0,
            0,
            root / "out.log",
            root / "err.log",
            0,
            0,
        )
    outcome = ProcessOutcome.CANCELLED if cancelled else ProcessOutcome.START_FAILED
    return ProcessResult(
        outcome,
        None,
        0,
        root / "out.log",
        root / "err.log",
        0,
        0,
        error_code=ErrorCode.PROCESS_CANCELLED if cancelled else ErrorCode.INTERNAL_ERROR,
        diagnostic_message="xcode project tool cancelled"
        if cancelled
        else "xcode project tool failed",
    )


def _applier(root: Path, runner: _Runner) -> XcodeProjectPlanApplier:
    """创建绑定固定 Ruby 文件和绝对 Ruby 可执行文件的应用器。"""
    script = root / "tools" / "apply_project.rb"
    script.parent.mkdir()
    script.write_text("# fixed xcode project tool\n", encoding="utf-8")
    ruby = root / "ruby"
    return XcodeProjectPlanApplier(runner, script, ruby_executable=ruby)


def test_xcode_applier_writes_workspace_request_and_uses_fixed_entrypoint(
    tmp_path: Path,
) -> None:
    """Given 工程计划，When 应用，Then 只写 workspace 内 JSON 并调用固定 Ruby 入口。"""
    (tmp_path / "Game.xcodeproj").mkdir()
    runner = _Runner(_result(tmp_path))
    applier = _applier(tmp_path, runner)

    result = applier.apply(_plan(), tmp_path)

    assert result.changed is True
    assert result.request_path == tmp_path / ".spacetime" / "xcode-project-request.json"
    assert result.request_path.read_text(encoding="utf-8").endswith("\n")
    assert runner.requests[0].executable == tmp_path / "ruby"
    assert runner.requests[0].working_directory == tmp_path.resolve()
    assert runner.requests[0].arguments == (
        (tmp_path / "tools" / "apply_project.rb").as_posix(),
        result.request_path.as_posix(),
    )


def test_xcode_applier_requires_explicit_ruby_executable(tmp_path: Path) -> None:
    """验证 Ruby 工具不能隐式解析为当前目录下的同名文件。"""
    script = tmp_path / "apply_project.rb"
    script.write_text("# fixed", encoding="utf-8")

    with pytest.raises(ValueError, match="ruby_executable"):
        XcodeProjectPlanApplier(_Runner(_result(tmp_path)), script)


def test_xcode_applier_is_idempotent_and_does_not_run_ruby_twice(tmp_path: Path) -> None:
    """Given 相同请求，When 重复应用，Then 第二次只返回幂等回执。"""
    (tmp_path / "Game.xcodeproj").mkdir()
    runner = _Runner(_result(tmp_path))
    applier = _applier(tmp_path, runner)

    first = applier.apply(_plan(), tmp_path)
    second = applier.apply(_plan(), tmp_path)

    assert first.changed is True
    assert second.idempotent is True
    assert second.process_result is None
    assert len(runner.requests) == 1


def test_xcode_applier_rejects_conflict_without_overwriting_existing_request(
    tmp_path: Path,
) -> None:
    """Given workspace 中已有不同请求，Then 拒绝覆盖并保留原 JSON。"""
    (tmp_path / "Game.xcodeproj").mkdir()
    runner = _Runner(_result(tmp_path))
    applier = _applier(tmp_path, runner)
    applier.apply(_plan(), tmp_path)
    request_path = tmp_path / ".spacetime" / "xcode-project-request.json"
    original = request_path.read_bytes()

    with pytest.raises(XcodeProjectApplyConflict):
        applier.apply(_plan("com.example.other"), tmp_path)

    assert request_path.read_bytes() == original
    assert len(runner.requests) == 1


def test_xcode_applier_rolls_back_request_after_tool_failure(tmp_path: Path) -> None:
    """Given 固定工具失败，Then 删除本次请求而不留下半应用状态。"""
    (tmp_path / "Game.xcodeproj").mkdir()
    runner = _Runner(_result(tmp_path, success=False))
    applier = _applier(tmp_path, runner)

    with pytest.raises(XcodeProjectApplyError):
        applier.apply(_plan(), tmp_path)

    assert not (tmp_path / ".spacetime" / "xcode-project-request.json").exists()


def test_xcode_applier_rejects_cancelled_application_and_cleans_request(tmp_path: Path) -> None:
    """Given 固定工具返回取消，Then 抛取消异常并回滚请求文件。"""
    (tmp_path / "Game.xcodeproj").mkdir()
    runner = _Runner(_result(tmp_path, success=False, cancelled=True))
    applier = _applier(tmp_path, runner)

    with pytest.raises(XcodeProjectApplyCancelled):
        applier.apply(_plan(), tmp_path)

    assert not (tmp_path / ".spacetime" / "xcode-project-request.json").exists()


def test_xcode_applier_does_not_start_ruby_when_already_cancelled(tmp_path: Path) -> None:
    """Given 预先取消的令牌，Then 不写请求也不启动 Ruby。"""
    (tmp_path / "Game.xcodeproj").mkdir()
    runner = _Runner(_result(tmp_path))
    applier = _applier(tmp_path, runner)
    cancellation = CancellationToken()
    cancellation.cancel()

    with pytest.raises(XcodeProjectApplyCancelled):
        applier.apply(_plan(), tmp_path, cancellation=cancellation)

    assert runner.requests == []
    assert not (tmp_path / ".spacetime").exists()


def test_xcode_applier_rejects_workspace_escape_and_invalid_workspace(tmp_path: Path) -> None:
    """验证 workspace 必须是绝对已存在目录，且计划路径不能逃逸。"""
    runner = _Runner(_result(tmp_path))
    applier = _applier(tmp_path, runner)

    with pytest.raises(XcodeProjectApplyError):
        applier.apply(_plan(), tmp_path / "missing")

    escaped = object.__new__(XcodeProjectPlan)
    object.__setattr__(escaped, "project_path", "../outside.xcodeproj")
    object.__setattr__(escaped, "targets", _plan().targets)
    with pytest.raises(XcodeProjectApplyError):
        applier.apply(escaped, tmp_path)


def test_ruby_entrypoint_contains_fixed_json_whitelist() -> None:
    """验证固定 Ruby 入口只允许结构化 JSON 字段，不提供任意脚本执行入口。"""
    script = Path(__file__).parents[3] / "tools" / "xcode" / "apply_project.rb"

    source = script.read_text(encoding="utf-8")

    assert "JSON.parse" in source
    assert "apply_xcode_project_plan" in source
    assert "eval(" not in source
    assert "instance_eval" not in source
    assert "system(" not in source
