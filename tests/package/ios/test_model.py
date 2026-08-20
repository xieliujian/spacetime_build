"""iOS 客户端导出模型测试。

本模块只验证不可变配置模型的边界行为，不执行 Unity、Xcode 或任何签名工具。
"""

import pytest

from configuration.model import SecretRef
from package.platforms.ios.model import IosExportMethod, IosExportTarget, IosPackageOptions


def test_ios_export_enums_expose_stable_protocol_values() -> None:
    """验证导出方法和签名目标使用稳定的协议字符串。"""
    assert IosExportMethod.DEVELOPMENT.value == "development"
    assert IosExportMethod.AD_HOC.value == "ad-hoc"
    assert IosExportMethod.APP_STORE.value == "app-store"
    assert IosExportMethod.ENTERPRISE.value == "enterprise"
    assert IosExportTarget.DEVELOPMENT.value == "development"
    assert IosExportTarget.DISTRIBUTION.value == "distribution"
    assert IosExportTarget.IN_HOUSE.value == "in-house"


def test_ios_options_normalize_targets_and_keep_secret_refs_by_target() -> None:
    """验证目标去重排序，并保持 profile、证书和私钥的目标映射完整。"""
    development = IosExportTarget.DEVELOPMENT
    distribution = IosExportTarget.DISTRIBUTION
    options = IosPackageOptions(
        bundle_id="com.example.game",
        configuration="Release",
        export_method=IosExportMethod.APP_STORE,
        export_targets=(distribution, development, distribution),
        team_reference="TEAM123456",
        profile_refs=(
            (distribution, SecretRef("secret://ios/profile/distribution")),
            (development, SecretRef("secret://ios/profile/development")),
        ),
        certificate_refs=(
            (distribution, SecretRef("secret://ios/certificate/distribution")),
            (development, SecretRef("secret://ios/certificate/development")),
        ),
        private_key_refs=(
            (distribution, SecretRef("secret://ios/private-key/distribution")),
            (development, SecretRef("secret://ios/private-key/development")),
        ),
        project_only=False,
    )

    assert options.export_targets == (development, distribution)
    assert options.profile_refs == (
        (development, SecretRef("secret://ios/profile/development")),
        (distribution, SecretRef("secret://ios/profile/distribution")),
    )
    assert options.certificate_refs[0][0] is development
    assert options.private_key_refs[1][0] is distribution


def test_ios_project_only_options_do_not_require_signing_material() -> None:
    """验证仅导出 Xcode 工程时可以不配置 profile、证书和私钥。"""
    options = IosPackageOptions(
        bundle_id="com.example.game",
        configuration="Debug",
        export_method=IosExportMethod.DEVELOPMENT,
        export_targets=(IosExportTarget.DEVELOPMENT,),
        team_reference="TEAM123456",
        profile_refs=(),
        certificate_refs=(),
        private_key_refs=(),
        project_only=True,
    )

    assert options.project_only is True
    assert options.profile_refs == ()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("bundle_id", "not an id"),
        ("configuration", ""),
        ("team_reference", "TEAM 123456"),
    ),
)
def test_ios_options_reject_invalid_text_fields(field: str, value: str) -> None:
    """验证 bundle ID、构建配置和团队引用拒绝非法文本。"""
    values = {
        "bundle_id": "com.example.game",
        "configuration": "Release",
        "team_reference": "TEAM123456",
    }
    values[field] = value

    with pytest.raises(ValueError):
        IosPackageOptions(
            bundle_id=values["bundle_id"],
            configuration=values["configuration"],
            export_method=IosExportMethod.APP_STORE,
            export_targets=(IosExportTarget.DISTRIBUTION,),
            team_reference=values["team_reference"],
            profile_refs=((IosExportTarget.DISTRIBUTION, SecretRef("secret://ios/profile")),),
            certificate_refs=(
                (IosExportTarget.DISTRIBUTION, SecretRef("secret://ios/certificate")),
            ),
            private_key_refs=(
                (IosExportTarget.DISTRIBUTION, SecretRef("secret://ios/private-key")),
            ),
            project_only=False,
        )


def test_ios_options_reject_incomplete_or_duplicate_target_mappings() -> None:
    """验证非 project-only 配置要求每个目标恰好一份三类秘密引用。"""
    target = IosExportTarget.DISTRIBUTION
    other_target = IosExportTarget.DEVELOPMENT
    profile = SecretRef("secret://ios/profile")
    certificate = SecretRef("secret://ios/certificate")
    private_key = SecretRef("secret://ios/private-key")

    with pytest.raises(ValueError):
        IosPackageOptions(
            "com.example.game",
            "Release",
            IosExportMethod.APP_STORE,
            (target, other_target),
            "TEAM123456",
            ((target, profile),),
            ((target, certificate), (other_target, certificate)),
            ((target, private_key), (other_target, private_key)),
            False,
        )

    with pytest.raises(ValueError):
        IosPackageOptions(
            "com.example.game",
            "Release",
            IosExportMethod.APP_STORE,
            (target,),
            "TEAM123456",
            ((target, profile), (target, profile)),
            ((target, certificate),),
            ((target, private_key),),
            False,
        )
