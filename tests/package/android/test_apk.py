"""Android APK Gradle 构建计划测试。"""

from pathlib import Path

import pytest

from observability.failures import ErrorCode
from core.errors import ToolExecutionError
from ports.process import CancellationToken, ProcessRequest, ProcessResult
from package.platforms.android.apk import AndroidApkBuilder
from package.platforms.android.model import (
    AndroidAbi,
    AndroidBuildType,
    AndroidOutputKind,
    AndroidPackageOptions,
)


def test_apk_builder_creates_release_task_and_discovers_single_output(tmp_path: Path) -> None:
    """验证 APK 计划和重复输出拒绝边界。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.APK,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        1,
    )
    output = tmp_path / "outputs"
    plan = AndroidApkBuilder.plan(tmp_path, output, options)
    assert plan.gradle_task == ":launcher:assembleRelease"
    output.mkdir()
    (output / "game.apk").write_bytes(b"apk")
    assert AndroidApkBuilder.discover_output(plan) == output / "game.apk"


class _Runner:
    """执行 Gradle 测试替身并生成 APK。"""

    def __init__(self, output: Path, success: bool = True) -> None:
        """保存输出目录和结果模式。"""
        self.output = output
        self.success = success
        self.arguments: tuple[str, ...] = ()

    def run(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        """记录参数并返回最小 ProcessResult。"""
        self.arguments = request.arguments
        from ports.process import ProcessOutcome, ProcessResult

        if self.success:
            self.output.mkdir(parents=True, exist_ok=True)
            (self.output / "game.apk").write_bytes(b"apk")
            return ProcessResult(
                ProcessOutcome.COMPLETED,
                0,
                0,
                self.output.parent / "out.log",
                self.output.parent / "err.log",
                0,
                0,
            )
        return ProcessResult(
            ProcessOutcome.START_FAILED,
            None,
            0,
            self.output.parent / "out.log",
            self.output.parent / "err.log",
            0,
            0,
            error_code=ErrorCode.INTERNAL_ERROR,
            diagnostic_message="gradle failed",
        )


def test_apk_builder_executes_fixed_gradle_task_and_discovers_output(tmp_path: Path) -> None:
    """Given ProcessRunner，When build，Then 只执行固定 APK task 并返回产物。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.APK,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        1,
    )
    output = tmp_path / "outputs"
    output.mkdir()
    runner = _Runner(output)
    plan = AndroidApkBuilder.plan(tmp_path, output, options)

    artifact = AndroidApkBuilder.build(plan, runner, tmp_path / "gradle")

    assert artifact == output / "game.apk"
    assert runner.arguments == (":launcher:assembleRelease",)


def test_apk_builder_stops_on_gradle_failure(tmp_path: Path) -> None:
    """Given Gradle 非零结果，When build，Then 抛工具错误且不发现伪输出。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.APK,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        1,
    )
    output = tmp_path / "outputs"
    output.mkdir()
    plan = AndroidApkBuilder.plan(tmp_path, output, options)
    with pytest.raises(ToolExecutionError):
        AndroidApkBuilder.build(plan, _Runner(output, False), tmp_path / "gradle")
