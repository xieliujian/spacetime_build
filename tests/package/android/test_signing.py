"""Android 签名计划测试。"""

import pytest

from configuration.model import SecretRef
from package.platforms.android.model import (
    AndroidAbi,
    AndroidBuildType,
    AndroidOutputKind,
    AndroidPackageOptions,
)
from package.platforms.android.signing import AndroidSigningPlanner


def test_android_signing_plan_separates_secret_ref_and_certificate_fingerprint() -> None:
    """验证 APK 使用 apksigner，计划只保存 SecretRef 和 SHA256 指纹。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.APK,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        1,
    )
    plan = AndroidSigningPlanner.plan(options, SecretRef("secret://android/keystore"), "a" * 64)
    assert plan.tool == "apksigner"
    assert plan.secret_ref.value == "secret://android/keystore"
    assert "password" not in repr(plan).lower()


def test_android_signing_plan_rejects_md5_sha1_and_project_output() -> None:
    """验证弱指纹和 project-only 输出不能进入签名阶段。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.PROJECT,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        1,
    )
    with pytest.raises(ValueError):
        AndroidSigningPlanner.plan(options, SecretRef("secret://android/keystore"), "a" * 32)
