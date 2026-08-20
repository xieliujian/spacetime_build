"""CAS 激活器的验证凭证、冲突和幂等测试。"""

import hashlib
from typing import cast

import pytest

from core.artifacts import BlobRef
from core.errors import PublishError
from ports.storage import (
    CompareAndSwapRequest,
    CompareAndSwapResult,
    ObjectStore,
    ObjectVerification,
    PutObjectRequest,
    StoredObject,
)
from release.activator import ReleaseActivator
from release.bundle_codec import ReleaseBundleFactory
from release.bundles import RELEASE_BUNDLE_SCHEMA_VERSION, ReleaseBundlePayload
from release.bundles import ReleaseBundle
from release.entries import ReleaseEntry, ReleaseObjectOrigin, ResourceVariant
from release.manifest_codec import ReleaseManifestFactory
from release.manifests import RELEASE_MANIFEST_SCHEMA_VERSION, ReleaseManifestPayload
from release.remote_verifier import RemoteReleaseVerifier
from release.activation import VerifiedReleaseBundle
from release.snapshots import (
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)
from release.upload_plan import UploadItem, UploadPhase, UploadPlanFactory
from release.upload_plan import UploadPlan


class _Store(ObjectStore):
    """实现上传、验证和 CAS 的内存替身。"""

    def __init__(self) -> None:
        """初始化对象记录和入口代际。"""
        self.objects: dict[str, tuple[str, int]] = {}
        self.generation = 0
        self.entry_digest: str | None = None

    def put(self, request: PutObjectRequest) -> StoredObject:
        """记录不可变对象。"""
        self.objects[request.key] = (request.sha256, len(request.content))
        return StoredObject(request.key, request.sha256, len(request.content))

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """返回对象校验结果。"""
        value = self.objects.get(reference.key)
        if value is None:
            return ObjectVerification(reference, False, None, None)
        return ObjectVerification(reference, True, value[0], value[1])

    def compare_and_swap(self, request: CompareAndSwapRequest) -> CompareAndSwapResult:
        """按代际执行内存 CAS。"""
        if request.expected_generation != self.generation:
            return CompareAndSwapResult(False, self.generation, self.entry_digest)
        self.entry_digest = hashlib.sha256(request.content).hexdigest()
        self.generation += 1
        return CompareAndSwapResult(True, self.generation, self.entry_digest)


def _bundle_and_plan(store: _Store) -> tuple[ReleaseBundle, UploadPlan]:
    """构造一个可验证的主清 Bundle 和上传计划。"""
    content = b"asset-bundle"
    transfer_sha = hashlib.sha256(content).hexdigest()
    source_sha = "a" * 64
    transfer_blob = BlobRef("blobs/" + transfer_sha, transfer_sha, len(content))
    source_blob = BlobRef("blobs/" + source_sha, source_sha, 1)
    entry = ReleaseEntry(
        "scene/a.ab",
        ResourceVariant.MAIN,
        source_blob,
        "1" * 32,
        1,
        transfer_blob,
        len(content),
        123,
        "123",
        "cdn/scene/a.ab",
        0,
        ReleaseObjectOrigin.CURRENT_UPLOAD,
    )
    snapshot_entry = ReleaseSnapshotEntry(
        entry,
        ReleaseArtifactClass.ASSET_BUNDLE,
        frozenset({ReleaseMembership.FILE_LIST, ReleaseMembership.ASSET_BUNDLE_DATABASE}),
        (),
        None,
    )
    snapshot = ReleaseSnapshot.create(ResourceVariant.MAIN, (snapshot_entry,))
    manifest = ReleaseManifestFactory.create(
        ReleaseManifestPayload(
            RELEASE_MANIFEST_SCHEMA_VERSION, ResourceVariant.MAIN, 123, snapshot, ("build",)
        )
    )
    bundle = ReleaseBundleFactory.create(
        ReleaseBundlePayload(RELEASE_BUNDLE_SCHEMA_VERSION, (manifest,), None)
    )
    version_content = b"version-entry"
    version_sha = hashlib.sha256(version_content).hexdigest()
    resource = UploadItem("cdn/scene/a.ab", transfer_blob, content, UploadPhase.RESOURCE)
    version = UploadItem(
        "version/current",
        BlobRef("blobs/" + version_sha, version_sha, len(version_content)),
        version_content,
        UploadPhase.VERSION_ENTRY,
    )
    plan = UploadPlanFactory.create(bundle.bundle_id, (resource,), (), version, 0)
    store.put(PutObjectRequest(resource.key, resource.content, resource.blob.sha256))
    store.put(PutObjectRequest(version.key, version.content, version.blob.sha256))
    return bundle, plan


def test_activator_requires_verified_bundle_and_supports_idempotent_result() -> None:
    """验证只有远端验证通过才能 CAS，重复相同入口视为幂等成功。"""
    store = _Store()
    bundle, plan = _bundle_and_plan(store)
    verified = RemoteReleaseVerifier(store).verify(bundle, plan)
    activator = ReleaseActivator(store)
    first = activator.activate(plan, verified)
    assert first.applied is True
    second = activator.activate(plan, verified)
    assert second.idempotent is True


def test_activator_rejects_fake_verification_and_generation_conflict() -> None:
    """验证普通对象和陈旧代际都不能绕过激活门禁。"""
    store = _Store()
    bundle, plan = _bundle_and_plan(store)
    with pytest.raises(PublishError):
        ReleaseActivator(store).activate(plan, cast(VerifiedReleaseBundle, object()))
    verified = RemoteReleaseVerifier(store).verify(bundle, plan)
    store.generation = 4
    with pytest.raises(PublishError):
        ReleaseActivator(store).activate(plan, verified)
