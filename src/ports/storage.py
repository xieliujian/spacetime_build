"""对象存储、内容校验和版本入口 CAS 的端口契约。"""

from __future__ import annotations

from dataclasses import dataclass


def validate_object_key(key: str) -> str:
    """校验使用正斜杠的相对对象键。"""
    if not isinstance(key, str) or not key or key.startswith(("/", "\\")):
        raise ValueError("object key 必须是非空相对路径")
    parts = key.split("/")
    if any(not part or part in {".", ".."} or "\\" in part for part in parts):
        raise ValueError("object key 含非法路径段")
    if any(ord(c) < 0x20 for c in key) or "%2f" in key.casefold() or "%5c" in key.casefold():
        raise ValueError("object key 含控制字符或 URL 绕过")
    return key


@dataclass(frozen=True, slots=True)
class PutObjectRequest:
    """不可变对象写入请求。"""

    key: str
    content: bytes
    sha256: str

    def __post_init__(self) -> None:
        """校验对象键、内容和摘要。"""
        validate_object_key(self.key)
        if not isinstance(self.content, bytes):
            raise TypeError("content 必须是 bytes")
        if not isinstance(self.sha256, str) or len(self.sha256) != 64:
            raise ValueError("sha256 必须是 64 位十六进制摘要")


@dataclass(frozen=True, slots=True)
class StoredObject:
    """已保存对象的不可变引用。"""

    key: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class ObjectVerification:
    """对象远端校验结果。"""

    reference: StoredObject
    exists: bool
    sha256: str | None
    size: int | None


@dataclass(frozen=True, slots=True)
class CompareAndSwapRequest:
    """版本入口 compare-and-swap 请求。"""

    key: str
    expected_generation: int
    content: bytes

    def __post_init__(self) -> None:
        """校验入口键、旧代际和替换内容。"""
        validate_object_key(self.key)
        if not isinstance(self.expected_generation, int) or self.expected_generation < 0:
            raise ValueError("expected_generation 必须是非负整数")
        if not isinstance(self.content, bytes):
            raise TypeError("content 必须是 bytes")


@dataclass(frozen=True, slots=True)
class CompareAndSwapResult:
    """CAS 操作结果。"""

    applied: bool
    generation: int
    sha256: str | None


class ObjectStore:
    """对象存储端口的最小结构化协议。"""

    def put(self, request: PutObjectRequest) -> StoredObject:
        """以不可变键写入对象。"""
        raise NotImplementedError

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """读取并校验对象摘要和大小。"""
        raise NotImplementedError

    def compare_and_swap(self, request: CompareAndSwapRequest) -> CompareAndSwapResult:
        """按旧代际原子替换版本入口。"""
        raise NotImplementedError
