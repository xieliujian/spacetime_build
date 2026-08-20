"""发布缓存的只读命中验证和损坏项废弃。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Mapping

from core.artifacts import BlobRef
from core.errors import PublishError
from ports.storage import ObjectStore, StoredObject


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """缓存键对应的不可变产物引用和类型化元数据。"""

    cache_key: str
    artifact: BlobRef
    metadata: tuple[tuple[str, str], ...]


class ArtifactCache:
    """保存不可变缓存元数据并在每次命中时复核远端 Blob。"""

    def __init__(self, object_store: ObjectStore) -> None:
        """绑定用于命中复核的对象存储端口。"""
        if not isinstance(object_store, ObjectStore):
            raise TypeError("object_store 必须是 ObjectStore")
        self._object_store = object_store
        self._entries: dict[str, CacheEntry] = {}
        self._obsolete: set[str] = set()
        self._lock = RLock()

    def put(self, cache_key: str, artifact: BlobRef, metadata: Mapping[str, str]) -> CacheEntry:
        """以不可变键登记缓存元数据。

        参数：
            cache_key: 已由 ``CacheKeyFactory`` 生成的非空身份键。
            artifact: 已提交到 CAS 的产物 Blob。
            metadata: 非空键值字符串元数据，会按 UTF-8 键排序冻结。

        返回：
            新的不可变 ``CacheEntry``。

        异常：
            键、Blob 或元数据类型非法时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            相同键只能保持同一 Blob 身份；冲突不会覆盖既有条目。
        """
        if not isinstance(cache_key, str) or not cache_key:
            raise ValueError("cache_key 必须是非空字符串")
        if not isinstance(artifact, BlobRef):
            raise TypeError("artifact 必须是 BlobRef")
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata 必须是字符串 Mapping")
        pairs = tuple(
            sorted(
                ((key, value) for key, value in metadata.items()),
                key=lambda pair: pair[0].encode("utf-8"),
            )
        )
        if any(
            not isinstance(key, str) or not key or not isinstance(value, str)
            for key, value in pairs
        ):
            raise ValueError("metadata 必须是非空字符串键值")
        entry = CacheEntry(cache_key, artifact, pairs)
        with self._lock:
            old = self._entries.get(cache_key)
            if old is not None and old.artifact != artifact:
                raise PublishError("相同 cache_key 已绑定其他 Blob")
            self._entries[cache_key] = entry
            self._obsolete.discard(cache_key)
        return entry

    def get(
        self,
        cache_key: str,
        *,
        enabled: bool = True,
        force_rebuild: bool = False,
    ) -> CacheEntry | None:
        """读取并重新验证缓存命中，损坏项只标记废弃。

        参数：
            cache_key: 缓存身份键。
            enabled: 是否启用缓存。
            force_rebuild: 是否显式要求忽略既有缓存。

        返回：
            远端存在且摘要/大小一致的 ``CacheEntry``，否则 ``None``。

        异常：
            复核适配器发生不可恢复错误时抛出 ``PublishError``。

        约束与副作用：
            只读并发安全；损坏项不会交给业务层，也不会删除不可变对象。
        """
        if not enabled or force_rebuild:
            return None
        with self._lock:
            entry = self._entries.get(cache_key)
        if entry is None:
            return None
        observed = self._object_store.verify(
            StoredObject(entry.artifact.locator, entry.artifact.sha256, entry.artifact.size)
        )
        if (
            not observed.exists
            or observed.sha256 != entry.artifact.sha256
            or observed.size != entry.artifact.size
        ):
            with self._lock:
                self._obsolete.add(cache_key)
            return None
        return entry

    def is_obsolete(self, cache_key: str) -> bool:
        """返回缓存键是否已因远端校验失败被标记废弃。"""
        with self._lock:
            return cache_key in self._obsolete
