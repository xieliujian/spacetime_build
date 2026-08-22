"""真实 Unity batch builder 的结构化请求与输出边界测试。"""

from pathlib import Path

import pytest

from core.errors import ToolExecutionError
from ports.unity import UnityBatchResult
from resource.tasks.unity_asset import UnityAssetBuildRequest
from resource.unity_builder import (
    FixedUnityAssetOutputPlanner,
    MappingUnityAssetDependencyReader,
    UnityBatchAssetBuilder,
)
from resource.model import ResourceBuildInput
from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from core.platforms import BuildPlatform
from release.entries import ResourceVariant
from resource.unity_operations import UnityOperation, UnityProjectRole


class _Runner:
    """记录 UnityBatchRequest 并返回可控结果的 runner 替身。"""

    def __init__(self, result: object) -> None:
        """保存返回值和调用记录。"""
        self.result = result
        self.requests: list[object] = []

    def run(self, request: object) -> UnityBatchResult:
        """记录结构化 Unity 请求并返回固定结果。"""
        self.requests.append(request)
        return self.result


def _request(tmp_path: Path) -> UnityAssetBuildRequest:
    """构造资源 builder 请求。"""
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    operation = UnityOperation(
        "build_map",
        UnityProjectRole.RESOURCE,
        (("platform", "windows"),),
        ("map",),
    )
    return UnityAssetBuildRequest(
        _input(),
        (_config(),),
        source,
        output,
        operation,
        (("rule", "r1"),),
    )


def _input() -> ResourceBuildInput:
    """构造固定资源输入。"""
    return ResourceBuildInput(
        "source-1", "resource-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-3", None
    )


def _config() -> LogicalArtifact:
    """构造配置输入产物。"""
    return LogicalArtifact(
        logical_path="config/runtime.bin",
        kind=ArtifactKind.FILE,
        blob=BlobRef("blobs/" + "1" * 64, "1" * 64, 9),
        dependencies=(),
        subpackage_ids=frozenset(),
        metadata=ArtifactMetadata(
            source_task="config",
            source_revision="r100",
            toolchain_digest="b" * 64,
            attributes=(),
        ),
    )


def _result(log_path: Path, *, success: bool = True) -> UnityBatchResult:
    """构造 Unity runner 结果。"""
    return UnityBatchResult(
        success=success,
        exit_code=0 if success else 1,
        log_path=log_path,
    )


def test_unity_batch_builder_translates_operation_and_preserves_dependencies(
    tmp_path: Path,
) -> None:
    """验证 builder 使用旧 flag mapper 生成请求，并返回预登记依赖。"""
    log_path = tmp_path / "unity.log"
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "main.assetbundle").write_bytes(b"bundle")
    (output_root / "index.json").write_bytes(b"index")
    runner = _Runner(_result(log_path))
    builder = UnityBatchAssetBuilder(
        runner,  # type: ignore[arg-type]
        tmp_path / "Unity.exe",
        tmp_path / "project",
        "Build.Entry",
        log_path,
        30,
        FixedUnityAssetOutputPlanner(("map/index.json", "map/main.assetbundle")),
        dependency_reader=MappingUnityAssetDependencyReader(
            {"map/main.assetbundle": ("depend/shader_common.assetbundle",)}
        ),
    )
    request = _request(tmp_path)
    request = UnityAssetBuildRequest(
        request.resource_input,
        request.explicit_inputs,
        request.source_root,
        output_root,
        request.operation,
        request.settings,
    )

    outputs = builder.build(request)

    batch_request = runner.requests[0]
    assert batch_request.arguments[0] == "-BUILD_MAP"
    assert tuple(item.logical_path for item in outputs) == (
        "map/index.json",
        "map/main.assetbundle",
    )
    assert outputs[1].dependencies == ("depend/shader_common.assetbundle",)


def test_unity_batch_builder_rejects_failed_process(tmp_path: Path) -> None:
    """验证 Unity 非零退出不会伪造成功输出。"""
    log_path = tmp_path / "unity.log"
    builder = UnityBatchAssetBuilder(
        _Runner(_result(log_path, success=False)),  # type: ignore[arg-type]
        tmp_path / "Unity.exe",
        tmp_path / "project",
        "Build.Entry",
        log_path,
        30,
        FixedUnityAssetOutputPlanner(("map/main.assetbundle",)),
    )

    with pytest.raises(ToolExecutionError, match="Unity"):
        builder.build(_request(tmp_path))
