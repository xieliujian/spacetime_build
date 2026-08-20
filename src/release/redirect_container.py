"""按 Redirect 规划读取原始 Blob 并生成容器对象。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from core.artifacts import BlobRef
from core.errors import PublishError
from ports.storage import ObjectStore, PutObjectRequest
from release.redirect import RedirectContainerPlan


class RedirectBlobReader(Protocol):
    """Redirect 容器构建所需的最小 Blob 读取协议。"""

    def read(self, blob: BlobRef) -> bytes:
        """读取并返回指定持久 Blob 的完整字节。"""
        ...


@dataclass(frozen=True, slots=True)
class RedirectContainerResult:
    """已生成 Redirect 容器的字节、Blob 身份和逻辑路径。"""

    logical_path: str
    blob: BlobRef
    content: bytes


class RedirectContainerBuilder:
    """按规划顺序拼接 Redirect 容器并提交内容寻址对象。"""

    @staticmethod
    def build(
        plan: RedirectContainerPlan,
        reader: RedirectBlobReader | Mapping[str, bytes],
        object_store: ObjectStore,
    ) -> RedirectContainerResult:
        """读取每个源 Blob、校验完整身份并提交容器。

        参数：
            plan: 已验证的单个容器规划。
            reader: ``read(blob)`` 读取器，或供测试/本地适配器使用的 SHA256 到 bytes 映射。
            object_store: 用于提交容器 Blob 的对象存储端口。

        返回：
            已提交容器的逻辑路径、持久 BlobRef 与完整字节。

        异常：
            源 Blob 缺失、长度或 SHA256 不匹配时抛出 ``PublishError``。

        约束与副作用：
            只在所有源 Blob 校验通过后提交一次容器；不修改源对象。
        """
        if not isinstance(plan, RedirectContainerPlan):
            raise TypeError("plan 必须是 RedirectContainerPlan")
        if not isinstance(object_store, ObjectStore):
            raise TypeError("object_store 必须是 ObjectStore")
        content_parts: list[bytes] = []
        for item in plan.slices:
            try:
                content = (
                    reader[item.source_blob.sha256]
                    if isinstance(reader, Mapping)
                    else reader.read(item.source_blob)
                )
            except (KeyError, OSError, ValueError) as exc:
                raise PublishError(f"Redirect 源 Blob 不可读: {item.logical_path}") from exc
            if not isinstance(content, bytes) or len(content) != item.source_blob.size:
                raise PublishError(f"Redirect 源 Blob 长度不匹配: {item.logical_path}")
            if hashlib.sha256(content).hexdigest() != item.source_blob.sha256:
                raise PublishError(f"Redirect 源 Blob SHA256 不匹配: {item.logical_path}")
            content_parts.append(content)
        content = b"".join(content_parts)
        digest = hashlib.sha256(content).hexdigest()
        blob = BlobRef(f"blobs/{digest}", digest, len(content))
        stored = object_store.put(PutObjectRequest(plan.container_logical_path, content, digest))
        if (
            stored.key != plan.container_logical_path
            or stored.sha256 != digest
            or stored.size != len(content)
        ):
            raise PublishError("Redirect 容器对象存储回执不一致")
        return RedirectContainerResult(plan.container_logical_path, blob, content)
