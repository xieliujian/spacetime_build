"""texture 资源任务和类型化贴图构建端口。

贴图规则把压缩 profile、Spine 版本和多语言名单写入 builder 请求与任务配置摘要；
独立贴图、公共贴图和差异输入由 ``TextureBuilder`` 在隔离工程中处理。
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

TextureBuildRequest = UnityAssetBuildRequest
TextureBuildOutput = UnityAssetBuildOutput
TextureBuilder = UnityAssetBuilder


def _validate_list(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    """校验贴图规则中的稳定字符串名单。"""
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise ValueError(f"{field_name} 必须是非空字符串 tuple")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} 不得重复")
    return values


class TextureResourceTask(UnityAssetResourceTask):
    """生成 texture/ 下的贴图 Bundle 和索引。"""

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        builder: TextureBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        texture_profile: str = "default",
        spine_version: str | None = None,
        languages: tuple[str, ...] = (),
        implementation_version: str = "1",
    ) -> None:
        """初始化贴图任务并固定压缩、Spine 和多语言规则。"""
        if not isinstance(texture_profile, str) or not texture_profile:
            raise ValueError("texture_profile 必须是非空字符串")
        if spine_version is not None and (not isinstance(spine_version, str) or not spine_version):
            raise ValueError("spine_version 必须是非空字符串或 None")
        languages = _validate_list(languages, "languages") if languages else ()
        settings = (("texture_profile", texture_profile),)
        if spine_version is not None:
            settings += (("spine_version", spine_version),)
        if languages:
            settings += (("languages", ",".join(languages)),)
        active_settings = (
            settings
            if builder is not None or texture_profile != "default" or spine_version or languages
            else ()
        )
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.TEXTURE,
            name="texture",
            output_prefix="texture",
            operation_name="build_texture",
            builder=builder,
            output_root=output_root,
            operation_arguments=operation_arguments,
            settings=active_settings,
            implementation_version=implementation_version,
        )


__all__ = [
    "TextureBuildOutput",
    "TextureBuildRequest",
    "TextureBuilder",
    "TextureResourceTask",
]
