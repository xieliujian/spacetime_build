"""验证 Android Gradle 配置计划的 workspace 原子应用。"""

from pathlib import Path

import pytest

from observability.failures import ErrorCode
from package.platforms.android.gradle_apply import (
    GradleApplyConflict,
    GradleApplyError,
    GradleConfigurationApplier,
)
from package.platforms.android.gradle_config import (
    GradleConfigurationPlan,
    GradleConfigurationPlanner,
)
from package.platforms.android.model import (
    AndroidAbi,
    AndroidBuildType,
    AndroidOutputKind,
    AndroidPackageOptions,
)
from ports.process import ProcessOutcome, ProcessRequest, ProcessResult


class _Runner:
    """记录固定 Gradle script 请求的进程替身。"""

    def __init__(self, result: ProcessResult) -> None:
        """保存固定执行结果和调用列表。"""
        self.result = result
        self.requests: list[ProcessRequest] = []

    def run(self, request: ProcessRequest, cancellation: object = None) -> ProcessResult:
        """记录请求并返回预置结果。"""
        self.requests.append(request)
        return self.result


def _plan() -> GradleConfigurationPlan:
    """创建测试用 Gradle 配置计划。"""
    return GradleConfigurationPlanner.plan(
        AndroidPackageOptions(
            AndroidOutputKind.APK,
            (AndroidAbi.ARM64_V8A,),
            AndroidBuildType.RELEASE,
            "com.example.game",
            7,
        ),
        repositories=("https://repo.example.com",),
    )


def _result(root: Path, *, success: bool = True) -> ProcessResult:
    """创建完成或启动失败的 ProcessResult。"""
    if success:
        return ProcessResult(
            ProcessOutcome.COMPLETED, 0, 0, root / "out.log", root / "err.log", 0, 0
        )
    return ProcessResult(
        ProcessOutcome.START_FAILED,
        None,
        0,
        root / "out.log",
        root / "err.log",
        0,
        0,
        error_code=ErrorCode.INTERNAL_ERROR,
        diagnostic_message="gradle failed",
    )


def test_gradle_applier_writes_deterministic_request_and_is_idempotent(tmp_path: Path) -> None:
    """Given 同一计划，When 重复应用，Then 第二次不启动 Gradle。"""
    runner = _Runner(_result(tmp_path))
    script = tmp_path / "build_config.gradle"
    script.write_text("// fixed", encoding="utf-8")
    applier = GradleConfigurationApplier(runner, script, gradle_executable=tmp_path / "gradle")

    first = applier.apply(_plan(), tmp_path)
    second = applier.apply(_plan(), tmp_path)

    assert first.changed is True
    assert second.idempotent is True
    assert len(runner.requests) == 1
    assert first.request_path.read_text(encoding="utf-8").endswith("\n")


def test_gradle_applier_requires_explicit_gradle_executable(tmp_path: Path) -> None:
    """验证 Gradle 工具不能隐式解析为当前目录下的同名文件。"""
    script = tmp_path / "build_config.gradle"
    script.write_text("// fixed", encoding="utf-8")

    with pytest.raises(ValueError, match="gradle_executable"):
        GradleConfigurationApplier(_Runner(_result(tmp_path)), script)


def test_gradle_applier_rejects_conflicting_request_and_rolls_back_failure(tmp_path: Path) -> None:
    """Given workspace 中已有不同计划或进程失败，Then 不留下半应用配置。"""
    success_runner = _Runner(_result(tmp_path))
    script = tmp_path / "build_config.gradle"
    script.write_text("// fixed", encoding="utf-8")
    applier = GradleConfigurationApplier(
        success_runner,
        script,
        gradle_executable=tmp_path / "gradle",
    )
    applier.apply(_plan(), tmp_path)

    other_plan = GradleConfigurationPlanner.plan(
        AndroidPackageOptions(
            AndroidOutputKind.AAB,
            (AndroidAbi.ARM64_V8A,),
            AndroidBuildType.RELEASE,
            "com.example.game",
            8,
        )
    )
    with pytest.raises(GradleApplyConflict):
        applier.apply(other_plan, tmp_path)

    failed_root = tmp_path / "failed"
    failed_root.mkdir()
    failed_runner = _Runner(_result(failed_root, success=False))
    failed_applier = GradleConfigurationApplier(
        failed_runner,
        script,
        gradle_executable=tmp_path / "gradle",
    )
    with pytest.raises(GradleApplyError):
        failed_applier.apply(_plan(), failed_root)
    assert not (failed_root / ".spacetime" / "gradle-config-request.json").exists()
