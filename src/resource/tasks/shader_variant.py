"""Shader variant 资源任务和 Unity 变体收集端口。

默认模式保留固定输入目录到 ``depend/shader_variant/`` 的兼容行为；注入
``ShaderVariantBuilder`` 后，任务只负责构造类型化 ``collect_variant`` 操作、校验
Unity 输出并提交 CAS。Unity 进程、工程脚本和变体文件格式由端口实现，资源任务
不直接拼接命令行或访问具体工具。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.artifacts import ArtifactKind, ArtifactMetadata, LogicalArtifact
from core.manifest_codec import canonical_json_bytes
from core.tasks import ArtifactCollection, BuildContext, TaskPlan, TaskResult
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.task_base import _validate_logical_path
from resource.tasks.file_task import FileResourceTask
from resource.unity_operations import UnityOperation, UnityProjectRole


def _validate_text(value: object, field_name: str) -> str:
    """校验 Unity 任务参数是非空且无控制字符文本。"""
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 必须是非空且无控制字符的字符串")
    return value


def _validate_output_root(value: object) -> Path:
    """校验 Unity 输出根是绝对普通路径对象。"""
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError("output_root 必须是绝对 Path")
    if value.exists() and (not value.is_dir() or value.is_symlink()):
        raise ValueError("output_root 必须是普通目录或待创建路径")
    return value


def _ensure_under_root(path: Path, root: Path) -> Path:
    """解析输出文件并拒绝越出 Unity 输出根。"""
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ValueError(f"Shader variant 输出越出 output_root: {path}")
    return resolved_path


@dataclass(frozen=True, slots=True)
class ShaderVariantBuildRequest:
    """绑定一次 Shader variant 收集的输入、操作和输出根。

    参数：
        resource_input: 固定源码、资源快照、平台、变体和规则身份。
        source_root: 已隔离的 Shader 输入目录。
        output_root: Unity 生成文件的隔离输出根，可在执行时创建。
        operation: 固定的 ``collect_variant`` Shader 工程操作。

    返回：
        无；实例是不可变 Unity 构建请求。

    异常：
        路径、资源输入或操作身份非法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        只保存请求，不读取输入、不创建目录、不启动 Unity。
    """

    resource_input: ResourceBuildInput
    source_root: Path
    output_root: Path
    operation: UnityOperation

    def __post_init__(self) -> None:
        """校验 variant 请求的目录、操作名称和 Shader 工程角色。"""
        if not isinstance(self.resource_input, ResourceBuildInput):
            raise TypeError("resource_input 必须是 ResourceBuildInput")
        if not isinstance(self.source_root, Path) or not self.source_root.is_absolute():
            raise ValueError("source_root 必须是绝对 Path")
        if not self.source_root.is_dir() or self.source_root.is_symlink():
            raise ValueError("source_root 必须是已存在的普通目录")
        _validate_output_root(self.output_root)
        if not isinstance(self.operation, UnityOperation):
            raise TypeError("operation 必须是 UnityOperation")
        if self.operation.name != "collect_variant":
            raise ValueError("Shader variant 操作必须是 collect_variant")
        if self.operation.project_role is not UnityProjectRole.SHADER:
            raise ValueError("Shader variant 操作必须使用 Shader 工程")


@dataclass(frozen=True, slots=True)
class ShaderVariantBuildOutput:
    """表示 Unity 变体收集器生成的一份逻辑路径和文件路径。"""

    logical_path: str
    path: Path

    def __post_init__(self) -> None:
        """校验变体输出的逻辑路径和绝对普通文件路径类型。"""
        _validate_logical_path(self.logical_path)
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("Shader variant 输出 path 必须是绝对 Path")


class ShaderVariantBuilder(Protocol):
    """Shader variant 规划与 Unity 执行端口。"""

    def plan(self, request: ShaderVariantBuildRequest) -> tuple[str, ...]:
        """根据固定请求规划精确变体输出，不执行 Unity。"""
        ...

    def build(self, request: ShaderVariantBuildRequest) -> tuple[ShaderVariantBuildOutput, ...]:
        """执行变体收集并返回已生成的精确文件输出。"""
        ...


class ShaderVariantResourceTask(FileResourceTask):
    """生成 Shader variant 清单的文件或 Unity 收集产物。

    注入 builder 时，规划和执行分别调用 ``plan`` 与 ``build``，并拒绝输出集合漂移、
    路径逃逸和缺失文件；未注入时复用文件任务兼容模式。
    """

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        builder: ShaderVariantBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        implementation_version: str = "1",
    ) -> None:
        """初始化 Shader variant 文件任务和可选 Unity builder。

        参数：
            resource_input/source_root/blob_committer: 固定资源输入、Shader 源码目录和 CAS。
            builder: 可选变体规划/执行端口；省略时使用文件任务兼容模式。
            output_root: Unity 变体输出隔离根，使用 builder 时必须提供。
            operation_arguments: 进入 UnityOperation 的确定性参数对。
            implementation_version: 任务实现版本，参与任务身份。

        返回：
            ``None``。

        异常：
            参数、操作参数或 builder 能力不完整时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只保存配置，不读取 Shader、不创建输出目录、不启动 Unity。
        """
        if not isinstance(implementation_version, str) or not implementation_version:
            raise ValueError("implementation_version 必须是非空字符串")
        if builder is None and output_root is not None:
            raise ValueError("未提供 builder 时不得提供 output_root")
        if builder is None and operation_arguments:
            raise ValueError("未提供 builder 时不得配置 operation_arguments")
        if builder is not None:
            if output_root is None:
                raise ValueError("提供 builder 时必须提供 output_root")
            if not callable(getattr(builder, "plan", None)):
                raise TypeError("builder.plan 必须可调用")
            if not callable(getattr(builder, "build", None)):
                raise TypeError("builder.build 必须可调用")
        operation = UnityOperation(
            name="collect_variant",
            project_role=UnityProjectRole.SHADER,
            arguments=operation_arguments,
            expected_output_roots=("shader_variant",),
        )
        request = (
            ShaderVariantBuildRequest(resource_input, source_root, output_root, operation)
            if builder is not None and output_root is not None
            else None
        )
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.SHADER_VARIANT,
            name="shader_variant",
            output_prefix="depend/shader_variant",
            implementation_version=implementation_version,
        )
        self._builder = builder
        self._request_template = request

    def _request(self, source_root: Path) -> ShaderVariantBuildRequest:
        """按当前输入目录创建 Shader variant 构建请求。"""
        template = self._request_template
        if template is None:
            raise ValueError("Shader variant 未配置 builder request")
        return ShaderVariantBuildRequest(
            template.resource_input,
            source_root,
            template.output_root,
            template.operation,
        )

    @staticmethod
    def _validate_planned_outputs(paths: tuple[str, ...]) -> tuple[str, ...]:
        """校验 builder 规划返回的唯一 shader variant 逻辑路径。"""
        if not isinstance(paths, tuple):
            raise TypeError("Shader variant 规划输出必须是 tuple[str, ...]")
        if not paths:
            raise ValueError("Shader variant 至少需要一个规划输出")
        for path in paths:
            if not isinstance(path, str):
                raise TypeError("Shader variant 规划输出必须是 str")
            _validate_logical_path(path)
            if not path.startswith("depend/shader_variant/"):
                raise ValueError("Shader variant 输出必须位于 depend/shader_variant/ 前缀下")
        if len(set(paths)) != len(paths):
            raise ValueError("Shader variant 规划输出不得重复")
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("Shader variant 规划输出必须按 UTF-8 字节序排列")
        return paths

    def discover_outputs(self, source_root: Path) -> tuple[str, ...]:
        """发现 Unity variant 模式的精确输出，或回退到目录文件发现。"""
        if self._builder is None:
            return super().discover_outputs(source_root)
        paths = self._validate_planned_outputs(self._builder.plan(self._request(source_root)))
        return paths

    def plan(self, context: BuildContext, source_root: Path | None = None) -> TaskPlan:
        """生成包含 collect_variant 操作和规则版本的确定性任务计划。"""
        plan = super().plan(context, source_root)
        if self._builder is None:
            return plan
        request = self._request_template
        if request is None:
            raise ValueError("Shader variant 缺少构建请求")
        config_payload = {
            "operation": request.operation.name,
            "project_role": request.operation.project_role.value,
            "arguments": [list(pair) for pair in request.operation.arguments],
            "expected_output_roots": list(request.operation.expected_output_roots),
            "rule_version": self.resource_input.rule_version,
        }
        return TaskPlan(
            plan.spec,
            plan.resolved_input_digest,
            hashlib.sha256(canonical_json_bytes(config_payload)).hexdigest(),
        )

    def build(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """执行 Unity variant 收集、校验输出并把文件提交到 CAS。"""
        if self._builder is None:
            return super().build(context, inputs)
        del inputs
        source_root = self.source_root
        if source_root is None:
            raise ValueError("Shader variant 缺少 source_root")
        request = self._request(source_root)
        planned_paths = self.discover_outputs(source_root)
        outputs = self._builder.build(request)
        if not isinstance(outputs, tuple):
            raise TypeError("Shader variant builder 输出必须是 tuple")
        actual_paths = tuple(output.logical_path for output in outputs)
        for output in outputs:
            if not isinstance(output, ShaderVariantBuildOutput):
                raise TypeError("Shader variant builder 输出类型非法")
            if not output.logical_path.startswith("depend/shader_variant/"):
                raise ValueError("Shader variant 输出必须位于 depend/shader_variant/ 前缀下")
            _ensure_under_root(output.path, request.output_root)
        if actual_paths != planned_paths:
            raise ValueError("Shader variant 实际输出与规划输出不一致")
        artifacts: list[LogicalArtifact] = []
        for output in outputs:
            blob = self._blob_committer.commit(output.path, allowed_root=request.output_root)
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
                            ("operation", request.operation.name),
                        ),
                    ),
                )
            )
        return TaskResult(tuple(artifacts))


__all__ = [
    "ShaderVariantBuildOutput",
    "ShaderVariantBuildRequest",
    "ShaderVariantBuilder",
    "ShaderVariantResourceTask",
]
