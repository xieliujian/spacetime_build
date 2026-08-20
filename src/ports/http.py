"""外部 HTTP 调用的不可变端口契约。

请求只允许显式 URL、方法、头和有限响应大小；秘密通过独立的 binding ID 注入，禁止
把秘密值放入请求对象或 URL。具体网络实现位于 ``integrations.http``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from urllib.parse import urlsplit

from ports.secrets import SecretLease


class HttpMethod(str, Enum):
    """允许的 HTTP 方法集合。"""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    HEAD = "HEAD"


class SecretHttpTarget(str, Enum):
    """秘密允许写入的 HTTP 槽位。"""

    HEADER = "header"
    AUTHORIZATION = "authorization"


@dataclass(frozen=True, slots=True)
class SecretHttpBinding:
    """把不透明 binding ID 绑定到白名单 HTTP 槽位。"""

    binding_id: str
    target: SecretHttpTarget
    slot: str

    def __post_init__(self) -> None:
        """校验 binding ID、目标和 HTTP 头名。"""
        if not isinstance(self.binding_id, str) or not self.binding_id:
            raise ValueError("binding_id 必须是非空字符串")
        if not isinstance(self.target, SecretHttpTarget):
            raise TypeError("target 必须是 SecretHttpTarget")
        if not isinstance(self.slot, str) or not self.slot:
            raise ValueError("slot 必须是非空字符串")
        if self.target is SecretHttpTarget.AUTHORIZATION and self.slot != "Authorization":
            raise ValueError("AUTHORIZATION slot 必须是 Authorization")
        if any(character in self.slot for character in "\r\n:"):
            raise ValueError("slot 不能包含非法 HTTP 头字符")


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """一次有界 HTTP 请求。"""

    method: HttpMethod
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    timeout_seconds: float = 30.0
    max_response_bytes: int = 16 * 1024 * 1024
    secret_bindings: tuple[SecretHttpBinding, ...] = ()
    secret_lease: SecretLease | None = None

    def __post_init__(self) -> None:
        """校验 URL、头、请求体、限制和秘密绑定。"""
        if not isinstance(self.method, HttpMethod):
            raise TypeError("method 必须是 HttpMethod")
        if not isinstance(self.url, str):
            raise TypeError("url 必须是 str")
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise ValueError("url 必须是无用户信息的 http(s) URL")
        if not isinstance(self.headers, tuple):
            raise TypeError("headers 必须是 tuple")
        seen_headers: set[str] = set()
        for name, value in self.headers:
            if not isinstance(name, str) or not name or any(c in name for c in "\r\n:"):
                raise ValueError("HTTP header 名称无效")
            if not isinstance(value, str) or any(c in value for c in "\r\n"):
                raise ValueError("HTTP header 值无效")
            folded = name.casefold()
            if folded in seen_headers:
                raise ValueError(f"HTTP header 重复: {name}")
            seen_headers.add(folded)
        if not isinstance(self.body, bytes):
            raise TypeError("body 必须是 bytes")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise TypeError("timeout_seconds 必须是数值")
        if not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是有限正数")
        if not isinstance(self.max_response_bytes, int) or isinstance(
            self.max_response_bytes, bool
        ):
            raise TypeError("max_response_bytes 必须是 int")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes 必须大于 0")
        seen_bindings: set[str] = set()
        seen_slots: set[tuple[SecretHttpTarget, str]] = set()
        for binding in self.secret_bindings:
            if not isinstance(binding, SecretHttpBinding):
                raise TypeError("secret_bindings 元素必须是 SecretHttpBinding")
            if binding.binding_id in seen_bindings:
                raise ValueError("secret_bindings 存在重复 binding_id")
            if (binding.target, binding.slot) in seen_slots:
                raise ValueError("secret_bindings 存在重复目标槽位")
            seen_bindings.add(binding.binding_id)
            seen_slots.add((binding.target, binding.slot))


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """HTTP 响应的有界摘要。"""

    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def __post_init__(self) -> None:
        """校验状态码、响应头和二进制响应体。"""
        if not isinstance(self.status_code, int) or isinstance(self.status_code, bool):
            raise TypeError("status_code 必须是 int")
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code 必须位于 100..599")
        if not isinstance(self.headers, tuple) or not isinstance(self.body, bytes):
            raise TypeError("headers 必须是 tuple 且 body 必须是 bytes")


class HttpTransport(Protocol):
    """HTTP 请求传输协议。"""

    def send(self, request: HttpRequest) -> HttpResponse:
        """发送请求并返回有界响应。"""
        ...
