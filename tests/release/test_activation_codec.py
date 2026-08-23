"""验证已验证 Bundle 凭证和激活记录的本地 codec。"""

from __future__ import annotations

import json

import pytest

from release.activation import (
    RELEASE_ACTIVATION_SCHEMA_VERSION,
    ReleaseActivationRecord,
    ReleaseActivationStatus,
    verify_release_bundle,
)
from release.activation_codec import (
    read_release_activation_record,
    read_verified_release_bundle,
    write_release_activation_record,
    write_verified_release_bundle,
)
from release.bundle_codec import ReleaseBundleFactory
from release.bundles import RELEASE_BUNDLE_SCHEMA_VERSION, ReleaseBundlePayload
from release.entries import ResourceVariant
from release.manifest_codec import ReleaseManifestFactory
from release.manifests import RELEASE_MANIFEST_SCHEMA_VERSION, ReleaseManifestPayload
from release.snapshots import ReleaseSnapshot


def _bundle():
    """构造无当前上传对象的最小合法 Bundle。"""
    snapshot = ReleaseSnapshot.create(ResourceVariant.MAIN, ())
    manifest = ReleaseManifestFactory.create(
        ReleaseManifestPayload(
            RELEASE_MANIFEST_SCHEMA_VERSION,
            ResourceVariant.MAIN,
            1,
            snapshot,
            (),
        )
    )
    return ReleaseBundleFactory.create(
        ReleaseBundlePayload(RELEASE_BUNDLE_SCHEMA_VERSION, (manifest,), None)
    )


def test_verified_release_bundle_codec_round_trips(tmp_path) -> None:
    """验证凭证只能从 codec 的严格字段恢复。"""
    bundle = _bundle()
    verified = verify_release_bundle(bundle, {})
    path = tmp_path / "verified.json"

    write_verified_release_bundle(verified, path)

    assert read_verified_release_bundle(path) == verified


def test_activation_record_codec_rejects_unknown_schema(tmp_path) -> None:
    """验证激活记录 schema 版本不允许静默兼容。"""
    record = ReleaseActivationRecord(
        RELEASE_ACTIVATION_SCHEMA_VERSION,
        "activation-1",
        "a" * 64,
        "target",
        0,
        ReleaseActivationStatus.PREPARING,
        "b" * 64,
        None,
        None,
    )
    path = tmp_path / "activation.json"
    write_release_activation_record(record, path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 99
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError):
        read_release_activation_record(path)
