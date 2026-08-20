"""将工作区文件提交为持久内容寻址 Blob。

``BlobCommitter`` 只通过 ``ports.storage.ObjectStore`` 写入对象，不把临时工作区
路径登记到 ``BlobRef``。提交前后检查文件身份，发现读取期间文件变化时拒绝登记；
调用方可提供允许的隔离根目录以阻断路径逃逸。导入本模块不写文件。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, cast

from core.artifacts import BlobRef
from core.errors import ArtifactValidationError
from ports.storage import PutObjectRequest, StoredObject


class BlobStorePort(Protocol):
    """Blob 提交器需要的最小对象存储端口。"""

    def put(self, request: PutObjectRequest) -> StoredObject:
        """保存不可变对象并返回持久引用。"""
        ...


class BlobCommitter:
    """把稳定文件内容提交到对象存储并返回 ``BlobRef``。

    职责：
        校验文件边界和稳定性，计算 SHA256/大小，以 ``blobs/<sha256>`` 作为不可变
        对象键写入端口，并验证端口返回引用与请求一致。

    参数：
        object_store: 实现 ``ObjectStore`` 端口的持久对象存储。

    返回：
        ``commit`` 返回不含本地路径的 ``BlobRef``。

    异常：
        文件不存在、不是普通文件、越出允许根或读取期间变化时抛出
        ``FileNotFoundError`` / ``ValueError`` / ``ArtifactValidationError``。

    约束与副作用：
        只允许通过对象存储端口产生外部写入；不会创建或删除工作区文件。
    """

    def __init__(self, object_store: BlobStorePort) -> None:
        """保存对象存储端口依赖。

        参数：
            object_store: 不可变对象提交端口。

        返回：
            ``None``。

        异常：
            ``object_store`` 为空时抛出 ``TypeError``。

        约束与副作用：
            仅保存引用，不执行写入。
        """
        if cast(object, object_store) is None:
            raise TypeError("object_store 不得为 None")
        self._object_store = object_store

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int, int]:
        """读取普通文件的大小、修改时间和 inode 指纹。

        参数：
            path: 已解析的普通文件路径。

        返回：
            ``(size, mtime_ns, inode)`` 元组。

        异常：
            路径不是普通文件或为符号链接时抛出 ``ValueError``。

        约束与副作用：
            只读取元数据，不打开文件。
        """
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"提交目标必须是普通文件: {path}")
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, getattr(stat, "st_ino", 0)

    def _read_stable_bytes(self, path: Path) -> tuple[bytes, int]:
        """读取文件并在读取前后确认元数据未变化。

        参数：
            path: 已确认的普通文件。

        返回：
            文件内容与读取时大小。

        异常：
            文件变化时抛出 ``ArtifactValidationError``。

        约束与副作用：
            只读文件，不修改输入；变化的输入绝不进入对象存储。
        """
        before = self._fingerprint(path)
        content = path.read_bytes()
        after = self._fingerprint(path)
        if before != after or len(content) != before[0]:
            raise ArtifactValidationError(f"文件在读取期间发生变化: {path}")
        return content, len(content)

    def commit(self, path: Path, *, allowed_root: Path | None = None) -> BlobRef:
        """提交一个稳定文件并返回内容寻址引用。

        参数：
            path: 待读取的绝对文件路径。
            allowed_root: 可选的绝对隔离根；解析后的文件必须位于其内部。

        返回：
            指向 ``blobs/<sha256>`` 的 ``BlobRef``。

        异常：
            路径类型、根边界、文件状态、读期间变化或存储回执不一致时抛出业务异常。

        约束与副作用：
            文件读取稳定后才调用 ``ObjectStore.put``；不保留本地路径。
        """
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path 必须是绝对 Path")
        resolved = path.resolve(strict=False)
        if allowed_root is not None:
            if not isinstance(allowed_root, Path) or not allowed_root.is_absolute():
                raise ValueError("allowed_root 必须是绝对 Path")
            root = allowed_root.resolve()
            if resolved != root and root not in resolved.parents:
                raise ValueError("提交路径越出 allowed_root")
        if not resolved.exists():
            raise FileNotFoundError(resolved)
        content, size = self._read_stable_bytes(resolved)
        digest = hashlib.sha256(content).hexdigest()
        key = f"blobs/{digest}"
        stored = self._object_store.put(PutObjectRequest(key, content, digest))
        if stored.key != key or stored.sha256 != digest or stored.size != size:
            raise ArtifactValidationError("对象存储返回的 Blob 引用与内容不一致")
        # 最后一次元数据检查覆盖调用替身或慢速存储期间发生的输入变更。
        if self._fingerprint(resolved)[0] != size:
            raise ArtifactValidationError(f"文件在提交期间发生变化: {resolved}")
        return BlobRef(key, digest, size)
