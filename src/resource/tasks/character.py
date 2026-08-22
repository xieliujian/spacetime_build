"""character 资源任务和类型化角色构建端口。

角色任务把裁剪输入固定在隔离 ``source_root``，并要求显式传入 config 与 Shader Bundle
产物。模型、动画、材质和角色索引由 ``CharacterBuilder`` 处理，任务不反写源工程。
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

CharacterBuildRequest = UnityAssetBuildRequest
CharacterBuildOutput = UnityAssetBuildOutput
CharacterBuilder = UnityAssetBuilder


class CharacterResourceTask(UnityAssetResourceTask):
    """生成 character/ 下的角色 Bundle 和索引。"""

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        builder: CharacterBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        crop_profile: str | None = None,
        implementation_version: str = "1",
    ) -> None:
        """初始化角色任务和裁剪规则设置。"""
        settings = () if crop_profile is None else (("crop_profile", crop_profile),)
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.CHARACTER,
            name="character",
            output_prefix="character",
            operation_name="build_character",
            builder=builder,
            output_root=output_root,
            operation_arguments=operation_arguments,
            settings=settings,
            implementation_version=implementation_version,
        )

    def select_inputs(self, inputs: ArtifactCollection) -> tuple[LogicalArtifact, ...]:
        """要求 config 与 shader_bundle 两类显式输入并稳定排序。"""
        if not isinstance(inputs, ArtifactCollection):
            raise TypeError("inputs 必须是 ArtifactCollection")
        values = tuple(inputs.as_mapping().values())
        source_tasks = {item.metadata.source_task for item in values}
        if not {"config", "shader_bundle"}.issubset(source_tasks):
            raise ValueError("character 必须同时提供 config 和 shader_bundle 输入")
        if any(
            item.metadata.source_task == "config" and not item.logical_path.startswith("config/")
            for item in values
        ):
            raise ValueError("character 的 config 输入路径必须位于 config/")
        if any(
            item.metadata.source_task == "shader_bundle"
            and not item.logical_path.startswith("depend/shader_")
            for item in values
        ):
            raise ValueError("character 的 Shader 输入路径必须位于 depend/shader_/")
        if any(item.metadata.source_task not in {"config", "shader_bundle"} for item in values):
            raise ValueError("character 输入来源不受支持")
        return tuple(sorted(values, key=lambda item: item.logical_path.encode("utf-8")))


__all__ = [
    "CharacterBuildOutput",
    "CharacterBuildRequest",
    "CharacterBuilder",
    "CharacterResourceTask",
]
