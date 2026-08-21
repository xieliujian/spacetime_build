"""验证 HTTP 对象存储适配器的路径、摘要校验和 CAS 协议边界。"""

from __future__ import annotations

import hashlib

import pytest

from core.errors import ToolExecutionError
from integrations.storage import HttpObjectStore
from configuration.model import SecretRef
from ports.http import HttpRequest, HttpResponse, SecretHttpTarget
from ports.secrets import SecretLease, SecretLeaseRequest
from ports.storage import CompareAndSwapRequest, PutObjectRequest, StoredObject


class _FakeTransport:
    """按顺序返回响应并记录适配器发出的 HTTP 请求。"""

    def __init__(self, responses: list[HttpResponse]) -> None:
        """保存待返回的响应序列和空请求记录。"""
        self._responses = responses
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        """记录一次请求并返回队首响应。"""
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("fake HTTP 响应队列为空")
        return self._responses.pop(0)


class _Lease(SecretLease):
    """记录 opaque binding 解析和租约关闭次数的测试租约。"""

    def __init__(self, value: str) -> None:
        """保存仅供 fake transport 解析的秘密值。"""
        self.value = value
        self.resolve_calls: list[str] = []
        self.close_calls = 0

    def resolve(self, binding_id: str) -> str:
        """按 binding ID 返回测试秘密并记录访问。"""
        self.resolve_calls.append(binding_id)
        return self.value

    def close(self) -> None:
        """记录一次租约关闭。"""
        self.close_calls += 1

    def __repr__(self) -> str:
        """返回不包含测试秘密的表示。"""
        return "Lease(<redacted>)"


class _SecretProvider:
    """为每次对象操作创建独立短期租约。"""

    def __init__(self, lease: _Lease) -> None:
        """保存待返回的测试租约。"""
        self.lease = lease
        self.requests: list[SecretLeaseRequest] = []

    def acquire(self, request: SecretLeaseRequest) -> SecretLease:
        """记录租约申请并返回测试租约。"""
        self.requests.append(request)
        return self.lease


def _digest(content: bytes) -> str:
    """计算测试对象的 SHA256。"""
    return hashlib.sha256(content).hexdigest()


def test_http_object_store_put_escapes_key_and_checks_response() -> None:
    """验证 PUT 使用安全对象路径、内容摘要和结构化成功回执。"""
    content = b"payload"
    digest = _digest(content)
    transport = _FakeTransport([HttpResponse(201, (), b"")])
    store = HttpObjectStore("https://cdn.example/objects/", transport)

    stored = store.put(PutObjectRequest("release/file name.bin", content, digest))

    request = transport.requests[0]
    assert request.method.value == "PUT"
    assert request.url == "https://cdn.example/objects/release/file%20name.bin"
    assert dict(request.headers) == {
        "Content-Length": str(len(content)),
        "X-Content-SHA256": digest,
    }
    assert stored.key == "release/file name.bin"
    assert stored.sha256 == digest
    assert stored.size == len(content)


def test_http_object_store_binds_authorization_with_short_lived_opaque_lease() -> None:
    """验证对象请求只携带 binding ID，且每次操作结束后关闭租约。"""
    content = b"payload"
    lease = _Lease("Bearer secret-value")
    provider = _SecretProvider(lease)

    class Transport(_FakeTransport):
        """解析授权 binding 以模拟真实 HTTP transport。"""

        def send(self, request: HttpRequest) -> HttpResponse:
            """确认 transport 才能在发送边界解析秘密。"""
            assert len(request.secret_bindings) == 1
            binding = request.secret_bindings[0]
            assert binding.target is SecretHttpTarget.AUTHORIZATION
            assert binding.slot == "Authorization"
            assert request.secret_lease is not None
            assert request.secret_lease.resolve(binding.binding_id) == "Bearer secret-value"
            return super().send(request)

    transport = Transport([HttpResponse(201, (), b"")])
    store = HttpObjectStore(
        "https://cdn.example",
        transport,
        credential=SecretRef("secret://env/CDN_TOKEN"),
        secret_provider=provider,
    )

    store.put(PutObjectRequest("release/file.bin", content, _digest(content)))

    assert len(provider.requests) == 1
    assert provider.requests[0].binding_ids == ("object-store-authorization",)
    assert lease.resolve_calls == ["object-store-authorization"]
    assert lease.close_calls == 1
    assert "secret-value" not in repr(transport.requests[0])


