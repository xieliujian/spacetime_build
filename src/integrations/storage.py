"""文件系统和 HTTP 对象存储及版本入口 CAS 适配器。

普通对象使用排他创建，重复写入只有内容完全一致时才幂等成功；版本入口是可变对象，
使用进程内锁和原子替换按 generation 执行 CAS。该实现用于本地 CDN fixture，供应商存储
适配器可以复用同一 ``ObjectStore`` 端口。HTTP 适配器约定对象服务返回摘要和代际响应头，
不把服务商 URL 规则泄漏到发布领域层。
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

from core.errors import ToolExecutionError
from ports.http import HttpMethod, HttpRequest, HttpResponse, HttpTransport
from ports.storage import (
    CompareAndSwapRequest,
    CompareAndSwapResult,
    ObjectStore,
    ObjectVerification,
    PutObjectRequest,
    StoredObject,
    validate_object_key,
)


class HttpObjectStore(ObjectStore):
    """通过结构化 HTTP 端口访问对象服务和版本入口 CAS。"""

    def __init__(
        self,
        base_url: str,
        transport: HttpTransport,
        *,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 4096,
    ) -> None:
        """创建 HTTP 对象存储适配器，不在构造阶段发起网络请求。"""
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("base_url 必须是非空字符串")
        try:
            parsed = urlsplit(base_url)
        except ValueError as exc:
            raise ValueError("base_url 不是合法 URL") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url 必须是无用户信息、查询和片段的 HTTP URL")
        if not callable(getattr(transport, "send", None)):
            raise TypeError("transport 必须提供 send 方法")
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        HttpRequest(
            HttpMethod.HEAD,
            self._base_url,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )

    def put(self, request: PutObjectRequest) -> StoredObject:
        """以 PUT 写入对象并验证 HTTP 成功状态。"""
        if not isinstance(request, PutObjectRequest):
            raise TypeError("request 必须是 PutObjectRequest")
        if hashlib.sha256(request.content).hexdigest() != request.sha256:
            raise ValueError("对象内容 SHA256 与请求不一致")
        response = self._send(
            HttpMethod.PUT,
            request.key,
            body=request.content,
            headers=(
                ("Content-Length", str(len(request.content))),
                ("X-Content-SHA256", request.sha256),
            ),
        )
        self._require_success(response, "对象 PUT")
        return StoredObject(request.key, request.sha256, len(request.content))

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """通过 HEAD 读取远端摘要和大小，404 映射为对象不存在。"""
        if not isinstance(reference, StoredObject):
            raise TypeError("reference 必须是 StoredObject")
        response = self._send(HttpMethod.HEAD, reference.key)
        if response.status_code == 404:
            return ObjectVerification(reference, False, None, None)
        self._require_success(response, "对象 HEAD")
        sha256 = self._required_sha256(response)
        size = self._required_size(response)
        return ObjectVerification(reference, True, sha256, size)

    def compare_and_swap(self, request: CompareAndSwapRequest) -> CompareAndSwapResult:
        """使用 If-Match 代际执行版本入口 CAS，并保留服务端冲突摘要。"""
        if not isinstance(request, CompareAndSwapRequest):
            raise TypeError("request 必须是 CompareAndSwapRequest")
        digest = hashlib.sha256(request.content).hexdigest()
        response = self._send(
            HttpMethod.PUT,
            request.key,
            body=request.content,
            headers=(
                ("Content-Length", str(len(request.content))),
                ("X-Content-SHA256", digest),
                ("If-Match", str(request.expected_generation)),
            ),
        )
        if response.status_code in {409, 412}:
            generation = self._required_generation(response)
            current_sha256 = self._optional_sha256(response)
            return CompareAndSwapResult(False, generation, current_sha256)
        self._require_success(response, "版本入口 CAS")
        generation = self._required_generation(response)
        response_sha256 = self._required_sha256(response)
        if response_sha256 != digest:
            raise ToolExecutionError("版本入口 CAS 成功但远端 SHA256 不一致")
        return CompareAndSwapResult(True, generation, response_sha256)

    def _send(
        self,
        method: HttpMethod,
        key: str,
        *,
        body: bytes = b"",
        headers: tuple[tuple[str, str], ...] = (),
    ) -> HttpResponse:
        """构造单次对象请求并委托已有 HTTP transport。"""
        return self._transport.send(
            HttpRequest(
                method,
                self._url(key),
                headers=headers,
                body=body,
                timeout_seconds=self._timeout_seconds,
                max_response_bytes=self._max_response_bytes,
            )
        )

    def _url(self, key: str) -> str:
        """把相对对象键逐段编码为稳定 HTTP URL。"""
        validate_object_key(key)
        encoded = "/".join(quote(part, safe="-._~") for part in key.split("/"))
        return f"{self._base_url}/{encoded}"

    @staticmethod
    def _require_success(response: HttpResponse, operation: str) -> None:
        """把非 2xx HTTP 响应转换为脱敏工具错误。"""
        if not isinstance(response, HttpResponse):
            raise TypeError("transport 必须返回 HttpResponse")
        if not 200 <= response.status_code < 300:
            raise ToolExecutionError(f"{operation}失败，HTTP {response.status_code}")

    @staticmethod
    def _header(response: HttpResponse, name: str) -> str | None:
        """不区分大小写读取单个响应头。"""
        folded_name = name.casefold()
        for header_name, value in response.headers:
            if header_name.casefold() == folded_name:
                return value
        return None

    @classmethod
    def _required_sha256(cls, response: HttpResponse) -> str:
        """读取并校验对象服务返回的摘要头。"""
        value = cls._optional_sha256(response)
        if value is None:
            raise ToolExecutionError("对象响应缺少 X-Object-SHA256")
        return value

    @classmethod
    def _optional_sha256(cls, response: HttpResponse) -> str | None:
        """读取可选摘要头，并拒绝非小写 SHA256 值。"""
        value = cls._header(response, "X-Object-SHA256")
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ToolExecutionError("对象响应 X-Object-SHA256 无效")
        return value

    @classmethod
    def _required_size(cls, response: HttpResponse) -> int:
        """读取并校验 Content-Length。"""
        value = cls._header(response, "Content-Length")
        if value is None or not value.isdigit():
            raise ToolExecutionError("对象响应缺少有效 Content-Length")
        return int(value)

    @classmethod
    def _required_generation(cls, response: HttpResponse) -> int:
        """读取并校验版本入口代际响应头。"""
        value = cls._header(response, "X-Object-Generation")
        if value is None or not value.isdigit():
            raise ToolExecutionError("对象响应缺少有效 X-Object-Generation")
        return int(value)


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
