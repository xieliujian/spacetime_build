"""本地文件系统对象存储和版本入口 CAS 适配器。

普通对象使用排他创建，重复写入只有内容完全一致时才幂等成功；版本入口是可变对象，
使用进程内锁和原子替换按 generation 执行 CAS。该实现用于本地 CDN fixture，供应商存储
适配器可以复用同一 ``ObjectStore`` 端口。
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from pathlib import Path

from ports.storage import (
    CompareAndSwapRequest,
    CompareAndSwapResult,
    ObjectStore,
    ObjectVerification,
    PutObjectRequest,
    StoredObject,
    validate_object_key,
)


class FileSystemObjectStore(ObjectStore):
    """在配置根目录下实现不可变对象和版本入口 CAS。"""

    def __init__(self, root: Path) -> None:
        """创建并保存绝对对象根目录。"""
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("root 必须是绝对 Path")
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        """解析对象键并确认最终路径仍位于根目录。"""
        validate_object_key(key)
        path = (self._root / key).resolve()
        if path != self._root and self._root not in path.parents:
            raise ValueError("对象路径越出存储根目录")
        return path

    def put(self, request: PutObjectRequest) -> StoredObject:
        """校验摘要后排他写入对象，重复同内容返回已有引用。"""
        if not isinstance(request, PutObjectRequest):
            raise TypeError("request 必须是 PutObjectRequest")
        actual = hashlib.sha256(request.content).hexdigest()
        if actual != request.sha256:
            raise ValueError("对象内容 SHA256 与请求不一致")
        path = self._path(request.key)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.is_symlink() or not path.is_file() or path.read_bytes() != request.content:
                    raise ValueError("不可变对象内容冲突")
            else:
                self._write_exclusive(path, request.content)
        return StoredObject(request.key, request.sha256, len(request.content))

    def _write_exclusive(self, path: Path, content: bytes) -> None:
        """使用排他创建写入一个新对象。"""
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(path, flags)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor != -1:
                os.close(descriptor)

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """回读对象并重新计算大小和 SHA256。"""
        if not isinstance(reference, StoredObject):
            raise TypeError("reference 必须是 StoredObject")
        path = self._path(reference.key)
        if not path.is_file() or path.is_symlink():
            return ObjectVerification(reference, False, None, None)
        content = path.read_bytes()
        return ObjectVerification(
            reference, True, hashlib.sha256(content).hexdigest(), len(content)
        )

    def compare_and_swap(self, request: CompareAndSwapRequest) -> CompareAndSwapResult:
        """按当前代际原子替换入口内容并返回新代际。"""
        if not isinstance(request, CompareAndSwapRequest):
            raise TypeError("request 必须是 CompareAndSwapRequest")
        path = self._path(request.key)
        generation_path = self._path(request.key + ".generation")
        with self._lock:
            generation = self._read_generation(generation_path)
            if generation != request.expected_generation:
                current = path.read_bytes() if path.is_file() else None
                return CompareAndSwapResult(
                    False,
                    generation,
                    hashlib.sha256(current).hexdigest() if current is not None else None,
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(request.content)
            os.replace(temporary, path)
            next_generation = generation + 1
            generation_path.write_text(str(next_generation), encoding="ascii")
            return CompareAndSwapResult(
                True, next_generation, hashlib.sha256(request.content).hexdigest()
            )

    @staticmethod
    def _read_generation(path: Path) -> int:
        """读取缺省为零的入口代际。"""
        if not path.is_file():
            return 0
        value = path.read_text(encoding="ascii")
        if not value.isdigit():
            raise ValueError("入口 generation 文件损坏")
        return int(value)
