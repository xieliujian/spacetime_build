"""验证 iOS provisioning profile 元数据的纯 bytes 解析和脱敏边界。"""

from datetime import datetime, timezone
import plistlib

import pytest

from configuration.model import SecretRef
from package.platforms.ios.provisioning import ProfileMetadata, ProvisioningProfileReader


def _profile_bytes() -> bytes:
    """构造不包含真实秘密的 provisioning profile plist fixture。"""
    return plistlib.dumps(
        {
            "UUID": "12345678-1234-4ABC-8DEF-1234567890AB",
            "TeamIdentifier": ["TEAM123456"],
            "Entitlements": {
                "application-identifier": "TEAM123456.com.example.game",
                "com.apple.developer.team-identifier": "TEAM123456",
                "keychain-access-groups": ["TEAM123456.*"],
            },
            "ExpirationDate": datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def test_parse_bytes_returns_profile_metadata_without_resolving_secret() -> None:
    """Given plist bytes，When 解析，Then 返回完整公开元数据并保留 SecretRef。"""
    profile_ref = SecretRef("secret://ios/profiles/distribution")

    metadata = ProvisioningProfileReader.parse_bytes(profile_ref, _profile_bytes())

    assert isinstance(metadata, ProfileMetadata)
    assert metadata.profile_ref == profile_ref
    assert metadata.uuid == "12345678-1234-4ABC-8DEF-1234567890AB"
    assert metadata.team == "TEAM123456"
    assert metadata.application_identifier == "TEAM123456.com.example.game"
    assert dict(metadata.entitlements)["application-identifier"] == ("TEAM123456.com.example.game")
    assert dict(metadata.entitlements)["keychain-access-groups"] == ("TEAM123456.*",)
    assert metadata.expiration_date == datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_profile_metadata_is_immutable_and_has_compatibility_aliases() -> None:
    """验证元数据冻结、嵌套 entitlement 冻结以及公开别名的一致性。"""
    metadata = ProvisioningProfileReader.parse_bytes(
        SecretRef("secret://ios/profiles/development"), _profile_bytes()
    )

    with pytest.raises(AttributeError):
        metadata.uuid = "different"  # type: ignore[misc]

    assert metadata.team_identifier == metadata.team
    assert metadata.expires_at == metadata.expiration_date
    assert metadata.source == metadata.profile_ref
    assert isinstance(dict(metadata.entitlements)["keychain-access-groups"], tuple)


def test_profile_metadata_redaction_never_contains_secret_locator() -> None:
    """验证 repr 和脱敏摘要不会泄漏 provisioning profile 的 SecretRef locator。"""
    secret_locator = "secret://ios/profiles/private-distribution"
    metadata = ProvisioningProfileReader.parse_bytes(SecretRef(secret_locator), _profile_bytes())

    redacted = metadata.to_redacted_dict()

    assert secret_locator not in repr(metadata)
    assert secret_locator not in repr(redacted)
    assert redacted["profile_ref"] == "SecretRef(<redacted>)"
    assert redacted["uuid"] == metadata.uuid


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not a plist", "无法解析"),
        (
            plistlib.dumps({"UUID": "12345678-1234-4ABC-8DEF-1234567890AB"}),
            "Entitlements",
        ),
    ],
)
def test_parse_bytes_rejects_invalid_or_incomplete_profiles(payload: bytes, message: str) -> None:
    """验证损坏 profile 和缺少必需字段时不会生成部分元数据。"""
    with pytest.raises(ValueError, match=message):
        ProvisioningProfileReader.parse_bytes(SecretRef("secret://ios/profiles/test"), payload)


def test_parse_bytes_rejects_invalid_profile_reference_and_uuid() -> None:
    """验证 SecretRef 和 UUID 在纯解析边界被严格校验。"""
    invalid_uuid = plistlib.dumps(
        {
            "UUID": "not-a-uuid",
            "TeamIdentifier": ["TEAM123456"],
            "Entitlements": {
                "application-identifier": "TEAM123456.com.example.game",
            },
            "ExpirationDate": datetime.now(timezone.utc),
        }
    )

    with pytest.raises(TypeError):
        ProvisioningProfileReader.parse_bytes(object(), invalid_uuid)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="UUID"):
        ProvisioningProfileReader.parse_bytes(SecretRef("secret://ios/profiles/test"), invalid_uuid)
