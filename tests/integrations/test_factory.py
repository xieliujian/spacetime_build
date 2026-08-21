"""验证本地与 HTTP 对象存储组合根的显式装配边界。"""

from __future__ import annotations

from pathlib import Path

import pytest

from configuration.model import SecretRef
from integrations.factory import IntegrationFactory
from integrations.storage import HttpObjectStore
from integrations.svn import SvnSourceProvider
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


def test_local_factory_explicitly_binds_svn_source_credential(tmp_path: Path) -> None:
    """验证本地组合根显式装配 SVN 读取端口并只转发秘密引用。"""
    credential = SecretRef("secret://env/SVN_PASSWORD")

    factory = IntegrationFactory.local(
        _ProcessRunner(),
        tmp_path / "objects",
        source_executable=tmp_path / "svn.exe",
        source_temp_root=tmp_path / "svn-commands",
        source_credential=credential,
    )

    assert isinstance(factory.source_provider, SvnSourceProvider)
    assert "SVN_PASSWORD" not in repr(factory)


def test_local_factory_rejects_partial_svn_configuration(tmp_path: Path) -> None:
    """验证组合根不接受不完整的 SVN 可执行文件和临时根配置。"""
    with pytest.raises(ValueError, match="必须同时提供"):
        IntegrationFactory.local(
            _ProcessRunner(),
            tmp_path / "objects",
            source_executable=tmp_path / "svn.exe",
        )
