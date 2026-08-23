"""正式构建 application 的生产组合根。

本模块集中创建版本分配、对象上传、远端验证、CAS 激活、preflight 和外部探针服务。
它只接收 ``IntegrationFactory`` 已经装配的端口，不读取全局环境、不猜测远端地址，
也不在导入时创建目录或连接外部系统。缺少真实节点能力探针时，preflight 默认拒绝，
避免把开发替身误当成生产可用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from application.formal_release import FormalReleaseUseCase
from application.preflight import PreflightProbe, PreflightService
from application.publish_release import PublishReleaseUseCase
from integrations.factory import IntegrationFactory
from integrations.probes import ExternalProbeSuite
from ports.ci import CiJobClient
from ports.secrets import SecretProvider
from ports.source import SourceProvider
from ports.storage import ObjectStore
from release.activator import ReleaseActivator
from release.remote_verifier import RemoteReleaseVerifier
from release.uploader import ReleaseObjectUploader
from release.versioning import FileVersionStateStore, VersionAllocator
from integrations.unity import UnityBatchRunner


class _UnconfiguredPreflightProbe:
    """没有真实节点能力探针时的安全拒绝实现。"""

    def supports_platform(self, platform: object) -> bool:
        """拒绝未提供平台探针的生产运行。"""
        del platform
        return False

    def supports_toolchain(self, platform: object, unity_version: str) -> bool:
        """拒绝未提供工具链探针的生产运行。"""
        del platform, unity_version
        return False


@dataclass(frozen=True, slots=True)
class ProductionApplicationServices:
    """一次生产运行共享的 application 服务集合。

    参数：
        object_store/source_provider/unity_runner/ci_client/secret_provider: 已由外部
            ``IntegrationFactory`` 装配的端口实例。
        allocator/uploader/verifier/activator: 正式版本事务服务。
        formal_release/publish_release: 正式构建和发布 application 用例。
        preflight/probes: 启动前能力检查和外部系统探针。

    返回：
        无；通过 ``create`` 获得不可变服务集合。

    异常：
        组合依赖不完整或状态目录不是绝对路径时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        只在显式调用 ``create`` 时初始化本地版本状态根；不主动执行构建、上传或探针。
    """

    object_store: ObjectStore
    source_provider: SourceProvider | None
    unity_runner: UnityBatchRunner | None
    ci_client: CiJobClient | None
    secret_provider: SecretProvider
    allocator: VersionAllocator
    uploader: ReleaseObjectUploader
    verifier: RemoteReleaseVerifier
    activator: ReleaseActivator
    formal_release: FormalReleaseUseCase
    publish_release: PublishReleaseUseCase
    preflight: PreflightService
    probes: ExternalProbeSuite

    @classmethod
    def create(
        cls,
        integrations: IntegrationFactory,
        version_state_root: Path,
        *,
        max_upload_retries: int = 0,
        preflight_probe: PreflightProbe | None = None,
    ) -> "ProductionApplicationServices":
        """从唯一外部组合根创建完整 application 服务集合。

        参数：
            integrations: SVN、Unity、Jenkins、Secrets、Workspace 和 ObjectStore 端口集合。
            version_state_root: 版本流 JSON 状态根，必须是绝对路径。
            max_upload_retries: 上传临时失败的有限重试次数。
            preflight_probe: 真实平台/工具链只读探针；缺省时安全拒绝运行。

        返回：
            可供 CLI composition 使用的 ``ProductionApplicationServices``。

        异常：
            integrations 类型、对象存储能力或路径/重试参数非法时抛出异常。

        约束与副作用：
            仅创建版本状态目录；不会读取秘密、访问网络、执行 Unity 或触发发布。
        """
        if not isinstance(integrations, IntegrationFactory):
            raise TypeError("integrations 必须是 IntegrationFactory")
        if not isinstance(version_state_root, Path) or not version_state_root.is_absolute():
            raise ValueError("version_state_root 必须是绝对 Path")
        if not callable(getattr(integrations.object_store, "put", None)):
            raise TypeError("integrations.object_store 未实现 ObjectStore")
        allocator = VersionAllocator(FileVersionStateStore(version_state_root))
        uploader = ReleaseObjectUploader(
            integrations.object_store,
            max_retries=max_upload_retries,
        )
        verifier = RemoteReleaseVerifier(integrations.object_store)
        activator = ReleaseActivator(integrations.object_store)
        return cls(
            object_store=integrations.object_store,
            source_provider=integrations.source_provider,
            unity_runner=integrations.unity_runner,
            ci_client=integrations.ci_client,
            secret_provider=integrations.secret_provider,
            allocator=allocator,
            uploader=uploader,
            verifier=verifier,
            activator=activator,
            formal_release=FormalReleaseUseCase(allocator, uploader, verifier, activator),
            publish_release=PublishReleaseUseCase(uploader, verifier, activator),
            preflight=PreflightService(preflight_probe or _UnconfiguredPreflightProbe()),
            probes=ExternalProbeSuite(),
        )


__all__ = ["ProductionApplicationServices"]
