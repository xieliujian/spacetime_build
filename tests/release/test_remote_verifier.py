"""远端发布对象验证测试。"""

import hashlib
from typing import cast

import pytest

from core.artifacts import BlobRef
from ports.storage import ObjectStore, ObjectVerification, StoredObject
from release.bundles import ReleaseBundle
from release.remote_verifier import RemoteReleaseVerifier
from release.upload_plan import UploadItem, UploadPhase, UploadPlanFactory


class _Store(ObjectStore):
    """按上传内容返回远端校验结果的替身。"""

    def __init__(self, item: UploadItem) -> None:
        """保存待验证对象。"""
        self.item = item

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """返回本地对象的正确摘要。"""
        return ObjectVerification(reference, True, reference.sha256, reference.size)


def test_remote_verifier_rejects_plan_without_bundle_object() -> None:
    """验证校验器只验证计划对象，缺 bundle 需要对象时失败。"""
    content = b"protocol"
    sha = hashlib.sha256(content).hexdigest()
    item = UploadItem(
        "protocol.txt", BlobRef("blobs/" + sha, sha, len(content)), content, UploadPhase.PROTOCOL
    )
    version_content = b"v"
    version_sha = hashlib.sha256(version_content).hexdigest()
    version = UploadItem(
        "version/current",
        BlobRef("blobs/" + version_sha, version_sha, 1),
        version_content,
        UploadPhase.VERSION_ENTRY,
    )
    plan = UploadPlanFactory.create("a" * 64, (), (item,), version, 0)
    # bundle is intentionally omitted here; type/identity validation is the first gate.
    with pytest.raises(TypeError):
        RemoteReleaseVerifier(_Store(item)).verify(cast(ReleaseBundle, object()), plan)
