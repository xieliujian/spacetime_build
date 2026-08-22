"""scene 资源任务和 Unity 场景构建端口。

任务显式消费已登记的 ``depend/shader_*`` 产物，不把该输入偷渡为任务依赖；Shader
产物的逻辑路径、Blob 摘要和大小会进入场景任务输入摘要。Unity 工程、分组规则和
场景 Bundle 格式由 ``SceneBuilder`` 端口提供；资源任务只负责请求构造、输出边界、
Unity Manifest 依赖校验和 CAS 提交。本期正式资源范围不包含低清变体或源工程反写。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.artifacts import ArtifactKind, ArtifactMetadata, LogicalArtifact
from core.manifest_codec import canonical_json_bytes
from core.tasks import ArtifactCollection, BuildContext, TaskPlan, TaskResult, TaskSpec
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.task_base import _validate_logical_path
from resource.tasks.file_task import FileResourceTask
from resource.unity_operations import UnityOperation, UnityProjectRole

_SHADER_PREFIX = "depend/shader_"
_SCENE_PREFIX = "scene/"


def _validate_output_root(value: object) -> Path:
    """校验 Unity 场景输出根是绝对普通目录路径。

    参数：
        value: 待校验的输出根。

    返回：
        通过校验的绝对 ``Path``。

    异常：
        路径不是绝对路径，或已存在对象不是普通目录时抛出 ``ValueError``。

    约束与副作用：
        只检查路径元数据，不创建目录、不启动 Unity。
    """
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError("output_root 必须是绝对 Path")
    if value.exists() and (not value.is_dir() or value.is_symlink()):
        raise ValueError("output_root 必须是普通目录或待创建路径")
    return value


def _ensure_under_root(path: Path, root: Path) -> Path:
    """解析场景输出并拒绝越出 Unity 输出根。

    参数：
        path: builder 返回的文件路径。
        root: 任务授权的 Unity 输出根。

    返回：
        解析后的路径，用于后续普通文件检查。

    异常：
        输出等于根目录或解析后位于根目录外时抛出 ``ValueError``。

    约束与副作用：
        只解析路径，不创建、删除或写入文件。
    """
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ValueError(f"scene 输出越出 output_root: {path}")
    return resolved_path


@dataclass(frozen=True, slots=True)
class SceneBuildRequest:
    """绑定一次场景构建的 Shader 输入、操作和隔离输出根。

    参数：
        resource_input: 固定源码、资源快照、平台、变体和规则身份。
        shader_inputs: 已登记的 Shader Bundle 产物，按逻辑路径排序。
        source_root: 已隔离的场景输入目录。
        output_root: Unity 生成文件的隔离输出根，可在执行时创建。
        operation: 固定的 ``build_scene`` 资源工程操作。

    返回：
        无；实例是不可变 Unity 构建请求。

    异常：
        输入、目录或操作身份非法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        只保存请求，不读取输入、不创建目录、不启动 Unity。
    """

    resource_input: ResourceBuildInput
    shader_inputs: tuple[LogicalArtifact, ...]
    source_root: Path
    output_root: Path
    operation: UnityOperation

    def __post_init__(self) -> None:
        """校验 Shader 上游、目录和场景操作身份。"""
        if not isinstance(self.resource_input, ResourceBuildInput):
            raise TypeError("resource_input 必须是 ResourceBuildInput")
        if not isinstance(self.shader_inputs, tuple) or not self.shader_inputs:
            raise ValueError("shader_inputs 必须是非空 tuple")
        for artifact in self.shader_inputs:
            if not isinstance(artifact, LogicalArtifact):
                raise TypeError("shader_inputs 必须全部是 LogicalArtifact")
            if not artifact.logical_path.startswith(_SHADER_PREFIX):
                raise ValueError("scene 只能消费 depend/shader_* 输入")
            if artifact.metadata.source_task != "shader_bundle":
                raise ValueError("scene 输入必须来自 shader_bundle 任务")
        paths = tuple(item.logical_path for item in self.shader_inputs)
        if len(set(paths)) != len(paths):
            raise ValueError("shader_inputs 逻辑路径不得重复")
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("shader_inputs 必须按 UTF-8 字节序排列")
        if not isinstance(self.source_root, Path) or not self.source_root.is_absolute():
            raise ValueError("source_root 必须是绝对 Path")
        if not self.source_root.is_dir() or self.source_root.is_symlink():
            raise ValueError("source_root 必须是已存在的普通目录")
        _validate_output_root(self.output_root)
        if not isinstance(self.operation, UnityOperation):
            raise TypeError("operation 必须是 UnityOperation")
        if self.operation.name != "build_scene":
            raise ValueError("scene 操作必须是 build_scene")
        if self.operation.project_role is not UnityProjectRole.RESOURCE:
            raise ValueError("scene 操作必须使用资源工程")


@dataclass(frozen=True, slots=True)
class SceneBuildOutput:
    """表示 Unity 生成的一份场景输出及其有序 Bundle 依赖。

    参数：
        logical_path: 客户端逻辑输出路径，必须位于 ``scene/``。
        path: Unity 输出根内的绝对普通文件路径。
        dependencies: Unity Manifest 提供的有序依赖逻辑路径；重复项必须保留。

    返回：
        无；通过字段读取不可变输出记录。

    异常：
        逻辑路径、依赖、文件路径类型非法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        只做内存校验，不读取文件；依赖顺序不被排序或去重。
    """

    logical_path: str
    path: Path
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验场景输出逻辑路径、依赖和绝对文件路径类型。"""
        _validate_logical_path(self.logical_path)
        if not self.logical_path.startswith(_SCENE_PREFIX):
            raise ValueError("scene 输出必须位于 scene/ 前缀")
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("scene 输出 path 必须是绝对 Path")
        if not isinstance(self.dependencies, tuple):
            raise TypeError("scene dependencies 必须是 tuple[str, ...]")
        for dependency in self.dependencies:
            _validate_logical_path(dependency)


