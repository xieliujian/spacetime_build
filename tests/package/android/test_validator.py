"""Android APK/AAB ZIP 包体安全验证测试。"""

import zipfile
from pathlib import Path

import pytest

from package.platforms.android.model import (
    AndroidAbi,
    AndroidBuildType,
    AndroidOutputKind,
    AndroidPackageOptions,
)
from package.platforms.android.validator import AndroidPackageValidator


def test_android_validator_checks_extension_abi_and_zip_paths(tmp_path: Path) -> None:
    """验证包扩展名、ABI native 库和路径逃逸。"""
    path = tmp_path / "game.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("lib/arm64-v8a/libil2cpp.so", b"so")
    options = AndroidPackageOptions(
        AndroidOutputKind.APK,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        1,
    )
    report = AndroidPackageValidator.validate(path, options)
    assert report.is_valid is True
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("../escape", b"bad")
    with pytest.raises(ValueError):
        AndroidPackageValidator.validate(path, options)
