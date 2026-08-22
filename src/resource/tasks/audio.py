"""audio 资源任务和类型化音频构建端口。

音频 bank 名单、大小写规则和编码策略进入确定性 builder 请求；缺 bank 或大小写冲突
由 builder 在隔离工作区报告，任务在构造阶段先拒绝不可判别的名单。
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

AudioBuildRequest = UnityAssetBuildRequest
AudioBuildOutput = UnityAssetBuildOutput
AudioBuilder = UnityAssetBuilder


def _validate_names(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    """校验大小写不敏感资源名单。"""
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise ValueError(f"{field_name} 必须是字符串 tuple")
    folded = [value.casefold() for value in values]
    if len(set(folded)) != len(folded):
        raise ValueError(f"{field_name} 存在大小写不敏感重复项")
    return values


class AudioResourceTask(UnityAssetResourceTask):
    """生成 audio/ 下的音频包和索引。"""

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        builder: AudioBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        bank_names: tuple[str, ...] = (),
        encoding_profile: str = "default",
        implementation_version: str = "1",
    ) -> None:
        """初始化音频任务并固定 bank 名单和编码规则。"""
        bank_names = _validate_names(bank_names, "bank_names")
        if not isinstance(encoding_profile, str) or not encoding_profile:
            raise ValueError("encoding_profile 必须是非空字符串")
        settings = (("encoding_profile", encoding_profile),)
        if bank_names:
            settings += (("bank_names", ",".join(bank_names)),)
        active_settings = (
            settings if builder is not None or bank_names or encoding_profile != "default" else ()
        )
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.AUDIO,
            name="audio",
            output_prefix="audio",
            operation_name="build_audio",
            builder=builder,
            output_root=output_root,
            operation_arguments=operation_arguments,
            settings=active_settings,
            implementation_version=implementation_version,
        )


__all__ = [
    "AudioBuildOutput",
    "AudioBuildRequest",
    "AudioBuilder",
    "AudioResourceTask",
]
