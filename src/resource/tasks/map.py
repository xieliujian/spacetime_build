"""map 资源任务和类型化地图构建端口。

地图任务要求调用方显式传入 config 产物；Unity 地图分组、数据打包和索引生成由
``MapBuilder`` 注入，任务只负责输入来源、操作身份、输出所有权和 CAS 边界。
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

MapBuildRequest = UnityAssetBuildRequest
MapBuildOutput = UnityAssetBuildOutput
MapBuilder = UnityAssetBuilder


class MapResourceTask(UnityAssetResourceTask):
    """生成 map/ 下的地图 Bundle 和索引。"""

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        builder: MapBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        implementation_version: str = "1",
    ) -> None:
        """初始化地图任务和可选 MapBuilder。"""
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.MAP,
            name="map",
            output_prefix="map",
            operation_name="build_map",
            builder=builder,
            output_root=output_root,
            operation_arguments=operation_arguments,
            implementation_version=implementation_version,
        )

    def select_inputs(self, inputs: ArtifactCollection) -> tuple[LogicalArtifact, ...]:
        """选择唯一来源为 config/ 的显式输入。"""
        if not isinstance(inputs, ArtifactCollection):
            raise TypeError("inputs 必须是 ArtifactCollection")
        values = tuple(inputs.as_mapping().values())
        if not values:
            raise ValueError("map 缺少 config 输入")
        if any(
            not item.logical_path.startswith("config/") or item.metadata.source_task != "config"
            for item in values
        ):
            raise ValueError("map 输入必须全部来自 config 任务")
        return tuple(sorted(values, key=lambda item: item.logical_path.encode("utf-8")))


__all__ = ["MapBuildOutput", "MapBuildRequest", "MapBuilder", "MapResourceTask"]
