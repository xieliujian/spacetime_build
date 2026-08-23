"""生产 Unity 资源 builder 的清单驱动装配。

本模块把真实项目规则与通用资源任务之间的剩余边界显式化：隔离源码快照提供
``unity_outputs.json``，Unity batchmode 在指定输出根生成文件，输出依赖由可选的
``dependencies.json`` 提供。模块不扫描未知目录、不猜测资源规则，也不直接提交
CAS；所有外部进程仍通过 ``UnityBatchRunnerPort`` 注入。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.tasks.audio import AudioResourceTask
from resource.tasks.character import CharacterResourceTask
from resource.tasks.map import MapResourceTask
from resource.tasks.particle import ParticleResourceTask
from resource.tasks.texture import TextureResourceTask
from resource.tasks.ui import UiResourceTask
from resource.tasks.unity_asset import UnityAssetBuildRequest, UnityAssetBuilder
from resource.tasks.video import VideoResourceTask
from resource.unity_builder import (
    UnityAssetDependencyReader,
    UnityBatchAssetBuilder,
    UnityBatchRunnerPort,
)


def _safe_relative_path(value: object, field_name: str) -> Path:
    """校验清单文件路径为不含逃逸的相对路径。

    参数：
        value: 待读取的路径值。
        field_name: 错误消息使用的字段名。

    返回：
        规范化前的相对 ``Path``。

    异常：
        路径为空、绝对、包含 ``..`` 或控制字符时抛出 ``ValueError``。

    约束与副作用：
        只做内存校验，不访问文件系统。
    """
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 必须是非空安全路径")
    path = Path(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field_name} 必须是无路径逃逸的相对路径")
    return path


def _under_root(root: Path, relative: Path) -> Path:
    """解析根目录内的清单路径并拒绝符号链接逃逸。"""
    if not isinstance(root, Path) or not root.is_absolute() or root.is_symlink():
        raise ValueError("root 必须是绝对普通目录")
    candidate = (root / relative).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise ValueError("清单路径越出隔离根")
    return candidate


def _read_json(path: Path, field_name: str) -> object:
    """读取 UTF-8 JSON 清单并把解析失败转换为稳定错误。"""
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"{field_name} 不存在或不是普通文件: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} 不是合法 UTF-8 JSON: {path}") from exc


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    """把 JSON 对象校验为字符串键映射。"""
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} 必须是 JSON object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} 的键必须是字符串")
    return value


def _schema(value: Mapping[str, object], field_name: str) -> None:
    """校验清单 schema 版本。"""
    if value.get("schema_version") != 1:
        raise ValueError(f"{field_name}.schema_version 必须等于 1")


class ManifestUnityAssetOutputPlanner:
    """从隔离源码快照的 Unity 输出清单规划精确逻辑输出。

    清单格式为 ``{"schema_version": 1, "operations": {"build_map":
    {"outputs": ["map/main.assetbundle"]}}}``。输出必须属于当前操作声明的
    根目录；规划结果按 UTF-8 字节序排序，避免操作系统目录顺序影响任务身份。
    """

    def __init__(self, manifest_relative_path: str = ".spacetime/unity_outputs.json") -> None:
        """保存源码快照内的输出清单相对路径。"""
        self._manifest_relative_path = _safe_relative_path(
            manifest_relative_path, "manifest_relative_path"
        )

    def plan(self, request: UnityAssetBuildRequest) -> tuple[str, ...]:
        """读取操作输出清单并返回稳定排序的逻辑路径。

        参数：
            request: 已校验的 Unity 资源请求。

        返回：
            当前操作声明的非空、唯一、按 UTF-8 排序的逻辑输出路径。

        异常：
            清单缺失、schema 错误、操作未登记、输出越界或重复时抛出
            ``FileNotFoundError`` / ``ValueError``。

        约束与副作用：
            只读取隔离源码快照，不启动 Unity、不创建文件。
        """
        if not isinstance(request, UnityAssetBuildRequest):
            raise TypeError("request 必须是 UnityAssetBuildRequest")
        path = _under_root(request.source_root, self._manifest_relative_path)
        root = _mapping(_read_json(path, "Unity 输出清单"), "Unity 输出清单")
        _schema(root, "Unity 输出清单")
        operations = _mapping(root.get("operations"), "Unity 输出清单.operations")
        operation = _mapping(operations.get(request.operation.name), "Unity 操作")
        outputs = operation.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise ValueError("Unity 操作 outputs 必须是非空数组")
        expected_root = request.operation.expected_output_roots[0]
        paths: list[str] = []
        for value in outputs:
            if not isinstance(value, str):
                raise ValueError("Unity 输出逻辑路径必须是字符串")
            _validate_logical_output(value, expected_root)
            paths.append(value)
        if len(paths) != len(set(paths)):
            raise ValueError("Unity 输出逻辑路径不得重复")
        return tuple(sorted(paths, key=lambda value: value.encode("utf-8")))


class JsonUnityDependencyReader(UnityAssetDependencyReader):
    """读取 Unity 输出根内可选的确定性依赖清单。

    清单格式为 ``{"schema_version": 1, "dependencies": {"map/a":
    ["depend/b"]}}``。没有依赖清单表示该输出没有额外依赖；清单存在但格式错误
    时失败，避免静默丢失 Unity 依赖。
    """

    def __init__(self, manifest_relative_path: str = ".spacetime/dependencies.json") -> None:
        """保存输出根内的依赖清单相对路径。"""
        self._manifest_relative_path = _safe_relative_path(
            manifest_relative_path, "manifest_relative_path"
        )

    def read(self, output_root: Path, logical_path: str) -> tuple[str, ...]:
        """读取一个逻辑输出对应的依赖顺序。

        参数：
            output_root: Unity 隔离输出根。
            logical_path: 待读取依赖的逻辑输出路径。

        返回：
            依赖逻辑路径元组，保留清单顺序和重复项。

        异常：
            清单存在但 schema 或路径非法时抛出 ``ValueError``。

        约束与副作用：
            只读取输出根内的普通清单文件；缺少可选清单时返回空元组。
        """
        if not isinstance(output_root, Path) or not output_root.is_absolute():
            raise ValueError("output_root 必须是绝对 Path")
        _validate_logical_output(logical_path, logical_path.split("/", 1)[0])
        path = _under_root(output_root, self._manifest_relative_path)
        if not path.exists():
            return ()
        root = _mapping(_read_json(path, "Unity 依赖清单"), "Unity 依赖清单")
        _schema(root, "Unity 依赖清单")
        dependencies = _mapping(root.get("dependencies"), "Unity 依赖清单.dependencies")
        raw_values = dependencies.get(logical_path, [])
        if not isinstance(raw_values, list):
            raise ValueError(f"依赖清单中的 {logical_path} 必须是数组")
        result: list[str] = []
        for value in raw_values:
            if not isinstance(value, str):
                raise ValueError("Unity 依赖路径必须是字符串")
            _validate_logical_path_value(value)
            result.append(value)
        return tuple(result)


class ProductionUnityAssetBuilderFactory:
    """创建七类资源共用的真实 Unity batch builder。

    每次 ``create`` 都生成独立的日志定位和清单读取器。任务仍需由调用方提供隔离
    ``source_root``、``output_root`` 和正确的资源输入；factory 不复制工程、不猜测
    输出，也不自动写入源仓库。
    """

    def __init__(
        self,
        runner: UnityBatchRunnerPort,
        executable: Path,
        project_path: Path,
        log_root: Path,
        method: str,
        timeout_seconds: float,
        *,
        output_manifest_relative_path: str = ".spacetime/unity_outputs.json",
        dependency_manifest_relative_path: str = ".spacetime/dependencies.json",
    ) -> None:
        """保存真实 Unity 运行所需的显式依赖。"""
        if not callable(getattr(runner, "run", None)):
            raise TypeError("runner.run 必须可调用")
        for field_name, value in (
            ("executable", executable),
            ("project_path", project_path),
            ("log_root", log_root),
        ):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{field_name} 必须是绝对 Path")
        if not isinstance(method, str) or not method or any(char in method for char in "\r\n"):
            raise ValueError("method 必须是非空且不含换行的字符串")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是正数")
        self._runner = runner
        self._executable = executable
        self._project_path = project_path
        self._log_root = log_root
        self._method = method
        self._timeout_seconds = float(timeout_seconds)
        self._output_manifest_relative_path = output_manifest_relative_path
        self._dependency_manifest_relative_path = dependency_manifest_relative_path

    def create(self, task_name: str, run_id: str) -> UnityAssetBuilder:
        """为一个资源任务创建真实 batch builder。

        参数：
            task_name: 资源任务名，用于日志目录。
            run_id: 当前任务运行身份，用于日志文件名。

        返回：
            配置了清单规划器和依赖读取器的 ``UnityBatchAssetBuilder``。

        异常：
            任务名或运行身份不安全时抛出 ``ValueError``。

        约束与副作用：
            只创建内存对象，不检查 Unity 是否安装，不启动进程。
        """
        _safe_segment(task_name, "task_name")
        _safe_segment(run_id, "run_id")
        log_path = self._log_root / task_name / f"{run_id}.unity.log"
        return UnityBatchAssetBuilder(
            self._runner,
            self._executable,
            self._project_path,
            self._method,
            log_path,
            self._timeout_seconds,
            ManifestUnityAssetOutputPlanner(self._output_manifest_relative_path),
            dependency_reader=JsonUnityDependencyReader(self._dependency_manifest_relative_path),
        )


def create_unity_resource_task(
    kind: ResourceKind,
    resource_input: ResourceBuildInput,
    source_root: Path,
    blob_committer: BlobCommitter,
    *,
    builder: UnityAssetBuilder,
    output_root: Path,
    operation_arguments: tuple[tuple[str, str], ...] = (),
    implementation_version: str = "1",
    crop_profile: str | None = None,
    texture_profile: str = "default",
    spine_version: str | None = None,
    languages: tuple[str, ...] = (),
    atlas_version: str = "1",
    fonts: tuple[str, ...] = (),
    custom_fbx: tuple[str, ...] = (),
    dependency_cleanup_version: str = "1",
    bank_names: tuple[str, ...] = (),
    encoding_profile: str = "default",
    source_names: tuple[str, ...] = (),
) -> object:
    """按资源种类创建一个已绑定真实 Unity builder 的七类任务。

    参数：
        kind/resource_input/source_root/blob_committer: 任务身份和 CAS 边界。
        builder/output_root: 真实 Unity builder 与隔离输出根。
        其余参数：各资源任务的显式规则设置，会进入任务身份摘要。

    返回：
        对应的资源任务对象。

    异常：
        资源种类不是七类 Unity 资源，或参数类型不满足具体任务约束时抛出异常。

    约束与副作用：
        只构造任务；不会读取输入、创建输出或启动 Unity。
    """
    common = {
        "builder": builder,
        "output_root": output_root,
        "operation_arguments": operation_arguments,
        "implementation_version": implementation_version,
    }
    if kind is ResourceKind.MAP:
        return MapResourceTask(resource_input, source_root, blob_committer, **common)
    if kind is ResourceKind.CHARACTER:
        return CharacterResourceTask(
            resource_input,
            source_root,
            blob_committer,
            **common,
            crop_profile=crop_profile,
        )
    if kind is ResourceKind.TEXTURE:
        return TextureResourceTask(
            resource_input,
            source_root,
            blob_committer,
            **common,
            texture_profile=texture_profile,
            spine_version=spine_version,
            languages=languages,
        )
    if kind is ResourceKind.UI:
        return UiResourceTask(
            resource_input,
            source_root,
            blob_committer,
            **common,
            atlas_version=atlas_version,
            fonts=fonts,
            languages=languages,
        )
    if kind is ResourceKind.PARTICLE:
        return ParticleResourceTask(
            resource_input,
            source_root,
            blob_committer,
            **common,
            custom_fbx=custom_fbx,
            dependency_cleanup_version=dependency_cleanup_version,
        )
    if kind is ResourceKind.AUDIO:
        return AudioResourceTask(
            resource_input,
            source_root,
            blob_committer,
            **common,
            bank_names=bank_names,
            encoding_profile=encoding_profile,
        )
    if kind is ResourceKind.VIDEO:
        return VideoResourceTask(
            resource_input,
            source_root,
            blob_committer,
            **common,
            source_names=source_names,
            encoding_profile=encoding_profile,
        )
    raise ValueError(f"不支持的 Unity 资源种类: {kind!r}")


def _safe_segment(value: object, field_name: str) -> None:
    """校验日志目录和文件名使用的单路径段。"""
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{field_name} 必须是非空路径段")
    if any(char in value for char in "/\\\r\n\t"):
        raise ValueError(f"{field_name} 不得包含路径分隔符或控制字符")


def _validate_logical_path_value(value: str) -> None:
    """校验依赖逻辑路径不含绝对路径或逃逸段。"""
    if not value or value.startswith(("/", "\\")) or "\\" in value:
        raise ValueError(f"逻辑路径非法: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"逻辑路径非法: {value!r}")


def _validate_logical_output(value: str, expected_root: str) -> None:
    """校验一个输出路径属于期望资源根。"""
    _validate_logical_path_value(value)
    if not value.startswith(f"{expected_root}/"):
        raise ValueError(f"输出必须位于 {expected_root}/ 前缀: {value}")


__all__ = [
    "JsonUnityDependencyReader",
    "ManifestUnityAssetOutputPlanner",
    "ProductionUnityAssetBuilderFactory",
    "create_unity_resource_task",
]
