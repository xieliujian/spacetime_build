"""Android AAB Gradle 构建计划测试。"""

from pathlib import Path

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
