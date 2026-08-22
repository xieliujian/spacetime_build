"""UI 资源任务和类型化 UI 构建端口。

UI 任务要求图集或字体等显式输入，并把图集规则、字体名单和多语言名单纳入稳定
builder 请求。图集缓存必须由外部 builder 按规则和工具版本生成，不在任务中猜测。
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

UiBuildRequest = UnityAssetBuildRequest
UiBuildOutput = UnityAssetBuildOutput
UiBuilder = UnityAssetBuilder


class UiResourceTask(UnityAssetResourceTask):
    """生成 ui/ 下的 UI Bundle、图集和索引。"""

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        builder: UiBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        atlas_version: str = "1",
        fonts: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        implementation_version: str = "1",
    ) -> None:
        """初始化 UI 任务并固定图集、字体和多语言规则。"""
        if not isinstance(atlas_version, str) or not atlas_version:
            raise ValueError("atlas_version 必须是非空字符串")
        if any(not isinstance(value, str) or not value for value in fonts + languages):
            raise ValueError("fonts 和 languages 必须只包含非空字符串")
        if len(set(fonts)) != len(fonts) or len(set(languages)) != len(languages):
            raise ValueError("fonts 和 languages 不得重复")
        settings = (("atlas_version", atlas_version),)
        if fonts:
            settings += (("fonts", ",".join(fonts)),)
        if languages:
            settings += (("languages", ",".join(languages)),)
        active_settings = (
            settings if builder is not None or atlas_version != "1" or fonts or languages else ()
        )
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.UI,
            name="ui",
            output_prefix="ui",
            operation_name="build_ui",
            builder=builder,
            output_root=output_root,
            operation_arguments=operation_arguments,
            settings=active_settings,
            implementation_version=implementation_version,
        )

    def select_inputs(self, inputs: ArtifactCollection) -> tuple[LogicalArtifact, ...]:
        """选择图集、贴图或字体任务显式输入，并拒绝无来源产物。"""
        if not isinstance(inputs, ArtifactCollection):
            raise TypeError("inputs 必须是 ArtifactCollection")
        values = tuple(inputs.as_mapping().values())
        if not values:
            raise ValueError("ui 缺少 atlas/font 输入")
        accepted_tasks = {"atlas", "texture", "font", "ui"}
        if any(item.metadata.source_task not in accepted_tasks for item in values):
            raise ValueError("ui 输入必须来自 atlas、texture、font 或 ui 任务")
        return tuple(sorted(values, key=lambda item: item.logical_path.encode("utf-8")))


UIResourceTask = UiResourceTask

__all__ = ["UIResourceTask", "UiBuildOutput", "UiBuildRequest", "UiBuilder", "UiResourceTask"]
