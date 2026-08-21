"""Lua 资源任务和源码/字节码/加密转换端口。

默认模式保留固定输入目录到 ``script/`` 的兼容行为；注入 ``LuaTransformer`` 后，
任务只负责构造不可变请求、校验精确输出和提交转换字节。编译器、加密策略和秘密
解析由端口实现，任务自身只携带类型化 ``SecretRef``，不接触明文密钥。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from configuration.model import SecretRef
from core.artifacts import ArtifactKind, ArtifactMetadata, LogicalArtifact
from core.manifest_codec import canonical_json_bytes
from core.tasks import ArtifactCollection, BuildContext, TaskPlan, TaskResult
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.task_base import _validate_logical_path
from resource.tasks.file_task import FileResourceTask


def _validate_text(value: object, field_name: str) -> str:
    """校验 Lua 请求中的非空、无控制字符文本。"""
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 必须是非空且无控制字符的字符串")
    return value


def _validate_optional_text(value: object, field_name: str) -> str | None:
    """校验允许为空的 Lua 配置文本。"""
    if value is None:
        return None
    return _validate_text(value, field_name)


def _validate_relative_entry(value: object, field_name: str) -> str:
    """校验脚本入口是安全的相对逻辑路径。"""
    entry = _validate_text(value, field_name)
    _validate_logical_path(entry)
    return entry


def _normalize_excludes(values: Iterable[str]) -> tuple[str, ...]:
    """校验并按 UTF-8 字节序规范化 Lua 排除规则。"""
    if isinstance(values, (str, bytes)):
        raise TypeError("exclude_patterns 必须是字符串可迭代对象，而不是单个字符串")
    try:
        items = tuple(values)
    except TypeError as exc:
        raise TypeError("exclude_patterns 必须是字符串可迭代对象") from exc
    if not all(isinstance(item, str) and item for item in items):
        raise ValueError("exclude_patterns 的每一项必须是非空字符串")
    if any(any(ord(char) < 0x20 for char in item) for item in items):
        raise ValueError("exclude_patterns 不得包含控制字符")
    return tuple(sorted(set(items), key=lambda item: item.encode("utf-8")))


class LuaBuildMode(Enum):
    """Lua 任务的三种稳定构建模式。"""

    SOURCE = "source"
    BYTECODE = "bytecode"
    ENCRYPTED = "encrypted"


@dataclass(frozen=True, slots=True)
class LuaTransformRequest:
    """绑定一次 Lua 转换的输入、模式、工具版本和秘密引用。

    参数：
        source_root: 已隔离且存在的 Lua 输入目录。
        output_prefix: 生成脚本使用的客户端逻辑输出前缀。
        mode: 原始源码、编译字节码或加密模式。
        hotfix_entry: 可选热更新入口，相对于 output_prefix 的逻辑路径。
        compiler_version: bytecode/encrypted 模式使用的编译器版本。
        encryption_strategy_version: encrypted 模式使用的加密策略版本。
        encryption_key_ref: encrypted 模式使用的类型化秘密引用，不是明文密钥。
        exclude_patterns: 由转换器解释的源码排除规则。

    返回：
        无；实例为不可变、可审计的转换请求。

    异常：
        模式、工具版本、入口、排除规则或秘密引用不满足组合约束时抛出
        ``TypeError`` / ``ValueError``。

    约束与副作用：
        只验证内存参数和输入目录，不读取脚本、不解析秘密、不启动编译器或加密器。
    """

    source_root: Path
    output_prefix: str
    mode: LuaBuildMode
    hotfix_entry: str | None
    compiler_version: str | None
    encryption_strategy_version: str | None
    encryption_key_ref: SecretRef | None
    exclude_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验 Lua 请求的路径、模式和加密秘密组合。"""
        if not isinstance(self.source_root, Path) or not self.source_root.is_absolute():
            raise ValueError("source_root 必须是绝对 Path")
        if not self.source_root.is_dir() or self.source_root.is_symlink():
            raise ValueError("source_root 必须是已存在的普通目录")
        _validate_relative_entry(self.output_prefix, "output_prefix")
        if not isinstance(self.mode, LuaBuildMode):
            raise TypeError("mode 必须是 LuaBuildMode")
        if self.hotfix_entry is not None:
            _validate_relative_entry(self.hotfix_entry, "hotfix_entry")
        compiler_version = _validate_optional_text(self.compiler_version, "compiler_version")
        strategy_version = _validate_optional_text(
            self.encryption_strategy_version, "encryption_strategy_version"
        )
        if self.encryption_key_ref is not None and not isinstance(
            self.encryption_key_ref, SecretRef
        ):
            raise TypeError("encryption_key_ref 必须是 SecretRef 或 None")
        if (
            self.mode in (LuaBuildMode.BYTECODE, LuaBuildMode.ENCRYPTED)
            and compiler_version is None
        ):
            raise ValueError("bytecode/encrypted 模式必须提供 compiler_version")
        if self.mode is LuaBuildMode.ENCRYPTED:
            if strategy_version is None:
                raise ValueError("encrypted 模式必须提供 encryption_strategy_version")
            if self.encryption_key_ref is None:
                raise ValueError("encrypted 模式必须提供 encryption_key_ref")
        elif strategy_version is not None or self.encryption_key_ref is not None:
            raise ValueError("非 encrypted 模式不得提供加密配置")
        object.__setattr__(self, "exclude_patterns", _normalize_excludes(self.exclude_patterns))