def test_http_object_store_closes_lease_when_transport_fails() -> None:
    """验证 HTTP transport 异常时也会关闭已经获取的秘密租约。"""
    lease = _Lease("secret-value")
    provider = _SecretProvider(lease)

    class FailingTransport:
        """在发送边界注入网络失败的 transport。"""

        def send(self, request: HttpRequest) -> HttpResponse:
            """抛出不含秘密的传输异常。"""
            raise ConnectionError("network unavailable")

    store = HttpObjectStore(
        "https://cdn.example",
        FailingTransport(),
        credential=SecretRef("secret://env/CDN_TOKEN"),
        secret_provider=provider,
    )

    with pytest.raises(ConnectionError):
        store.put(PutObjectRequest("release/file.bin", b"payload", _digest(b"payload")))

    assert lease.close_calls == 1


def test_http_object_store_verify_maps_not_found_and_validates_head_headers() -> None:
    """验证 HEAD 的 404 语义和远端摘要/大小响应。"""
    content = b"payload"
    digest = _digest(content)
    transport = _FakeTransport(
        [
            HttpResponse(404, (), b""),
            HttpResponse(
                200,
                (("x-object-sha256", digest), ("Content-Length", str(len(content)))),
                b"",
            ),
        ]
    )
    store = HttpObjectStore("https://cdn.example", transport)
    missing_reference = StoredObject("release/file.bin", digest, len(content))
    missing = store.verify(missing_reference)
    present = store.verify(missing_reference)

    assert not missing.exists
    assert present.exists
    assert present.sha256 == digest
    assert present.size == len(content)
    assert all(request.method.value == "HEAD" for request in transport.requests)


def test_http_object_store_rejects_content_digest_mismatch_before_network_call() -> None:
    """验证 PUT 在网络调用前拒绝内容与声明摘要不一致。"""
    transport = _FakeTransport([HttpResponse(201, (), b"")])
    store = HttpObjectStore("https://cdn.example", transport)

    with pytest.raises(ValueError, match="SHA256"):
        store.put(PutObjectRequest("release/file.bin", b"payload", "a" * 64))

    assert transport.requests == []


def test_http_object_store_cas_uses_generation_header_and_exposes_conflict() -> None:
    """验证 CAS 使用 If-Match 代际并保留服务端冲突摘要。"""
    content = b"v2"
    digest = _digest(content)
    transport = _FakeTransport(
        [
            HttpResponse(
                200,
                (("X-Object-Generation", "4"), ("X-Object-SHA256", digest)),
                b"",
            ),
            HttpResponse(
                409,
                (("X-Object-Generation", "5"), ("X-Object-SHA256", "a" * 64)),
                b"",
            ),
        ]
    )
    store = HttpObjectStore("https://cdn.example", transport)

    applied = store.compare_and_swap(CompareAndSwapRequest("version/current", 3, content))
    conflict = store.compare_and_swap(CompareAndSwapRequest("version/current", 4, content))

    assert applied.applied
    assert applied.generation == 4
    assert applied.sha256 == digest
    assert not conflict.applied
    assert conflict.generation == 5
    assert conflict.sha256 == "a" * 64
    assert dict(transport.requests[0].headers)["If-Match"] == "3"


def test_http_object_store_rejects_success_without_required_metadata() -> None:
    """验证远端成功响应缺少摘要元数据时不能伪造验证结果。"""
    transport = _FakeTransport([HttpResponse(200, (("Content-Length", "7"),), b"")])
    store = HttpObjectStore("https://cdn.example", transport)
    reference = StoredObject("release/file.bin", _digest(b"payload"), len(b"payload"))

    with pytest.raises(ToolExecutionError, match="SHA256"):
        store.verify(reference)
