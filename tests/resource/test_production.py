"""验证生产 Unity 资源 builder 的清单边界与七类任务工厂。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from core.platforms import BuildPlatform
from core.tasks import ArtifactCollection, BuildContext
from ports.storage import PutObjectRequest, StoredObject
from release.entries import ResourceVariant
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.production import (
    JsonUnityDependencyReader,
    ManifestUnityAssetOutputPlanner,
    ProductionUnityAssetBuilderFactory,
    create_unity_resource_task,
)
from resource.tasks.unity_asset import UnityAssetBuildRequest
from resource.unity_operations import UnityOperation, UnityProjectRole


class _Runner:
    """记录生产 builder 生成的 Unity 请求。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.requests: list[object] = []

    def run(self, request: object) -> object:
        """创建清单声明的输出并返回成功结果。"""
        self.requests.append(request)
        for output in request.expected_outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(output.name.encode("utf-8"))
        from ports.unity import UnityBatchResult

        return UnityBatchResult(True, 0, request.log_path)


class _Store:
    """提供最小不可变对象存储替身。"""

    def put(self, request: PutObjectRequest) -> StoredObject:
        """返回与请求一致的对象回执。"""
        return StoredObject(request.key, request.sha256, len(request.content))


def _resource_input() -> ResourceBuildInput:
    """创建固定生产资源输入。"""
    return ResourceBuildInput(
        "source-snapshot",
        "resource-snapshot",
        BuildPlatform.WINDOWS,
        ResourceVariant.MAIN,
        "rules-1",
        None,
    )


def _config_artifact() -> LogicalArtifact:
    """创建 map 任务需要的配置产物。"""
    digest = "1" * 64
    return LogicalArtifact(
        "config/runtime.bin",
        ArtifactKind.FILE,
        BlobRef(f"blobs/{digest}", digest, 1),
        (),
        frozenset(),
        ArtifactMetadata("config", "r1", "2" * 64, ()),
    )


def _unity_request(source_root: Path, operation_name: str = "build_map") -> UnityAssetBuildRequest:
    """创建输出规划器使用的真实 Unity 请求。"""
    return UnityAssetBuildRequest(
        _resource_input(),
        (_config_artifact(),),
        source_root,
        source_root.parent / "output",
        UnityOperation(
            operation_name,
            UnityProjectRole.RESOURCE,
            (("platform", "windows"),),
            (operation_name.removeprefix("build_"),),
        ),
    )


def test_manifest_planner_reads_operation_outputs_from_snapshot(tmp_path: Path) -> None:
    """验证输出规划来自隔离快照的显式清单且按 UTF-8 稳定排序。"""
    source_root = tmp_path / "source"
    manifest = source_root / ".spacetime" / "unity_outputs.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operations": {
                    "build_map": {
                        "outputs": ["map/main.assetbundle", "map/index.json"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    planner = ManifestUnityAssetOutputPlanner()
    request = _unity_request(source_root)

    assert planner.plan(request) == ("map/index.json", "map/main.assetbundle")


def test_manifest_planner_rejects_unknown_operation_and_unsafe_output(tmp_path: Path) -> None:
    """验证生产输出清单不能越权声明未知操作或路径。"""
    source_root = tmp_path / "source"
    manifest = source_root / ".spacetime" / "unity_outputs.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operations": {"build_map": {"outputs": ["../escape.bin"]}},
            }
        ),
        encoding="utf-8",
    )
    planner = ManifestUnityAssetOutputPlanner()
    request = _unity_request(source_root)

    with pytest.raises(ValueError, match="路径"):
        planner.plan(request)


def test_dependency_reader_reads_ordered_manifest(tmp_path: Path) -> None:
    """验证 Unity 输出依赖由输出根内的明确 manifest 提供。"""
    output_root = tmp_path / "output"
    manifest = output_root / ".spacetime" / "dependencies.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dependencies": {"map/main.assetbundle": ["depend/a", "depend/b"]},
            }
        ),
        encoding="utf-8",
    )

    reader = JsonUnityDependencyReader()

    assert reader.read(output_root, "map/main.assetbundle") == ("depend/a", "depend/b")


def test_production_factory_creates_real_batch_builder_and_task(tmp_path: Path) -> None:
    """验证 factory 可将真实 Unity runner 注入 map 任务，而不退回文件兼容模式。"""
    source_root = tmp_path / "source"
    manifest = source_root / ".spacetime" / "unity_outputs.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operations": {"build_map": {"outputs": ["map/main.assetbundle"]}},
            }
        ),
        encoding="utf-8",
    )
    runner = _Runner()
    builder = ProductionUnityAssetBuilderFactory(
        runner,  # type: ignore[arg-type]
        tmp_path / "Unity.exe",
        tmp_path / "unity-project",
        tmp_path / "logs",
        "Build.Entry",
        60,
    ).create("map", "run-1")
    task = create_unity_resource_task(
        ResourceKind.MAP,
        _resource_input(),
        source_root,
        BlobCommitter(_Store()),
        builder=builder,
        output_root=tmp_path / "output",
    )

    plan = task.plan_with_inputs(
        BuildContext("a" * 64, "r1", "b" * 64, None, 1),
        ArtifactCollection.from_artifacts((_config_artifact(),)),
    )

    assert plan.spec.outputs == frozenset({"map/main.assetbundle"})
    assert runner.requests == []