@dataclass(frozen=True, slots=True)
class LuaTransformOutput:
    """表示 Lua 转换器生成的一份逻辑路径和完整字节。"""

    logical_path: str
    content: bytes

    def __post_init__(self) -> None:
        """校验 Lua 产物逻辑路径和内容类型。"""
        _validate_logical_path(self.logical_path)
        if not isinstance(self.content, bytes):
            raise TypeError("content 必须是 bytes")


class LuaTransformer(Protocol):
    """Lua 源码筛选、编译和加密转换器的最小端口。"""

    def discover_outputs(self, request: LuaTransformRequest) -> tuple[str, ...]:
        """根据固定请求规划精确脚本输出，不生成或提交产物。"""
        ...

    def transform(self, request: LuaTransformRequest) -> tuple[LuaTransformOutput, ...]:
        """根据同一请求生成源码、字节码或加密 Lua 字节。"""
        ...


class LuaResourceTask(FileResourceTask):
    """生成 script/ 下的 Lua 文件或类型化转换产物。

    注入转换器时，规划和执行分别调用 ``discover_outputs`` 与 ``transform``，并拒绝
    输出集合漂移；未注入时仅允许 source 模式并复用文件任务的兼容行为。
    """

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        transformer: LuaTransformer | None = None,
        mode: LuaBuildMode = LuaBuildMode.SOURCE,
        hotfix_entry: str | None = None,
        compiler_version: str | None = None,
        encryption_strategy_version: str | None = None,
        encryption_key_ref: SecretRef | None = None,
        exclude_patterns: tuple[str, ...] = (),
        transformer_version: str = "1",
    ) -> None:
        """初始化 Lua 文件任务和可选的编译/加密转换器。

        参数：
            resource_input/source_root/blob_committer: 固定资源输入、隔离源码目录和 CAS。
            transformer: 可选 Lua 转换端口；省略时使用 source 文件兼容模式。
            mode: Lua 构建模式。
            hotfix_entry: 可选热更新入口相对路径。
            compiler_version: 编译器版本身份。
            encryption_strategy_version: 加密策略版本身份。
            encryption_key_ref: 加密秘密引用。
            exclude_patterns: 源码排除规则。
            transformer_version: 任务实现版本，参与身份摘要。

        返回：
            ``None``。

        异常：
            参数类型、模式组合或转换器能力不完整时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只保存任务配置，不读取源码、不获取秘密、不执行编译或加密。
        """
        if not isinstance(mode, LuaBuildMode):
            raise TypeError("mode 必须是 LuaBuildMode")
        if transformer is None and mode is not LuaBuildMode.SOURCE:
            raise ValueError("未提供 Lua transformer 时只能使用 source 模式")
        if transformer is None and (hotfix_entry is not None or exclude_patterns):
            raise ValueError("未提供 Lua transformer 时不得配置 hotfix_entry 或 exclude_patterns")
        if transformer is not None:
            if not callable(getattr(transformer, "discover_outputs", None)):
                raise TypeError("transformer.discover_outputs 必须可调用")
            if not callable(getattr(transformer, "transform", None)):
                raise TypeError("transformer.transform 必须可调用")
        _validate_text(transformer_version, "transformer_version")
        request = LuaTransformRequest(
            source_root,
            "script",
            mode,
            hotfix_entry,
            compiler_version,
            encryption_strategy_version,
            encryption_key_ref,
            exclude_patterns,
        )
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.LUA,
            name="lua",
            output_prefix="script",
            implementation_version=transformer_version,
        )
        self._transformer = transformer
        self._request_template = request
        self._transformer_version = transformer_version

    def _request(self, source_root: Path) -> LuaTransformRequest:
        """为当前任务和指定输入目录创建新的 Lua 转换请求。"""
        template = self._request_template
        return LuaTransformRequest(
            source_root,
            template.output_prefix,
            template.mode,
            template.hotfix_entry,
            template.compiler_version,
            template.encryption_strategy_version,
            template.encryption_key_ref,
            template.exclude_patterns,
        )

    @staticmethod
    def _validate_transform_outputs(
        outputs: Iterable[LuaTransformOutput],
    ) -> tuple[LuaTransformOutput, ...]:
        """校验转换器输出为 script/ 下按 UTF-8 排序的唯一集合。"""
        if not isinstance(outputs, tuple):
            raise TypeError("Lua 转换器输出必须是 tuple")
        normalized = tuple(outputs)
        if not normalized:
            raise ValueError("Lua 转换器至少需要生成一个输出")
        for output in normalized:
            if not isinstance(output, LuaTransformOutput):
                raise TypeError("Lua 转换器输出必须是 LuaTransformOutput")
            if not output.logical_path.startswith("script/"):
                raise ValueError("Lua 转换输出必须位于 script/ 前缀下")
        paths = tuple(output.logical_path for output in normalized)
        if len(set(paths)) != len(paths):
            raise ValueError("Lua 转换输出逻辑路径不得重复")
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("Lua 转换输出必须按 UTF-8 字节序排列")
        return normalized

    @staticmethod
    def _planned_transform_outputs(paths: tuple[str, ...]) -> tuple[str, ...]:
        """校验转换器规划返回的 Lua 逻辑路径元组。"""
        if not isinstance(paths, tuple):
            raise TypeError("Lua 转换器规划输出必须是 tuple[str, ...]")
        outputs = tuple(LuaTransformOutput(path, b"") for path in paths)
        return tuple(
            output.logical_path for output in LuaResourceTask._validate_transform_outputs(outputs)
        )

    def _validate_hotfix_output(self, paths: tuple[str, ...]) -> None:
        """确保声明的热更新入口确实属于当前精确输出集合。"""
        if self._request_template.hotfix_entry is None:
            return
        expected = f"script/{self._request_template.hotfix_entry}"
        if expected not in paths:
            raise ValueError(f"Lua hotfix 入口未出现在规划输出: {expected}")

    def discover_outputs(self, source_root: Path) -> tuple[str, ...]:
        """发现转换模式的精确 Lua 输出，或回退到目录文件发现。"""
        if self._transformer is None:
            return super().discover_outputs(source_root)
        outputs = self._transformer.discover_outputs(self._request(source_root))
        paths = self._planned_transform_outputs(outputs)
        self._validate_hotfix_output(paths)
        return paths

    def plan(self, context: BuildContext, source_root: Path | None = None) -> TaskPlan:
        """生成包含 Lua 模式、工具版本和排除规则的确定性任务计划。"""
        plan = super().plan(context, source_root)
        if self._transformer is None:
            return plan
        request = self._request_template
        key_digest = None
        if request.encryption_key_ref is not None:
            key_digest = hashlib.sha256(
                request.encryption_key_ref.reveal_locator().encode("utf-8")
            ).hexdigest()
        config_payload = {
            "kind": self.resource_kind.value,
            "mode": request.mode.value,
            "hotfix_entry": request.hotfix_entry,
            "compiler_version": request.compiler_version,
            "encryption_strategy_version": request.encryption_strategy_version,
            "encryption_key_ref_digest": key_digest,
            "exclude_patterns": list(request.exclude_patterns),
            "transformer_version": self._transformer_version,
        }
        return TaskPlan(
            plan.spec,
            plan.resolved_input_digest,
            hashlib.sha256(canonical_json_bytes(config_payload)).hexdigest(),
        )

    def build(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """执行 Lua 转换、拒绝输出漂移并把完整字节提交到 CAS。"""
        if self._transformer is None:
            return super().build(context, inputs)
        del inputs
        source_root = self.source_root
        if source_root is None:
            raise ValueError("Lua 任务缺少 source_root")
        request = self._request(source_root)
        planned_paths = self.discover_outputs(source_root)
        outputs = self._validate_transform_outputs(self._transformer.transform(request))
        actual_paths = tuple(output.logical_path for output in outputs)
        if actual_paths != planned_paths:
            raise ValueError("Lua 转换实际输出与规划输出不一致")
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
                            ("mode", request.mode.value),
                            ("transformer_version", self._transformer_version),
                        ),
                    ),
                )
            )
        return TaskResult(tuple(artifacts))


__all__ = [
    "LuaBuildMode",
    "LuaResourceTask",
    "LuaTransformOutput",
    "LuaTransformRequest",
    "LuaTransformer",
]
