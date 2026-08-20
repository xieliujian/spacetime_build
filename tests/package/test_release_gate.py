"""包体使用 ReleaseBundle 前置门禁测试。"""

import hashlib

import pytest

from core.errors import PublishError
from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from release.activation import VerifiedReleaseBundle, verify_release_bundle
from release.assembly import ReleaseAssemblyItem, ReleaseAssembler
from release.entries import ResourceVariant
from package.release_gate import PackageReleaseGate


def test_release_gate_requires_verified_bundle() -> None:
    """验证普通 ReleaseBundle 不能绕过远端对象验证。"""
    with pytest.raises(PublishError):
        PackageReleaseGate.require_verified("bundle-id", None)


def test_release_gate_accepts_matching_verified_bundle() -> None:
    """验证门禁只返回匹配的验证凭证，不修改 Bundle 状态。"""
    with pytest.raises(TypeError):
        VerifiedReleaseBundle("bundle-id", "x", "x")
    content = b"package-resource"
    digest = hashlib.sha256(content).hexdigest()
    artifact = LogicalArtifact(
        "scene/a.assetbundle",
        ArtifactKind.ASSET_BUNDLE,
        BlobRef("blobs/" + digest, digest, len(content)),
        (),
        frozenset(),
        ArtifactMetadata("scene", "1", "tool", ()),
    )
    bundle = ReleaseAssembler.assemble(
        ResourceVariant.MAIN,
        1,
        ("build",),
        (ReleaseAssemblyItem(artifact, "a" * 32),),
    ).bundle
    verification = verify_release_bundle(bundle, {digest: digest})
    assert PackageReleaseGate.require_verified(bundle.bundle_id, verification) is verification
