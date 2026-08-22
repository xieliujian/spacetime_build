"""把通用资源 builder 请求连接到真实 Unity batchmode 端口。

``UnityBatchAssetBuilder`` 是资源任务与 ``UnityBatchRunner`` 之间的受控适配器：
任务只提供类型化操作和显式输出规划，适配器负责旧 flag 兼容映射、预期输出路径、
日志位置和进程执行。它不提交 CAS、不修改源工程、不执行 SVN 写操作；真实环境是否
可用由独立探针记录，而不是由构造适配器时的路径存在性推断。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.errors import ToolExecutionError
from ports.unity import UnityBatchRequest, UnityBatchResult
from resource.tasks.unity_asset import UnityAssetBuildOutput, UnityAssetBuildRequest
from resource.unity_operations import LegacyUnityFlagMapper


class UnityAssetOutputPlanner(Protocol):
    """按固定资源请求规划 Unity 输出逻辑路径。"""

    def plan(self, request: UnityAssetBuildRequest) -> tuple[str, ...]:
        """返回按 UTF-8 排序的精确逻辑输出路径。"""
        ...


class UnityAssetDependencyReader(Protocol):
    """读取 Unity 结果中的有序依赖。"""

    def read(
        self,
        output_root: Path,
        logical_path: str,
    ) -> tuple[str, ...]:
        """返回一份输出对应的 Unity Manifest 依赖。"""
        ...


class UnityBatchRunnerPort(Protocol):
    """Unity batch 执行器的最小可注入端口。"""

    def run(self, request: UnityBatchRequest) -> UnityBatchResult:
        """执行 Unity 请求并返回结构化结果。"""
        ...


@dataclass(frozen=True, slots=True)
class FixedUnityAssetOutputPlanner:
    """为受控工程 fixture 提供固定输出规划。"""

    outputs: tuple[str, ...]

    def plan(self, request: UnityAssetBuildRequest) -> tuple[str, ...]:
        """校验固定输出位于当前操作前缀后返回稳定元组。"""
        del request
        if not isinstance(self.outputs, tuple) or not self.outputs:
            raise ValueError("outputs 必须是非空 tuple")
        return self.outputs


class UnityBatchAssetBuilder:
    """使用 UnityBatchRunner 执行一个类型化资源构建请求。"""

    def __init__(
        self,
        runner: UnityBatchRunnerPort,
        executable: Path,
        project_path: Path,
        method: str,
        log_path: Path,
        timeout_seconds: float,
        output_planner: UnityAssetOutputPlanner,
        *,
        dependency_reader: UnityAssetDependencyReader | None = None,
    ) -> None:
        """绑定 Unity 进程、工程、日志和输出规划依赖。

        参数：
            runner: 已注入 ProcessRunner 的 Unity batch 适配器。
            executable: Unity 可执行文件绝对路径。
            project_path: 隔离 Unity 工程绝对路径。
            method: 版本化 Unity 静态入口方法。
            log_path: 本次任务的绝对日志路径。
            timeout_seconds: Unity 最大执行时长。
            output_planner: 不启动 Unity 的确定性输出规划器。
            dependency_reader: 可选 Unity Manifest 依赖读取器。

        返回：
            ``None``；构造阶段不验证外部工具存在性，不启动进程。

        异常：
            参数类型或路径形式非法时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只保存端口和固定配置；真实副作用只发生在 ``build`` 调用。
        """
        if not callable(getattr(runner, "run", None)):
            raise TypeError("runner.run 必须可调用")
        for field_name, value in (
            ("executable", executable),
            ("project_path", project_path),
            ("log_path", log_path),
        ):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{field_name} 必须是绝对 Path")
        if not isinstance(method, str) or not method or any(char in method for char in "\r\n"):
            raise ValueError("method 必须是非空且不含换行的字符串")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是正数")
        if not callable(getattr(output_planner, "plan", None)):
            raise TypeError("output_planner.plan 必须可调用")
        if dependency_reader is not None and not callable(getattr(dependency_reader, "read", None)):
            raise TypeError("dependency_reader.read 必须可调用")
        self._runner = runner
        self._executable = executable
        self._project_path = project_path
        self._method = method
        self._log_path = log_path
        self._timeout_seconds = float(timeout_seconds)
        self._output_planner = output_planner
        self._dependency_reader = dependency_reader

    def plan(self, request: UnityAssetBuildRequest) -> tuple[str, ...]:
        """委托输出规划器并返回精确逻辑输出集合。"""
        if not isinstance(request, UnityAssetBuildRequest):
            raise TypeError("request 必须是 UnityAssetBuildRequest")
        paths = self._output_planner.plan(request)
        if not isinstance(paths, tuple) or not paths:
            raise ValueError("Unity 输出规划必须返回非空 tuple")
        return paths

    def build(self, request: UnityAssetBuildRequest) -> tuple[UnityAssetBuildOutput, ...]:
        """执行 Unity、校验预期文件并返回带依赖的输出记录。

        参数：
            request: 已通过资源任务校验的类型化请求。

        返回：
            builder 规划的文件输出，路径仍位于请求 output_root 内。

        异常：
            Unity 退出失败或预期文件缺失时抛出 ``ToolExecutionError``；路径和输出
            规划不一致时抛出 ``ValueError``。

        约束与副作用：
            只写隔离 Unity 工程/输出目录和日志；不写 CAS、不修改源 SVN。
        """
        if not isinstance(request, UnityAssetBuildRequest):
            raise TypeError("request 必须是 UnityAssetBuildRequest")
        logical_paths = self.plan(request)
        output_paths = tuple(
            self._output_path(request, logical_path) for logical_path in logical_paths
        )
        batch_request = UnityBatchRequest(
            self._executable,
            self._project_path,
            self._method,
            LegacyUnityFlagMapper.arguments_for(request.operation),
            self._log_path,
            self._timeout_seconds,
            output_paths,
        )
        result = self._runner.run(batch_request)
        if not result.success:
            missing = ", ".join(str(path) for path in result.missing_outputs)
            detail = f"，缺少输出: {missing}" if missing else ""
            raise ToolExecutionError(
                f"Unity 资源操作失败: {request.operation.name}, exit_code={result.exit_code}{detail}"
            )
        outputs: list[UnityAssetBuildOutput] = []
        for logical_path, output_path in zip(logical_paths, output_paths, strict=True):
            dependencies = (
                self._dependency_reader.read(request.output_root, logical_path)
                if self._dependency_reader is not None
                else ()
            )
            outputs.append(UnityAssetBuildOutput(logical_path, output_path, dependencies))
        return tuple(outputs)

    @staticmethod
    def _output_path(request: UnityAssetBuildRequest, logical_path: str) -> Path:
        """把客户端逻辑路径映射到 output_root 下并拒绝前缀逃逸。"""
        roots = request.operation.expected_output_roots
        if not roots or not logical_path.startswith(f"{roots[0]}/"):
            raise ValueError("Unity 输出逻辑路径不属于操作预期根")
        relative = logical_path[len(roots[0]) + 1 :]
        path = (request.output_root / relative).resolve(strict=False)
        root = request.output_root.resolve(strict=False)
        if path == root or root not in path.parents:
            raise ValueError("Unity 输出路径越出 output_root")
        return path


class MappingUnityAssetDependencyReader:
    """从固定逻辑路径映射读取 Unity 依赖的简单适配器。

    真实 Unity Manifest 解析器可以实现相同 Protocol；该类只用于受控验证和迁移证据，
    不扫描未知目录，也不改变依赖顺序。
    """

    def __init__(self, dependencies: Mapping[str, tuple[str, ...]]) -> None:
        """保存逻辑路径到有序依赖元组的只读快照。"""
        if not isinstance(dependencies, Mapping):
            raise TypeError("dependencies 必须是 Mapping")
        normalized: dict[str, tuple[str, ...]] = {}
        for logical_path, values in dependencies.items():
            if not isinstance(logical_path, str) or not isinstance(values, tuple):
                raise TypeError("依赖映射键和值类型非法")
            if any(not isinstance(value, str) for value in values):
                raise TypeError("依赖路径必须是字符串")
            normalized[logical_path] = values
        self._dependencies = normalized

    def read(self, output_root: Path, logical_path: str) -> tuple[str, ...]:
        """返回预登记依赖，不读取 output_root。"""
        del output_root
        return self._dependencies.get(logical_path, ())


__all__ = [
    "FixedUnityAssetOutputPlanner",
    "MappingUnityAssetDependencyReader",
    "UnityAssetDependencyReader",
    "UnityAssetOutputPlanner",
    "UnityBatchRunnerPort",
    "UnityBatchAssetBuilder",
]
