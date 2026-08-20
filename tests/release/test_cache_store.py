"""发布缓存命中、损坏废弃和禁用语义测试。"""

from core.artifacts import BlobRef
from ports.storage import (
    CompareAndSwapRequest,
    CompareAndSwapResult,
    ObjectStore,
    ObjectVerification,
    PutObjectRequest,
    StoredObject,
)
from release.cache_store import ArtifactCache


class _Store(ObjectStore):
    """可注入验证结果的内存对象存储。"""

    def __init__(self) -> None:
        """初始化默认成功的验证结果。"""
        self.valid = True

    def put(self, request: PutObjectRequest) -> StoredObject:
        """返回写入请求对应的存储回执。"""
        return StoredObject(request.key, request.sha256, len(request.content))

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """按可注入开关返回正确或损坏的校验结果。"""
        if not self.valid:
            return ObjectVerification(reference, True, "0" * 64, reference.size + 1)
        return ObjectVerification(reference, True, reference.sha256, reference.size)

    def compare_and_swap(self, request: CompareAndSwapRequest) -> CompareAndSwapResult:
        """该测试存储不支持版本入口 CAS。"""
        raise NotImplementedError


def test_artifact_cache_revalidates_hit_and_marks_corruption_obsolete() -> None:
    """验证命中复核和损坏项不返回业务层。"""
    store = _Store()
    cache = ArtifactCache(store)
    blob = BlobRef("blobs/" + "a" * 64, "a" * 64, 10)
    cache.put("k", blob, {"task": "scene"})
    assert cache.get("k") is not None
    store.valid = False
    assert cache.get("k") is None
    assert cache.is_obsolete("k") is True


def test_artifact_cache_can_be_disabled_or_forced_miss() -> None:
    """验证禁用和强制重建不读取缓存。"""
    cache = ArtifactCache(_Store())
    blob = BlobRef("blobs/" + "b" * 64, "b" * 64, 1)
    cache.put("k", blob, {})
    assert cache.get("k", enabled=False) is None
    assert cache.get("k", force_rebuild=True) is None