class SceneBuilder(Protocol):
    """场景规划与 Unity 执行端口。"""

    def plan(self, request: SceneBuildRequest) -> tuple[str, ...]:
        """根据固定请求规划精确场景输出，不执行 Unity。"""
        ...

    def build(self, request: SceneBuildRequest) -> tuple[SceneBuildOutput, ...]:
        """执行场景构建并返回已生成的精确文件输出。"""
        ...


class SceneResourceTask(FileResourceTask):
    """生成场景 Bundle 和索引，并拥有独占的 ``scene/`` 输出前缀。

    注入 builder 时，任务只接收显式 Shader Bundle 产物，规划和执行分别调用
    ``plan`` 与 ``build``，并拒绝输出集合漂移、路径逃逸、缺失文件和非法依赖；
    未注入时复用文件任务兼容模式。场景任务不执行低清变换，也不反写源工程。
    """

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        builder: SceneBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        implementation_version: str = "1",
    ) -> None:
        """初始化场景文件任务和可选 Unity builder。

        参数：
            resource_input/source_root/blob_committer: 固定资源输入、场景源码目录和 CAS。
            builder: 可选场景规划/执行端口；省略时使用文件任务兼容模式。
            output_root: Unity 场景输出隔离根；使用 builder 时必须提供。
            operation_arguments: 进入 UnityOperation 的确定性参数对。
            implementation_version: 任务实现版本，参与任务身份。

        返回：
            ``None``。

        异常：
            参数、操作参数或 builder 能力不完整时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只保存配置，不读取场景、不创建输出目录、不启动 Unity。
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
        if any(key == "platform" for key, _value in operation_arguments):
            raise ValueError("platform 参数由 ResourceBuildInput 固定，不得覆盖")
        operation = UnityOperation(
            name="build_scene",
            project_role=UnityProjectRole.RESOURCE,
            arguments=(("platform", resource_input.platform.value),) + operation_arguments,
            expected_output_roots=("scene",),
        )
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.SCENE,
            name="scene",
            output_prefix="scene",
            implementation_version=implementation_version,
        )
        self._builder = builder
        self._operation = operation
        self._output_root = output_root

    @staticmethod
    def _shader_inputs(inputs: ArtifactCollection) -> tuple[LogicalArtifact, ...]:
        """从显式输入集合提取并校验 Shader Bundle 产物。

        参数：
            inputs: 调用方显式传入的逻辑产物集合。

        返回：
            按逻辑路径 UTF-8 字节序排列的 Shader 产物元组。

        异常：
            集合为空、包含非 Shader 输入或来源任务错误时抛出 ``ValueError``。

        约束与副作用：
            只读集合；不读取 Blob locator，不触发其他任务。
        """
        if not isinstance(inputs, ArtifactCollection):
            raise TypeError("inputs 必须是 ArtifactCollection")
        mapping = inputs.as_mapping()
        if not mapping:
            raise ValueError("scene 缺少显式 shader 输入")
        if any(not path.startswith(_SHADER_PREFIX) for path in mapping):
            raise ValueError("scene 输入集合只能包含 shader_bundle 产物")
        shaders = tuple(mapping.values())
        if any(item.metadata.source_task != "shader_bundle" for item in shaders):
            raise ValueError("scene 输入必须来自 shader_bundle 任务")
        return tuple(sorted(shaders, key=lambda item: item.logical_path.encode("utf-8")))

    @staticmethod
    def _validate_planned_outputs(paths: tuple[str, ...]) -> tuple[str, ...]:
        """校验 builder 规划返回的唯一 ``scene/`` 逻辑路径。

        参数：
            paths: builder 返回的规划输出。

        返回：
            原样返回且已验证的路径元组。

        异常：
            输出为空、重复、未排序或越出 ``scene/`` 时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            纯内存校验；不读取输出文件，不写 CAS。
        """
        if not isinstance(paths, tuple) or not paths:
            raise ValueError("scene 至少需要一个规划输出")
        for path in paths:
            if not isinstance(path, str):
                raise TypeError("scene 规划输出必须是 str")
            _validate_logical_path(path)
            if not path.startswith(_SCENE_PREFIX):
                raise ValueError("scene 输出必须位于 scene/ 前缀")
        if len(set(paths)) != len(paths):
            raise ValueError("scene 规划输出不得重复")
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("scene 规划输出必须按 UTF-8 字节序排列")
        return paths

    def _request(self, inputs: ArtifactCollection) -> SceneBuildRequest:
        """按显式输入集合创建一次场景构建请求。"""
        if self._builder is None or self._output_root is None:
            raise ValueError("scene 未配置 builder request")
        if self.source_root is None:
            raise ValueError("scene 缺少 source_root")
        return SceneBuildRequest(
            self.resource_input,
            self._shader_inputs(inputs),
            self.source_root,
            self._output_root,
            self._operation,
        )

    def discover_outputs(self, source_root: Path) -> tuple[str, ...]:
        """发现文件兼容模式输出；builder 模式需结合显式输入规划。"""
        if self._builder is not None:
            raise ValueError("scene builder 模式必须通过 plan_with_inputs 规划")
        return super().discover_outputs(source_root)

    def plan(
        self,
        context: BuildContext,
        source_root: Path | ArtifactCollection | None = None,
    ) -> TaskPlan:
        """规划场景任务；builder 模式要求显式传入 Shader 产物集合。"""
        if self._builder is not None:
            if not isinstance(source_root, ArtifactCollection):
                raise ValueError("scene builder 模式必须显式提供 shader 输入")
            return self.plan_with_inputs(context, source_root)
        if source_root is not None and not isinstance(source_root, Path):
            raise TypeError("source_root 必须是 Path 或 None")
        return super().plan(context, source_root)

    def plan_with_inputs(
        self,
        context: BuildContext,
        inputs: ArtifactCollection,
        source_root: Path | None = None,
    ) -> TaskPlan:
        """生成绑定 Shader 输入摘要和 ``build_scene`` 操作的任务计划。"""
        if self._builder is None:
            return super().plan_with_inputs(context, inputs, source_root)
        request = self._request(inputs)
        paths = self._validate_planned_outputs(self._builder.plan(request))
        input_payload = [
            {
                "logical_path": artifact.logical_path,
                "sha256": artifact.blob.sha256,
                "size": artifact.blob.size,
            }
            for artifact in request.shader_inputs
        ]
        resolved_input_digest = hashlib.sha256(
            canonical_json_bytes(
                {
                    "source_snapshot_id": self.resource_input.source_snapshot_id,
                    "resource_snapshot_id": self.resource_input.resource_snapshot_id,
                    "shader_inputs": input_payload,
                }
            )
        ).hexdigest()
        config_payload = {
            "operation": request.operation.name,
            "project_role": request.operation.project_role.value,
            "arguments": [list(pair) for pair in request.operation.arguments],
            "expected_output_roots": list(request.operation.expected_output_roots),
            "rule_version": self.resource_input.rule_version,
        }
        config_digest = hashlib.sha256(canonical_json_bytes(config_payload)).hexdigest()
        if not isinstance(context, BuildContext):
            raise TypeError("context 必须是 BuildContext")
        spec = TaskSpec(
            self.name,
            (),
            frozenset(paths),
            self._implementation_version,
            (
                ("kind", self.resource_kind.value),
                ("platform", self.resource_input.platform.value),
                ("variant", self.resource_input.variant.value),
            ),
        )
        return TaskPlan(spec, resolved_input_digest, config_digest)

    def build(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """执行场景构建、校验 Unity 依赖并提交精确 CAS 产物。"""
        if self._builder is None:
            return super().build(context, inputs)
        request = self._request(inputs)
        planned_paths = self._validate_planned_outputs(self._builder.plan(request))
        outputs = self._builder.build(request)
        if not isinstance(outputs, tuple):
            raise TypeError("scene builder 输出必须是 tuple")
        actual_paths = tuple(output.logical_path for output in outputs)
        for output in outputs:
            if not isinstance(output, SceneBuildOutput):
                raise TypeError("scene builder 输出类型非法")
            _validate_logical_path(output.logical_path)
            if not output.logical_path.startswith(_SCENE_PREFIX):
                raise ValueError("scene 输出必须位于 scene/ 前缀")
            _ensure_under_root(output.path, request.output_root)
            if output.path.is_symlink() or not output.path.resolve(strict=False).is_file():
                raise FileNotFoundError(f"scene 输出不是普通文件: {output.path}")
            if not isinstance(output.dependencies, tuple):
                raise TypeError("scene dependencies 必须是 tuple[str, ...]")
            for dependency in output.dependencies:
                _validate_logical_path(dependency)
        if actual_paths != planned_paths:
            raise ValueError("scene 实际输出与规划输出不一致")
        # 先完成所有文件、逻辑路径和 Manifest 依赖校验，再调用 CAS，避免留下部分产物。
        artifacts: list[LogicalArtifact] = []
        for output in outputs:
            blob = self._blob_committer.commit(output.path, allowed_root=request.output_root)
            kind = (
                ArtifactKind.ASSET_BUNDLE
                if output.logical_path.endswith(".assetbundle")
                else ArtifactKind.FILE
            )
            artifacts.append(
                LogicalArtifact(
                    logical_path=output.logical_path,
                    kind=kind,
                    blob=blob,
                    dependencies=output.dependencies,
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
                            ("shader_input_count", str(len(request.shader_inputs))),
                        ),
                    ),
                )
            )
        return TaskResult(tuple(artifacts))


__all__ = [
    "SceneBuildOutput",
    "SceneBuildRequest",
    "SceneBuilder",
    "SceneResourceTask",
]
