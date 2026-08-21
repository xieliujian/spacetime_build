"""第二层外部适配器组合根。

组合根集中创建端口实现，业务层只接收 Protocol。默认 local 工厂绑定本地安全替身和标准库
实现；remote 工厂仅在调用方显式提供 HTTP 对象地址时绑定远端对象存储，其他真实系统的
地址、凭据与可执行文件仍由配置层显式提供。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from integrations.http import UrllibHttpTransport
from integrations.secrets import ControlledFileSecretProvider, EnvironmentSecretProvider
from integrations.storage import FileSystemObjectStore, HttpObjectStore
from integrations.workspace import LocalWorkspaceProvider
from ports.http import HttpTransport
from ports.process import ProcessRunner
from ports.secrets import SecretProvider
from ports.storage import ObjectStore
from ports.workspace import WorkspaceProvider
from configuration.model import SecretRef


@dataclass(frozen=True, slots=True)
class IntegrationFactory:
    """持有一次构建运行所需的外部端口实现。"""

    process_runner: ProcessRunner
    http_transport: HttpTransport
    workspace_provider: WorkspaceProvider
    secret_provider: SecretProvider
    object_store: ObjectStore

    @classmethod
    def local(
        cls, process_runner: ProcessRunner, object_root: Path, secret_root: Path | None = None
    ) -> "IntegrationFactory":
        """创建本地对象存储、工作区、urllib HTTP 和环境凭据组合。"""
        secret_provider: SecretProvider = EnvironmentSecretProvider()
        if secret_root is not None:
            secret_provider = ControlledFileSecretProvider(str(secret_root))
        return cls(
            process_runner=process_runner,
            http_transport=UrllibHttpTransport(),
            workspace_provider=LocalWorkspaceProvider(),
            secret_provider=secret_provider,
            object_store=FileSystemObjectStore(object_root),
        )

    @classmethod
    def remote(
        cls,
        process_runner: ProcessRunner,
        object_base_url: str,
        *,
        http_transport: HttpTransport | None = None,
        secret_root: Path | None = None,
        credential: SecretRef | None = None,
    ) -> "IntegrationFactory":
        """显式创建 HTTP 对象存储组合，不在默认 CLI 中自动启用。"""
        secret_provider: SecretProvider = EnvironmentSecretProvider()
        if secret_root is not None:
            secret_provider = ControlledFileSecretProvider(str(secret_root))
        active_transport = http_transport if http_transport is not None else UrllibHttpTransport()
        return cls(
            process_runner=process_runner,
            http_transport=active_transport,
            workspace_provider=LocalWorkspaceProvider(),
            secret_provider=secret_provider,
            object_store=HttpObjectStore(
                object_base_url,
                active_transport,
                credential=credential,
                secret_provider=secret_provider if credential is not None else None,
            ),
        )
