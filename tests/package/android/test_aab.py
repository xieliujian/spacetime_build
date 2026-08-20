"""Android AAB Gradle 构建计划测试。"""

from pathlib import Path

from ports.process import CancellationToken, ProcessRequest, ProcessResult
from package.platforms.android.aab import AndroidAppBundleBuilder
from package.platforms.android.model import (
    AndroidAbi,
    AndroidBuildType,
    AndroidOutputKind,
    AndroidPackageOptions,
)


def test_aab_builder_creates_bundle_task_and_discovers_output(tmp_path: Path) -> None:
    """验证 AAB 计划固定 bundle task 并只接受单个产物。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.AAB,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        1,
    )
    output = tmp_path / "outputs"
    plan = AndroidAppBundleBuilder.plan(tmp_path, output, options)
    assert plan.gradle_task == ":launcher:bundleRelease"
    output.mkdir()
    (output / "game.aab").write_bytes(b"aab")
    assert AndroidAppBundleBuilder.discover_output(plan) == output / "game.aab"


class _Runner:
    """执行 AAB Gradle 测试替身。"""

    def __init__(self, output: Path) -> None:
        """保存 AAB 输出目录。"""
        self.output = output
        self.arguments: tuple[str, ...] = ()

    def run(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        """生成 AAB 并返回完成结果。"""
        self.arguments = request.arguments
        from ports.process import ProcessOutcome, ProcessResult

        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "game.aab").write_bytes(b"aab")
        return ProcessResult(
            ProcessOutcome.COMPLETED,
            0,
            0,
            self.output.parent / "out.log",
            self.output.parent / "err.log",
            0,
            0,
        )


def test_aab_builder_executes_fixed_bundle_task(tmp_path: Path) -> None:
    """Given ProcessRunner，When build，Then 执行固定 bundle task 并返回 AAB。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.AAB,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        1,
    )
    output = tmp_path / "outputs"
    output.mkdir()
    runner = _Runner(output)
    plan = AndroidAppBundleBuilder.plan(tmp_path, output, options)

    artifact = AndroidAppBundleBuilder.build(plan, runner, tmp_path / "gradle")

    assert artifact == output / "game.aab"
    assert runner.arguments == (":launcher:bundleRelease",)
