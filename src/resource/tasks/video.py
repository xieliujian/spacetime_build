"""video 资源任务和类型化视频构建端口。

视频源文件名单、编码策略和大小校验由 ``VideoBuilder`` 执行；任务提前拒绝大小写
不敏感重名，并将选择规则纳入确定性任务配置摘要。
"""

from __future__ import annotations

from pathlib import Path

from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.tasks.unity_asset import (
    UnityAssetBuildOutput,
    UnityAssetBuildRequest,
    UnityAssetBuilder,
    UnityAssetResourceTask,
)

VideoBuildRequest = UnityAssetBuildRequest
VideoBuildOutput = UnityAssetBuildOutput
VideoBuilder = UnityAssetBuilder


def _validate_names(values: tuple[str, ...]) -> tuple[str, ...]:
    """校验视频源名单且拒绝大小写不敏感重名。"""
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise ValueError("source_names 必须是字符串 tuple")
    folded = [value.casefold() for value in values]
    if len(set(folded)) != len(folded):
        raise ValueError("video source_names 存在大小写不敏感重复项")
    return values


class VideoResourceTask(UnityAssetResourceTask):
    """生成 video/ 下的视频包和索引。"""

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        builder: VideoBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        source_names: tuple[str, ...] = (),
        encoding_profile: str = "copy",
        implementation_version: str = "1",
    ) -> None:
        """初始化视频任务并固定源文件选择与编码规则。"""
        source_names = _validate_names(source_names)
        if not isinstance(encoding_profile, str) or not encoding_profile:
            raise ValueError("encoding_profile 必须是非空字符串")
        settings = (("encoding_profile", encoding_profile),)
        if source_names:
            settings += (("source_names", ",".join(source_names)),)
        active_settings = (
            settings if builder is not None or source_names or encoding_profile != "copy" else ()
        )
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.VIDEO,
            name="video",
            output_prefix="video",
            operation_name="build_video",
            builder=builder,
            output_root=output_root,
            operation_arguments=operation_arguments,
            settings=active_settings,
            implementation_version=implementation_version,
        )


__all__ = [
    "VideoBuildOutput",
    "VideoBuildRequest",
    "VideoBuilder",
    "VideoResourceTask",
]
