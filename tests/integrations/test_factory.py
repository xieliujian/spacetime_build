"""验证本地与 HTTP 对象存储组合根的显式装配边界。"""

from __future__ import annotations

from configuration.model import SecretRef
from integrations.factory import IntegrationFactory
from integrations.storage import HttpObjectStore
from ports.http import HttpRequest, HttpResponse
from ports.process import ProcessRequest, ProcessResult


class _ProcessRunner:
    """提供组合根所需的最小进程端口替身。"""

    def run(self, request: ProcessRequest) -> ProcessResult:
        """该测试不执行外部进程。"""
        raise AssertionError(f"不应执行外部进程: {request.executable}")


class _Transport:
    """提供组合根所需的最小 HTTP 端口替身。"""

    def send(self, request: HttpRequest) -> HttpResponse:
        """该测试不发起真实 HTTP 请求。"""
        raise AssertionError(f"不应发送 HTTP 请求: {request.url}")


def test_remote_factory_explicitly_binds_http_object_store() -> None:
    """验证远端组合根只在显式调用时绑定 HTTP 对象存储。"""
    transport = _Transport()

    factory = IntegrationFactory.remote(
        _ProcessRunner(),
        "https://cdn.example/objects",
        http_transport=transport,
    )

    assert factory.http_transport is transport
    assert isinstance(factory.object_store, HttpObjectStore)


def test_remote_factory_forwards_credential_without_resolving_it() -> None:
    """验证远端组合根只转发 SecretRef，不在装配阶段读取秘密。"""
    transport = _Transport()
    credential = SecretRef("secret://env/CDN_TOKEN")

    factory = IntegrationFactory.remote(
        _ProcessRunner(),
        "https://cdn.example/objects",
        http_transport=transport,
        credential=credential,
    )

    assert isinstance(factory.object_store, HttpObjectStore)
    assert "CDN_TOKEN" not in repr(factory)
