"""Redirect 容器字节构建测试。"""

import hashlib

from core.artifacts import BlobRef
import pytest

from core.errors import PublishError
from ports.storage import (
    CompareAndSwapRequest,
    CompareAndSwapResult,
    ObjectStore,
    ObjectVerification,
    PutObjectRequest,
    StoredObject,
)
from release.redirect import RedirectContainerPlan, RedirectSlicePlan
from release.redirect_container import RedirectContainerBuilder


class _Store(ObjectStore):
    """记录提交内容的内存对象存储。"""

    def __init__(self) -> None:
        """初始化空的对象请求记录。"""
        self.requests: list[PutObjectRequest] = []

    def put(self, request: PutObjectRequest) -> StoredObject:
        """保存请求并返回一致的对象回执。"""
        self.requests.append(request)
        return StoredObject(request.key, request.sha256, len(request.content))

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """返回成功的对象校验结果。"""
        return ObjectVerification(reference, True, reference.sha256, reference.size)

    def compare_and_swap(self, request: CompareAndSwapRequest) -> CompareAndSwapResult:
        """该测试存储不支持版本入口 CAS。"""
        raise NotImplementedError


def test_redirect_container_builder_uses_plan_order_and_submits_blob() -> None:
    """验证容器内容、Blob 身份和对象提交保持一致。"""
    first = b"first"
    second = b"second"
    first_sha = hashlib.sha256(first).hexdigest()
    second_sha = hashlib.sha256(second).hexdigest()
    plan = RedirectContainerPlan(
        "scene/redirect/0.assetbundle",
        (
            RedirectSlicePlan(
                "scene/z.assetbundle", BlobRef("blobs/" + second_sha, second_sha, 6), 0, 6
            ),
            RedirectSlicePlan(
                "scene/a.assetbundle", BlobRef("blobs/" + first_sha, first_sha, 5), 6, 5
            ),
        ),
    )
    store = _Store()
    result = RedirectContainerBuilder.build(plan, {first_sha: first, second_sha: second}, store)
    assert result.content == second + first
    assert result.blob.sha256 == hashlib.sha256(second + first).hexdigest()
    assert store.requests[0].content == second + first


def test_redirect_container_builder_rejects_short_or_mismatched_blob() -> None:
    """验证读到错误字节时不提交容器。"""
    digest = hashlib.sha256(b"expected").hexdigest()
    plan = RedirectContainerPlan(
        "scene/redirect/0.assetbundle",
        (RedirectSlicePlan("scene/a.assetbundle", BlobRef("blobs/" + digest, digest, 8), 0, 8),),
    )
    store = _Store()
    with pytest.raises(PublishError):
        RedirectContainerBuilder.build(plan, {digest: b"short"}, store)
    assert store.requests == []
