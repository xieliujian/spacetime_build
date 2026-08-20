"""Android APK Gradle 构建计划测试。"""

from pathlib import Path

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
