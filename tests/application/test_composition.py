"""验证正式环境 application composition 的单一装配边界。"""

from pathlib import Path

from application.composition import ProductionApplicationServices
from configuration.model import SecretRef
from integrations.factory import IntegrationFactory
from integrations.storage import FileSystemObjectStore
from integrations.unity import UnityBatchRunner
from release.activator import ReleaseActivator
from release.remote_verifier import RemoteReleaseVerifier
from release.uploader import ReleaseObjectUploader


class _ProcessRunner:
    """提供组合根所需的最小进程端口。"""

    def run(self, request: object, cancellation: object = None) -> object:
        """测试不会执行外部进程。"""
        raise AssertionError(f"不应执行外部进程: {request}, {cancellation}")


def test_production_application_services_bind_release_and_probe_dependencies(
    tmp_path: Path,
) -> None:
    """验证 composition 一次性绑定正式发布和外部探针所需服务。"""
    integrations = IntegrationFactory.local(
        _ProcessRunner(),
        tmp_path / "objects",
        secret_root=tmp_path / "secrets",
        source_executable=tmp_path / "svn.exe",
        source_temp_root=tmp_path / "svn-work",
        source_credential=SecretRef("secret://file/svn-token"),
    )

    services = ProductionApplicationServices.create(
        integrations,
        tmp_path / "versions",
    )

    assert isinstance(services.object_store, FileSystemObjectStore)
    assert isinstance(services.unity_runner, UnityBatchRunner)
    assert isinstance(services.uploader, ReleaseObjectUploader)
    assert isinstance(services.verifier, RemoteReleaseVerifier)
    assert isinstance(services.activator, ReleaseActivator)
    assert services.formal_release is not None
    assert services.publish_release is not None
    assert services.preflight is not None
    assert services.probes is not None
