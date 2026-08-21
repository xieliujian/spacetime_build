"""config 资源任务和类型化配置转换端口。

默认模式保留固定输入目录到 CAS 的兼容行为；注入 ``ConfigTransformer`` 后，任务
只负责构造请求、校验精确输出和提交转换字节，具体 Schema 编译、代码生成和 BIN
编码由外部端口实现。这样配置读取代码与 BIN 可以由同一个 Schema 快照绑定，且
任务本身不依赖 Unity 或项目专用转换器。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.artifacts import ArtifactKind, ArtifactMetadata, LogicalArtifact
from core.manifest_codec import canonical_json_bytes
from core.tasks import ArtifactCollection, BuildContext, TaskPlan, TaskResult
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.tasks.file_task import FileResourceTask
from resource.task_base import _validate_logical_path


def _validate_text(value: object, field_name: str) -> str:
    """校验转换请求中的非空单值文本。"""
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 必须是非空且无控制字符的字符串")
    return value


def _validate_output_prefix(value: object) -> str:
    """校验转换请求使用的相对逻辑输出前缀。"""
    prefix = _validate_text(value, "output_prefix")
    _validate_logical_path(prefix)
    return prefix.rstrip("/")


@dataclass(frozen=True, slots=True)
class ConfigTransformRequest:
    """绑定一次配置转换的输入目录、Schema 快照和调试开关。

    参数：
        source_root: 已隔离且存在的配置输入目录。
        output_prefix: 配置产物的客户端逻辑路径前缀。
        schema_snapshot_id: 生成读取代码、BIN 和 TXT 的同一 Schema 快照身份。
        emit_debug_text: 是否额外生成仅供排查的文本产物。

    返回：
        无；实例是不可变转换请求。

    异常：
        输入目录、前缀、Schema 身份或调试开关非法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        只保存请求，不读取目录、不启动转换器、不写入工作区。
    """

    source_root: Path
    output_prefix: str
    schema_snapshot_id: str
    emit_debug_text: bool

    def __post_init__(self) -> None:
        """校验配置转换请求的路径、身份和开关不变量。"""
        if not isinstance(self.source_root, Path) or not self.source_root.is_absolute():
            raise ValueError("source_root 必须是绝对 Path")
        if not self.source_root.is_dir() or self.source_root.is_symlink():
            raise ValueError("source_root 必须是已存在的普通目录")
        _validate_output_prefix(self.output_prefix)
        _validate_text(self.schema_snapshot_id, "schema_snapshot_id")
        if not isinstance(self.emit_debug_text, bool):
            raise TypeError("emit_debug_text 必须是 bool")


@dataclass(frozen=True, slots=True)
class ConfigTransformOutput:
    """表示配置转换器生成的一份逻辑路径和完整字节。"""

    logical_path: str
    content: bytes

    def __post_init__(self) -> None:
        """校验配置产物逻辑路径和内容类型。"""
        _validate_logical_path(self.logical_path)
        if not isinstance(self.content, bytes):
            raise TypeError("content 必须是 bytes")


class ConfigTransformer(Protocol):
    """配置 Schema 转换器的最小纯 Python 端口。"""

    def discover_outputs(self, request: ConfigTransformRequest) -> tuple[str, ...]:
        """根据固定请求枚举精确逻辑输出，不生成或提交产物。"""
        ...

    def transform(self, request: ConfigTransformRequest) -> tuple[ConfigTransformOutput, ...]:
        """根据同一请求生成配置代码、BIN 和可选 TXT 字节。"""
        ...


class ConfigResourceTask(FileResourceTask):
    """生成 config/ 下的确定性配置文件或 Schema 转换产物。

    注入转换器时，规划和执行分别调用同一端口的 ``discover_outputs`` 与
    ``transform``，并拒绝输出集合漂移；未注入时复用 ``FileResourceTask`` 的兼容
    文件模式，供现有 fixture 和迁移前的简单输入继续使用。
    """

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        transformer: ConfigTransformer | None = None,
        emit_debug_text: bool = False,
        transformer_version: str = "1",
    ) -> None:
        """初始化 config 文件任务和可选的 Schema 转换器。"""
        if transformer is not None:
            if not callable(getattr(transformer, "discover_outputs", None)):
                raise TypeError("transformer.discover_outputs 必须可调用")
            if not callable(getattr(transformer, "transform", None)):
                raise TypeError("transformer.transform 必须可调用")
        if not isinstance(emit_debug_text, bool):
            raise TypeError("emit_debug_text 必须是 bool")
        _validate_text(transformer_version, "transformer_version")
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.CONFIG,
            name="config",
            output_prefix="config",
        )
        self._transformer = transformer
        self._emit_debug_text = emit_debug_text
        self._transformer_version = transformer_version

    def _request(self, source_root: Path) -> ConfigTransformRequest:
        """为指定输入目录创建绑定当前资源快照的转换请求。"""
        return ConfigTransformRequest(
            source_root=source_root,
            output_prefix="config",
            schema_snapshot_id=self.resource_input.resource_snapshot_id,
            emit_debug_text=self._emit_debug_text,
        )

    @staticmethod
    def _validate_transform_outputs(
        outputs: Iterable[ConfigTransformOutput],
    ) -> tuple[ConfigTransformOutput, ...]:
        """校验转换器输出为按 UTF-8 字节序排列的唯一配置路径集合。"""
        if not isinstance(outputs, tuple):
            raise TypeError("转换器输出必须是 tuple")
        normalized = tuple(outputs)
        if not normalized:
            raise ValueError("配置转换器至少需要生成一个输出")
        for output in normalized:
            if not isinstance(output, ConfigTransformOutput):
                raise TypeError("转换器输出必须是 ConfigTransformOutput")
            if not output.logical_path.startswith("config/"):
                raise ValueError("配置转换输出必须位于 config/ 前缀下")
        paths = tuple(output.logical_path for output in normalized)
        if len(set(paths)) != len(paths):
            raise ValueError("配置转换输出逻辑路径不得重复")
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("配置转换输出必须按 UTF-8 字节序排列")
        return normalized

    @staticmethod
    def _planned_transform_outputs(paths: tuple[str, ...]) -> tuple[str, ...]:
        """校验转换器规划返回的逻辑路径元组。"""
        if not isinstance(paths, tuple):
            raise TypeError("转换器规划输出必须是 tuple[str, ...]")
        outputs = tuple(ConfigTransformOutput(path, b"") for path in paths)
        return tuple(
            output.logical_path
            for output in ConfigResourceTask._validate_transform_outputs(outputs)
        )

    def discover_outputs(self, source_root: Path) -> tuple[str, ...]:
        """发现配置转换模式的精确输出，或回退到目录文件发现。"""
        if self._transformer is None:
            return super().discover_outputs(source_root)
        outputs = self._transformer.discover_outputs(self._request(source_root))
        return self._planned_transform_outputs(outputs)

    def plan(self, context: BuildContext, source_root: Path | None = None) -> TaskPlan:
        """生成包含调试开关和转换模式的确定性配置任务计划。"""
        plan = super().plan(context, source_root)
        if self._transformer is None:
            return plan
        config_payload = {
            "kind": self.resource_kind.value,
            "rule_version": self.resource_input.rule_version,
            "mode": "transformer",
            "emit_debug_text": self._emit_debug_text,
            "schema_snapshot_id": self.resource_input.resource_snapshot_id,
            "transformer_version": self._transformer_version,
        }

        return TaskPlan(
            plan.spec,
            plan.resolved_input_digest,
            hashlib.sha256(canonical_json_bytes(config_payload)).hexdigest(),
        )

    def build(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """执行配置转换、校验精确输出并把每份字节提交到 CAS。"""
        if self._transformer is None:
            return super().build(context, inputs)
        del inputs
        source_root = self.source_root
        if source_root is None:
            raise ValueError("config 任务缺少 source_root")
        request = self._request(source_root)
        planned_paths = self.discover_outputs(source_root)
        outputs = self._validate_transform_outputs(self._transformer.transform(request))
        actual_paths = tuple(output.logical_path for output in outputs)
        if actual_paths != planned_paths:
            raise ValueError("配置转换实际输出与规划输出不一致")
        artifacts: list[LogicalArtifact] = []
        for output in outputs:
            blob = self._blob_committer.commit_bytes(output.content)
            artifacts.append(
                LogicalArtifact(
                    logical_path=output.logical_path,
                    kind=ArtifactKind.FILE,
                    blob=blob,
                    dependencies=(),
                    subpackage_ids=frozenset(),
                    metadata=ArtifactMetadata(
                        source_task=self.name,
                        source_revision=context.revision,
                        toolchain_digest=context.toolchain_digest,
                        attributes=(
                            ("platform", self.resource_input.platform.value),
                            ("variant", self.resource_input.variant.value),
                            ("rule_version", self.resource_input.rule_version),
                            ("schema_snapshot_id", self.resource_input.resource_snapshot_id),
                        ),
                    ),
                )
            )
        return TaskResult(tuple(artifacts))


__all__ = [
    "ConfigResourceTask",
    "ConfigTransformOutput",
    "ConfigTransformRequest",
    "ConfigTransformer",
]
