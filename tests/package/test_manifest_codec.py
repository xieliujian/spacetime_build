"""PackageManifest 本地 codec 测试。"""

from pathlib import Path

import pytest

from core.artifacts import BlobRef
from package.manifest import (
    PackageManifestFactory,
    PackageManifestPayload,
    read_package_manifest,
    write_package_manifest,
)


def _payload() -> PackageManifestPayload:
    """构造固定的 PackageManifest payload。"""
    return PackageManifestPayload(
        1,
        "pkg-identity",
        "a" * 64,
        "svn:123",
        "2022.3.62f2",
        (("gradle", "8.5"),),
        "config-digest",
        (("game.apk", BlobRef("blobs/" + "a" * 64, "a" * 64, 20), "apk"),),
        "certificate",
    )


def test_package_manifest_codec_round_trips_and_rejects_stale_id(tmp_path: Path) -> None:
    """验证本地 JSON round trip 和陈旧 ID 拒绝。"""
    path = tmp_path / "manifest.json"
    write_package_manifest(PackageManifestFactory.create(_payload()), path)
    assert (
        read_package_manifest(path).manifest_id
        == PackageManifestFactory.create(_payload()).manifest_id
    )
    text = path.read_text(encoding="utf-8").replace("config-digest", "other-config")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_package_manifest(path)
