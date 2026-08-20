"""资源任务的公共计划、输出所有权和执行契约。

本模块把资源任务接入现有 ``core.tasks`` 契约，同时保留资源任务自己的固定输入
和精确输出发现。第一期任务不建立工具内置依赖，所有 ``TaskSpec.dependencies``
固定为空；Jenkins 或应用层若需传递数据，必须显式传入 ``ArtifactCollection``。
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

from core.artifacts import LogicalArtifact
from core.manifest_codec import canonical_json_bytes
from core.tasks import ArtifactCollection, BuildContext, TaskPlan, TaskResult, TaskSpec
from resource.model import ResourceBuildInput, ResourceKind


def _validate_logical_path(path: str) -> None:
    """校验资源任务声明的客户端逻辑路径。

    参数：
        path: 使用 ``/`` 的相对逻辑路径。

    返回：
        ``None``，表示路径可由 ``LogicalArtifact`` 接收。

    异常：
        空段、点段、反斜杠或绝对路径会抛出 ``ValueError``。

    约束与副作用：
        纯函数；不访问本地文件系统。
    """
    if not isinstance(path, str) or not path or path.startswith("/"):
        raise ValueError(f"非法逻辑路径: {path!r}")
    if len(path) > 1 and path[1] == ":":
        raise ValueError(f"逻辑路径不得为 Windows 绝对路径: {path!r}")
    parts = path.split("/")
    if "\\" in path or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"非法逻辑路径: {path!r}")


def _digest_payload(value: object) -> str:
    """为任务计划字段计算确定性 SHA256。

    参数：
        value: 可规范 JSON 编码的计划值。

    返回：
        64 位小写 SHA256 字符串。

    异常：
        输入不可规范编码时透传 ``TypeError`` / ``ValueError``。

    约束与副作用：
        纯函数；不包含运行态 build ID 或本地绝对路径。
    """
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class ResourceBuildTask(ABC):
    """所有正式版本资源任务共享的抽象基类。

    职责：
        校验固定资源输入，发现精确输出，生成无依赖 ``TaskPlan``，并把实际构建
        委托给 ``build``。基类不调用 Unity、不访问对象存储，也不提交版本控制。

    参数：
        resource_input: 固定源码、资源快照、平台、变体和规则身份。
        kind: 资源任务种类。
        name: 任务名，必须与任务注册名一致。
        implementation_version: 参与任务身份的实现版本。

    返回：
        子类通过 ``discover_outputs`` 和 ``build`` 提供具体行为。

    异常：
        输入和输出契约非法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        规划阶段只读；``TaskSpec.dependencies`` 永远为空，避免工具层偷渡 DAG。
    """

    def __init__(
        self,
        *,
        resource_input: ResourceBuildInput,
        kind: ResourceKind,
        name: str,
        implementation_version: str,
        source_root: Path | None = None,
    ) -> None:
        """保存并校验资源任务公共身份。

        参数：
            resource_input: 固定资源输入模型。
            kind: 资源种类。
            name: 稳定任务名。
            implementation_version: 非空实现版本。

        返回：
            ``None``。

        异常：
            参数类型或名称非法时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            仅保存不可变引用，不创建目录、不读取输入。
        """
        if not isinstance(resource_input, ResourceBuildInput):
            raise TypeError("resource_input 必须是 ResourceBuildInput")
        if not isinstance(kind, ResourceKind):
            raise TypeError("kind 必须是 ResourceKind")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            raise ValueError("name 必须是非空任务名")
        if not isinstance(implementation_version, str) or not implementation_version:
            raise ValueError("implementation_version 必须是非空字符串")
        self._resource_input = resource_input
        self._kind = kind
        self._name = name
        self._implementation_version = implementation_version
        if source_root is not None and (not source_root.is_absolute() or not source_root.is_dir()):
            raise ValueError("source_root 必须是已存在的绝对目录")
        self._source_root = source_root

    @property
    def name(self) -> str:
        """返回任务逻辑名。

        返回：
            与 ``TaskSpec.name`` 相同的稳定任务名。

        异常：
            无。

        约束与副作用：
            只读属性，不产生 I/O。
        """
        return self._name

    @property
    def resource_input(self) -> ResourceBuildInput:
        """返回固定资源输入。

        返回：
            不可变 ``ResourceBuildInput``。

        异常：
            无。

        约束与副作用：
            只读属性，不暴露可变状态。
        """
        return self._resource_input

    @property
    def resource_kind(self) -> ResourceKind:
        """返回资源任务种类。

        返回：
            构造时固定的 ``ResourceKind``。

        异常：
            无。

        约束与副作用：
            只读属性，不产生 I/O。
        """
        return self._kind

    @property
    def source_root(self) -> Path | None:
        """返回可选的固定输入目录。

        返回：
            任务构造时绑定的绝对目录，或 ``None`` 表示由调用方传入。

        异常：
            无。

        约束与副作用：
            只读属性；路径不会进入 BuildManifest 身份。
        """
        return self._source_root

    @abstractmethod
    def discover_outputs(self, source_root: Path) -> tuple[str, ...]:
        """根据固定输入只读发现精确客户端输出。

        参数：
            source_root: 已隔离且固定的资源输入目录。

        返回：
            按确定性顺序排列的逻辑路径元组。

        异常：
            缺输入或输出规则非法时抛出 ``FileNotFoundError`` / ``ValueError``。

        约束与副作用：
            只能读取 source_root，禁止创建输出和调用外部工具。
        """
        raise NotImplementedError

    @abstractmethod
    def build(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """执行任务特定构建步骤。

        参数：
            context: 共享构建上下文。
            inputs: 调用方显式提供的上游产物集合。

        返回：
            含实际 ``LogicalArtifact`` 的 ``TaskResult``。

        异常：
            工具失败、输入缺失或产物验证失败时抛出业务异常。

        约束与副作用：
            不得触发版本控制、Jenkins 或发布；外部工具通过 ports 传入。
        """
        raise NotImplementedError

    def plan(self, context: BuildContext, source_root: Path | None = None) -> TaskPlan:
        """创建精确输出且无任务依赖的 ``TaskPlan``。

        参数：
            context: 共享构建上下文。
            source_root: 固定资源输入目录；省略时使用构造任务绑定的目录。

        返回：
            输出集合与输入/规则摘要绑定的不可变计划。

        异常：
            source_root 非绝对目录、输出重复或逻辑路径非法时抛出 ``ValueError``。

        约束与副作用：
            只调用 ``discover_outputs``；不写文件、不启动工具。
        """
        if not isinstance(context, BuildContext):
            raise TypeError("context 必须是 BuildContext")
        actual_root = source_root if source_root is not None else self._source_root
        if not isinstance(actual_root, Path) or not actual_root.is_absolute():
            raise ValueError("source_root 必须是绝对 Path")
        if not actual_root.is_dir():
            raise ValueError("source_root 必须是已存在的目录")
        outputs = self.discover_outputs(actual_root)
        if not isinstance(outputs, tuple) or not outputs:
            raise ValueError("任务必须声明至少一个输出")
        for output in outputs:
            _validate_logical_path(output)
        if len(set(outputs)) != len(outputs):
            raise ValueError("任务输出逻辑路径不得重复")
        if outputs != tuple(sorted(outputs, key=lambda value: value.encode("utf-8"))):
            raise ValueError("任务输出必须按 UTF-8 字节序排列")
        spec = TaskSpec(
            name=self._name,
            dependencies=(),
            outputs=frozenset(outputs),
            implementation_version=self._implementation_version,
            execution_attributes=(
                ("kind", self._kind.value),
                ("platform", self._resource_input.platform.value),
                ("variant", self._resource_input.variant.value),
            ),
        )
        resolved_input_digest = _digest_payload(
            {
                "kind": self._kind.value,
                "outputs": list(outputs),
                "resource_snapshot_id": self._resource_input.resource_snapshot_id,
                "source_snapshot_id": self._resource_input.source_snapshot_id,
            }
        )
        config_digest = _digest_payload(
            {"rule_version": self._resource_input.rule_version, "kind": self._kind.value}
        )
        return TaskPlan(spec, resolved_input_digest, config_digest)

    def execute(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """执行子类构建逻辑并检查返回类型。

        参数：
            context: 共享构建上下文。
            inputs: 调用方显式提供的输入产物。

        返回：
            子类 ``build`` 返回的 ``TaskResult``。

        异常：
            返回值类型错误时抛出 ``TypeError``。

        约束与副作用：
            不额外重试或递归调用任务；副作用边界由子类和端口控制。
        """
        result = self.build(context, inputs)
        if not isinstance(result, TaskResult):
            raise TypeError("build 必须返回 TaskResult")
        for artifact in result.outputs:
            if not isinstance(artifact, LogicalArtifact):
                raise TypeError("TaskResult.outputs 必须全部是 LogicalArtifact")
        return result
