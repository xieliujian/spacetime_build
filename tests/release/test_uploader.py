"""ReleaseObjectUploader 幂等、失败和取消测试。"""

import hashlib

import pytest

from core.artifacts import BlobRef
from ports.storage import ObjectStore, PutObjectRequest, StoredObject
from release.upload_plan import UploadItem, UploadPhase, UploadPlan, UploadPlanFactory
from release.uploader import ReleaseObjectUploader, UploadCancelled, UploadTransientError


class _Store(ObjectStore):
    """可注入失败的对象存储替身。"""

    def __init__(self, failures: int = 0) -> None:
        """保存需要注入的临时失败次数。"""
        self.failures = failures
        self.keys: list[str] = []

    def put(self, request: PutObjectRequest) -> StoredObject:
        """记录写入并按配置注入临时失败。"""
        if self.failures:
            self.failures -= 1
            raise UploadTransientError("temporary")
        self.keys.append(request.key)
        return StoredObject(request.key, request.sha256, len(request.content))


def _plan() -> UploadPlan:
    """构造单对象上传计划。"""
    content = b"object"
    digest = hashlib.sha256(content).hexdigest()
    item = UploadItem(
        "objects/a", BlobRef(f"blobs/{digest}", digest, len(content)), content, UploadPhase.RESOURCE
    )
    version_content = b"v"
    version_sha = hashlib.sha256(version_content).hexdigest()
    version = UploadItem(
        "version/current",
        BlobRef("blobs/" + version_sha, version_sha, 1),
        version_content,
        UploadPhase.VERSION_ENTRY,
    )
    return UploadPlanFactory.create("a" * 64, (item,), (), version, 0)


def test_uploader_retries_only_transient_failures_and_skips_version_entry() -> None:
    """验证临时失败可控重试，版本入口留给 CAS 激活。"""
    store = _Store(failures=1)
    report = ReleaseObjectUploader(store, max_retries=2).upload(_plan())
    assert report.uploaded_keys == ("objects/a",)
    assert report.attempts == 2
    assert store.keys == ["objects/a"]


def test_uploader_stops_before_new_object_when_cancelled() -> None:
    """验证取消不会上传版本入口或后续对象。"""

    class _Cancel:
        """已取消 token。"""

        is_cancelled = True

    with pytest.raises(UploadCancelled):
        ReleaseObjectUploader(_Store()).upload(_plan(), cancellation=_Cancel())
