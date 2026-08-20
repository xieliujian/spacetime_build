"""客户端包体内容寻址上传测试。"""

import hashlib

import pytest

from core.artifacts import BlobRef
from package.manifest import PackageManifestFactory, PackageManifestPayload
from package.uploader import PackageUploader
from ports.storage import (
    CompareAndSwapRequest,
    CompareAndSwapResult,
    ObjectStore,
    ObjectVerification,
    PutObjectRequest,
    StoredObject,
)


class _Store(ObjectStore):
    """记录包体对象上传请求的内存存储。"""

    def __init__(self) -> None:
        """初始化空上传请求列表。"""
        self.requests: list[PutObjectRequest] = []

    def put(self, request: PutObjectRequest) -> StoredObject:
        """记录上传请求并返回一致回执。"""
        self.requests.append(request)
        return StoredObject(request.key, request.sha256, len(request.content))

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """返回成功的远端验证结果。"""
        return ObjectVerification(reference, True, reference.sha256, reference.size)

    def compare_and_swap(self, request: CompareAndSwapRequest) -> CompareAndSwapResult:
        """该测试存储不实现版本入口 CAS。"""
        raise NotImplementedError


def test_package_uploader_requires_manifest_and_uploads_by_content_key() -> None:
    """验证上传前检查 Blob 身份且对象键包含 manifest ID。"""
    content = b"apk"
    digest = hashlib.sha256(content).hexdigest()
    manifest = PackageManifestFactory.create(
        PackageManifestPayload(
            1,
            "pkg",
            "a" * 64,
            "svn:1",
            "unity",
            (("gradle", "1"),),
            "cfg",
            (("game.apk", BlobRef("blobs/" + digest, digest, 3), "apk"),),
            None,
        )
    )
    store = _Store()
    report = PackageUploader(store).upload(manifest, {"game.apk": content})
    assert report.uploaded_keys == (f"packages/{manifest.manifest_id}/game.apk",)


def test_package_uploader_rejects_wrong_content_identity() -> None:
    """验证内容摘要不匹配时不调用对象存储。"""
    content = b"apk"
    digest = hashlib.sha256(content).hexdigest()
    manifest = PackageManifestFactory.create(
        PackageManifestPayload(
            1,
            "pkg",
            "a" * 64,
            "svn:1",
            "unity",
            (("gradle", "1"),),
            "cfg",
            (("game.apk", BlobRef("blobs/" + digest, digest, 3), "apk"),),
            None,
        )
    )
    store = _Store()
    with pytest.raises(ValueError):
        PackageUploader(store).upload(manifest, {"game.apk": b"bad"})
    assert store.requests == []
