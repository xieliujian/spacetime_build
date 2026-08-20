"""PackageManifest 确定性身份测试。"""

import pytest

from core.artifacts import BlobRef
from package.manifest import PackageManifest, PackageManifestFactory, PackageManifestPayload


def test_package_manifest_factory_is_deterministic_and_excludes_runtime_fields() -> None:
    """验证相同 payload 得到相同 ID，运行状态和上传 URL不进入身份。"""
    artifact = ("game.apk", BlobRef("blobs/" + "a" * 64, "a" * 64, 20), "apk")
    payload = PackageManifestPayload(
        1,
        "pkg-identity",
        "a" * 64,
        "svn:123",
        "2022.3.62f2",
        (("gradle", "8.5"), ("jdk", "17")),
        "config-digest",
        (artifact,),
        "cert-fingerprint",
    )
    first = PackageManifestFactory.create(payload)
    second = PackageManifestFactory.create(payload)
    assert first.manifest_id == second.manifest_id
    assert "https" not in repr(first)


def test_package_manifest_rejects_stale_id_and_secret_like_artifact_metadata() -> None:
    """验证 manifest 不能通过外部 ID 或秘密字符串构造。"""
    with pytest.raises(TypeError):
        PackageManifestFactory.bind("f" * 64, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PackageManifest("f" * 64, None)  # type: ignore[arg-type]
