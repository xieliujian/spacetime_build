"""Android 包体选项模型测试。"""

import pytest

from package.platforms.android.model import (
    AndroidAbi,
    AndroidBuildType,
    AndroidOutputKind,
    AndroidPackageOptions,
)


def test_android_options_normalize_abis_and_validate_build_mode() -> None:
    """验证 ABI 去重排序和 APK/AAB 构建模式。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.APK,
        (AndroidAbi.X86_64, AndroidAbi.ARM64_V8A, AndroidAbi.ARM64_V8A),
        AndroidBuildType.RELEASE,
        "com.example.game",
        12,
    )
    assert options.abis == (AndroidAbi.ARM64_V8A, AndroidAbi.X86_64)


def test_android_options_reject_empty_abis_and_bad_version() -> None:
    """验证 Android ABI 集合和版本号必须满足客户端约束。"""
    with pytest.raises(ValueError):
        AndroidPackageOptions(
            AndroidOutputKind.AAB, (), AndroidBuildType.RELEASE, "com.example.game", 1
        )
    with pytest.raises(ValueError):
        AndroidPackageOptions(
            AndroidOutputKind.APK, (AndroidAbi.ARM64_V8A,), AndroidBuildType.RELEASE, "bad id", 0
        )
