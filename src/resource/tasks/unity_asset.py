"""通用 Unity 资源 builder 任务基类。

本模块把场景之外的七类正式资源任务统一接入类型化 ``UnityOperation``、显式输入、
隔离输出和 CAS。具体任务只负责声明输入来源与规则设置，Unity 工程、分组、压缩、
编码和索引生成由 ``UnityAssetBuilder`` 端口实现；任务本身不拼命令行、不写源 SVN，
也不把显式输入转成 ``TaskSpec.dependencies``。
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


def _validate_output_root(value: object) -> Path:
    """校验 Unity 任务输出根是绝对普通目录。

    参数：
        value: 待校验的输出根路径。

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
    """解析 builder 输出并拒绝越出授权输出根。

    参数：
        path: builder 返回的文件路径。
        root: 任务授权的输出根。

    返回：
        解析后的普通路径候选。

    异常：
        输出等于根目录或解析后越出根目录时抛出 ``ValueError``。

    约束与副作用：
        只解析路径，不创建、删除或写入文件。
    """
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ValueError(f"资源输出越出 output_root: {path}")
    return resolved_path


def _validate_settings(settings: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    """校验并规范资源规则设置。

    参数：
        settings: 任务规则设置键值对。

    返回：
        按 UTF-8 键值稳定排序的设置元组。

    异常：
        非元组、重复键或含换行值时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        纯函数；不包含秘密原文，不访问文件系统。
    """
    if not isinstance(settings, tuple):
        raise TypeError("settings 必须是 tuple[tuple[str, str], ...]")
    keys: set[str] = set()
    for pair in settings:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError("settings 每项必须是 (key, value) 元组")
        key, value = pair
        if not isinstance(key, str) or not key or any(char in key for char in "\\/\r\n"):
            raise ValueError("settings key 必须是安全的非空字符串")
        if not isinstance(value, str) or any(char in value for char in "\r\n"):
            raise ValueError("settings value 必须是无换行字符串")
        if key in keys:
            raise ValueError(f"settings 存在重复 key: {key!r}")
        keys.add(key)
    return tuple(
        sorted(settings, key=lambda pair: (pair[0].encode("utf-8"), pair[1].encode("utf-8")))
    )


@dataclass(frozen=True, slots=True)
class UnityAssetBuildRequest:
    """绑定一次 Unity 资源构建的显式输入、操作和规则设置。

    参数：
        resource_input: 固定源码、资源快照、平台、变体和规则身份。
        explicit_inputs: 调用方登记并显式传入的上游产物，按逻辑路径排序。
        source_root: 已隔离的资源输入目录。
        output_root: Unity 生成文件的隔离输出根，可执行时创建。
        operation: 固定的资源工程操作。
        settings: 已解析的资源规则键值对，参与任务配置摘要。

    返回：
        无；实例是不可变 builder 请求。

    异常：
        输入、路径、操作或设置不合法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        只保存请求，不读取 Blob、不创建目录、不启动 Unity。
    """

    resource_input: ResourceBuildInput
    explicit_inputs: tuple[LogicalArtifact, ...]
    source_root: Path
    output_root: Path
    operation: UnityOperation
    settings: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """校验请求的输入顺序、路径、操作和规则设置。"""
        if not isinstance(self.resource_input, ResourceBuildInput):
            raise TypeError("resource_input 必须是 ResourceBuildInput")
        if not isinstance(self.explicit_inputs, tuple):
            raise TypeError("explicit_inputs 必须是 tuple[LogicalArtifact, ...]")
        paths: list[str] = []
        for artifact in self.explicit_inputs:
            if not isinstance(artifact, LogicalArtifact):
                raise TypeError("explicit_inputs 必须全部是 LogicalArtifact")
            paths.append(artifact.logical_path)
        if len(set(paths)) != len(paths):
            raise ValueError("explicit_inputs 逻辑路径不得重复")
        if tuple(paths) != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("explicit_inputs 必须按 UTF-8 字节序排列")
        if not isinstance(self.source_root, Path) or not self.source_root.is_absolute():
            raise ValueError("source_root 必须是绝对 Path")
        if not self.source_root.is_dir() or self.source_root.is_symlink():
            raise ValueError("source_root 必须是已存在的普通目录")
        _validate_output_root(self.output_root)
        if not isinstance(self.operation, UnityOperation):
            raise TypeError("operation 必须是 UnityOperation")
        object.__setattr__(self, "settings", _validate_settings(self.settings))


@dataclass(frozen=True, slots=True)
class UnityAssetBuildOutput:
    """表示 Unity 生成的一份资源文件及其有序依赖。

    参数：
        logical_path: 客户端逻辑输出路径。
        path: 输出根内的绝对文件路径。
        dependencies: Unity Manifest 提供的有序依赖逻辑路径，保留重复项。
        artifact_kind: 可选产物类型；省略时按 ``.assetbundle`` 后缀推导。

    返回：
        无；通过字段读取不可变输出记录。

    异常：
        路径、依赖或产物类型非法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        只做内存校验，不读取文件、不提交 CAS。
    """

    logical_path: str
    path: Path
    dependencies: tuple[str, ...] = ()
    artifact_kind: ArtifactKind | None = None

    def __post_init__(self) -> None:
        """校验资源输出逻辑路径、依赖和产物类型。"""
        _validate_logical_path(self.logical_path)
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("资源输出 path 必须是绝对 Path")
        if not isinstance(self.dependencies, tuple):
            raise TypeError("dependencies 必须是 tuple[str, ...]")
        for dependency in self.dependencies:
            _validate_logical_path(dependency)
        if self.artifact_kind is not None and not isinstance(self.artifact_kind, ArtifactKind):
            raise TypeError("artifact_kind 必须是 ArtifactKind 或 None")


class UnityAssetBuilder(Protocol):
    """七类资源共用的 Unity 规划与执行端口。"""

    def plan(self, request: UnityAssetBuildRequest) -> tuple[str, ...]:
        """根据固定请求规划精确输出，不执行 Unity。"""
        ...

    def build(self, request: UnityAssetBuildRequest) -> tuple[UnityAssetBuildOutput, ...]:
        """执行资源构建并返回精确文件输出。"""
        ...


class UnityAssetResourceTask(FileResourceTask):
    """把一个资源任务接入通用 Unity builder 和 CAS 输出边界。

    子类只需声明资源种类、操作名、输出前缀并覆盖 ``select_inputs``；公共实现负责
    生成确定性输入摘要、严格输出校验、依赖保序和原子边界内的 Blob 提交。未注入
    builder 时保留 FileResourceTask 的固定目录兼容模式。
    """

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        kind: ResourceKind,
        name: str,
        output_prefix: str,
        operation_name: str,
        builder: UnityAssetBuilder | None = None,
        output_root: Path | None = None,
        operation_arguments: tuple[tuple[str, str], ...] = (),
        settings: tuple[tuple[str, str], ...] = (),
        implementation_version: str = "1",
    ) -> None:
        """初始化通用 Unity 资源任务。

        参数：
            resource_input/source_root/blob_committer: 固定输入、源码目录和 CAS。
            kind/name/output_prefix: 资源种类、任务名和客户端输出前缀。
            operation_name: 类型化 Unity 操作名。
            builder: 可选 Unity 规划/执行端口；缺省为文件兼容模式。
            output_root: Unity 隔离输出根；使用 builder 时必须提供。
            operation_arguments: 进入 UnityOperation 的非平台参数。
            settings: 资源规则设置，进入 TaskPlan.config_digest。
            implementation_version: 任务实现版本。

        返回：
            ``None``。

        异常：
            参数或 builder 能力不完整时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只保存任务配置，不读取输入、不创建目录、不启动 Unity。
        """
        if not isinstance(operation_name, str) or not operation_name:
            raise ValueError("operation_name 必须是非空字符串")
        if builder is None and output_root is not None:
            raise ValueError("未提供 builder 时不得提供 output_root")
        if builder is None and (operation_arguments or settings):
            raise ValueError("未提供 builder 时不得配置 Unity 操作或规则设置")
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
            name=operation_name,
            project_role=UnityProjectRole.RESOURCE,
            arguments=(("platform", resource_input.platform.value),) + operation_arguments,
            expected_output_roots=(output_prefix,),
        )
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=kind,
            name=name,
            output_prefix=output_prefix,
            implementation_version=implementation_version,
        )
        self._builder = builder
        self._operation = operation
        self._output_root = output_root
        self._settings = _validate_settings(settings)

    def select_inputs(self, inputs: ArtifactCollection) -> tuple[LogicalArtifact, ...]:
        """选择并排序当前任务的显式上游输入。

        参数：
            inputs: 调用方显式传入的产物集合。

        返回：
            按逻辑路径 UTF-8 字节序排列的输入；默认允许空集合。

        异常：
            输入类型非法时抛出 ``TypeError``。

        约束与副作用：
            只读集合；具体任务通过覆盖本方法声明来源和数量约束。
        """
        if not isinstance(inputs, ArtifactCollection):
            raise TypeError("inputs 必须是 ArtifactCollection")
        return tuple(
            sorted(inputs.as_mapping().values(), key=lambda item: item.logical_path.encode("utf-8"))
        )

    def _request(self, inputs: ArtifactCollection) -> UnityAssetBuildRequest:
        """由显式输入构造一次统一资源 builder 请求。"""
        if self._builder is None or self._output_root is None:
            raise ValueError("资源任务未配置 builder request")
        if self.source_root is None:
            raise ValueError("资源任务缺少 source_root")
        return UnityAssetBuildRequest(
            self.resource_input,
            self.select_inputs(inputs),
            self.source_root,
            self._output_root,
            self._operation,
            self._settings,
        )

    def _validate_planned_outputs(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        """校验 builder 规划返回的唯一任务输出。

        参数：
            paths: builder 返回的逻辑输出路径。

        返回：
            原样返回且已验证的路径元组。

        异常：
            输出为空、重复、未排序或越出任务前缀时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            纯内存校验；不读取文件、不写 CAS。
        """
        if not isinstance(paths, tuple) or not paths:
            raise ValueError(f"{self.name} 至少需要一个规划输出")
        for path in paths:
            if not isinstance(path, str):
                raise TypeError(f"{self.name} 规划输出必须是 str")
            _validate_logical_path(path)
            if not path.startswith(f"{self._output_prefix}/"):
                raise ValueError(f"{self.name} 输出必须位于 {self._output_prefix}/ 前缀")
        if len(set(paths)) != len(paths):
            raise ValueError(f"{self.name} 规划输出不得重复")
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError(f"{self.name} 规划输出必须按 UTF-8 字节序排列")
        return paths

    def _input_digest(self, request: UnityAssetBuildRequest) -> str:
        """计算包含显式 Blob 身份的确定性输入摘要。"""
        payload = {
            "source_snapshot_id": self.resource_input.source_snapshot_id,
            "resource_snapshot_id": self.resource_input.resource_snapshot_id,
            "explicit_inputs": [
                {
                    "logical_path": item.logical_path,
                    "sha256": item.blob.sha256,
                    "size": item.blob.size,
                }
                for item in request.explicit_inputs
            ],
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    def _config_digest(self, request: UnityAssetBuildRequest) -> str:
        """计算包含操作、平台和资源设置的确定性配置摘要。"""
        payload = {
            "operation": request.operation.name,
            "project_role": request.operation.project_role.value,
            "arguments": [list(pair) for pair in request.operation.arguments],
            "expected_output_roots": list(request.operation.expected_output_roots),
            "settings": [list(pair) for pair in request.settings],
            "rule_version": self.resource_input.rule_version,
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    def discover_outputs(self, source_root: Path) -> tuple[str, ...]:
        """发现文件兼容模式输出；builder 模式必须结合显式输入规划。"""
        if self._builder is not None:
            raise ValueError(f"{self.name} builder 模式必须通过 plan_with_inputs 规划")
        return super().discover_outputs(source_root)

    def plan(
        self,
        context: BuildContext,
        source_root: Path | ArtifactCollection | None = None,
    ) -> TaskPlan:
        """规划资源任务；builder 模式要求显式输入集合。"""
        if self._builder is not None:
            if not isinstance(source_root, ArtifactCollection):
                raise ValueError(f"{self.name} builder 模式必须显式提供输入集合")
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
        """生成绑定显式输入、Unity 操作和规则摘要的任务计划。"""
        if self._builder is None:
            return super().plan_with_inputs(context, inputs, source_root)
        if not isinstance(context, BuildContext):
            raise TypeError("context 必须是 BuildContext")
        request = self._request(inputs)
        paths = self._validate_planned_outputs(self._builder.plan(request))
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
        return TaskPlan(spec, self._input_digest(request), self._config_digest(request))

    def _resolve_artifact_kind(self, output: UnityAssetBuildOutput) -> ArtifactKind:
        """解析 builder 输出对应的领域产物类型。"""
        if output.artifact_kind is not None:
            return output.artifact_kind
        return (
            ArtifactKind.ASSET_BUNDLE
            if output.logical_path.endswith(".assetbundle")
            else ArtifactKind.FILE
        )

    def build(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """执行 builder、校验所有输出并提交 CAS 产物。"""
        if self._builder is None:
            return super().build(context, inputs)
        request = self._request(inputs)
        planned_paths = self._validate_planned_outputs(self._builder.plan(request))
        outputs = self._builder.build(request)
        if not isinstance(outputs, tuple):
            raise TypeError(f"{self.name} builder 输出必须是 tuple")
        actual_paths = tuple(output.logical_path for output in outputs)
        for output in outputs:
            if not isinstance(output, UnityAssetBuildOutput):
                raise TypeError(f"{self.name} builder 输出类型非法")
            _validate_logical_path(output.logical_path)
            if not output.logical_path.startswith(f"{self._output_prefix}/"):
                raise ValueError(f"{self.name} 输出必须位于 {self._output_prefix}/ 前缀")
            resolved_path = _ensure_under_root(output.path, request.output_root)
            if output.path.is_symlink() or not resolved_path.is_file():
                raise FileNotFoundError(f"{self.name} 输出不是普通文件: {output.path}")
            for dependency in output.dependencies:
                _validate_logical_path(dependency)
        if actual_paths != planned_paths:
            raise ValueError(f"{self.name} 实际输出与规划输出不一致")
        # 所有输出边界和依赖校验完成后才写 CAS，避免非法结果留下部分登记。
        artifacts: list[LogicalArtifact] = []
        for output in outputs:
            blob = self._blob_committer.commit(output.path, allowed_root=request.output_root)
            artifacts.append(
                LogicalArtifact(
                    logical_path=output.logical_path,
                    kind=self._resolve_artifact_kind(output),
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
                            ("explicit_input_count", str(len(request.explicit_inputs))),
                        ),
                    ),
                )
            )
        return TaskResult(tuple(artifacts))


__all__ = [
    "UnityAssetBuildOutput",
    "UnityAssetBuildRequest",
    "UnityAssetBuilder",
    "UnityAssetResourceTask",
]
