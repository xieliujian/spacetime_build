"""particle 资源任务和类型化粒子构建端口。

粒子任务显式消费 Shader Bundle，custom FBX 名单和依赖清理规则进入 builder 请求；
依赖清理由版本化 builder 实现，任务只负责边界和确定性身份。
"""

from __future__ import annotations

from pathlib import Path

from core.artifacts import LogicalArtifact
from core.tasks import ArtifactCollection
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.tasks.unity_asset import (
    UnityAssetBuildOutput,
    UnityAssetBuildRequest,
    UnityAssetBuilder,
    UnityAssetResourceTask,
)

ParticleBuildRequest = UnityAssetBuildRequest
ParticleBuildOutput = UnityAssetBuildOutput
ParticleBuilder = UnityAssetBuilder


class ParticleResourceTask(UnityAssetResourceTask):
    """生成 particle/ 下的粒子 Bundle 和索引。"""

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        builder: ParticleBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        custom_fbx: tuple[str, ...] = (),
        dependency_cleanup_version: str = "1",
        implementation_version: str = "1",
    ) -> None:
        """初始化粒子任务并固定 FBX 与依赖清理规则。"""
        if any(not isinstance(value, str) or not value for value in custom_fbx):
            raise ValueError("custom_fbx 必须只包含非空字符串")
        if len(set(custom_fbx)) != len(custom_fbx):
            raise ValueError("custom_fbx 不得重复")
        if not isinstance(dependency_cleanup_version, str) or not dependency_cleanup_version:
            raise ValueError("dependency_cleanup_version 必须是非空字符串")
        settings = (("dependency_cleanup_version", dependency_cleanup_version),)
        if custom_fbx:
            settings += (("custom_fbx", ",".join(custom_fbx)),)
        active_settings = (
            settings
            if builder is not None or custom_fbx or dependency_cleanup_version != "1"
            else ()
        )
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.PARTICLE,
            name="particle",
            output_prefix="particle",
            operation_name="build_particle",
            builder=builder,
            output_root=output_root,
            operation_arguments=operation_arguments,
            settings=active_settings,
            implementation_version=implementation_version,
        )

    def select_inputs(self, inputs: ArtifactCollection) -> tuple[LogicalArtifact, ...]:
        """选择并校验 Shader Bundle 显式输入。"""
        if not isinstance(inputs, ArtifactCollection):
            raise TypeError("inputs 必须是 ArtifactCollection")
        values = tuple(inputs.as_mapping().values())
        if not values:
            raise ValueError("particle 缺少 shader_bundle 输入")
        if any(
            not item.logical_path.startswith("depend/shader_")
            or item.metadata.source_task != "shader_bundle"
            for item in values
        ):
            raise ValueError("particle 输入必须全部来自 shader_bundle")
        return tuple(sorted(values, key=lambda item: item.logical_path.encode("utf-8")))


__all__ = [
    "ParticleBuildOutput",
    "ParticleBuildRequest",
    "ParticleBuilder",
    "ParticleResourceTask",
]
