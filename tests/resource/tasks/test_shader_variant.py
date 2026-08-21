"""Shader variant 任务的 Unity 操作、输入快照和输出所有权测试。

本模块使用可控的 builder 替身验证资源任务边界；替身只写入临时输出目录，
不启动真实 Unity，也不假定真实变体文件格式。
"""

from pathlib import Path

import pytest

from core.platforms import BuildPlatform
from core.tasks import ArtifactCollection, BuildContext
from ports.storage import PutObjectRequest, StoredObject
from release.entries import ResourceVariant
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput
from resource.task_service import ResourceBuildService
from resource.tasks.shader_variant import (
    ShaderVariantBuildOutput,
    ShaderVariantBuildRequest,
    ShaderVariantResourceTask,
)
from resource.unity_operations import UnityProjectRole


class _Store:
    """记录 shader variant Blob 写入的内存对象存储替身。"""

    def __init__(self) -> None:
        """初始化写入记录。"""
        self.requests: list[PutObjectRequest] = []

    def put(self, request: PutObjectRequest) -> StoredObject:
        """保存请求并返回与请求一致的对象引用。"""
        self.requests.append(request)
        return StoredObject(request.key, request.sha256, len(request.content))


class _Builder:
    """规划并生成固定变体清单的 builder 替身。"""

    def __init__(self, output_root: Path) -> None:
        """保存输出根并初始化请求记录。"""
        self.output_root = output_root
        self.plan_requests: list[ShaderVariantBuildRequest] = []
        self.build_requests: list[ShaderVariantBuildRequest] = []

    def plan(self, request: ShaderVariantBuildRequest) -> tuple[str, ...]:
        """返回固定的变体集合和清单输出。"""
        self.plan_requests.append(request)
        return (
            "depend/shader_variant/variants.bin",
            "depend/shader_variant/variants.json",
        )

    def build(self, request: ShaderVariantBuildRequest) -> tuple[ShaderVariantBuildOutput, ...]:
        """写入固定字节并返回精确输出。"""
        self.build_requests.append(request)
        request.output_root.mkdir(parents=True, exist_ok=True)
        binary = request.output_root / "variants.bin"
        manifest = request.output_root / "variants.json"
        binary.write_bytes(b"variant-binary")
        manifest.write_bytes(b'{"version":1}')
        return (
            ShaderVariantBuildOutput("depend/shader_variant/variants.bin", binary),
            ShaderVariantBuildOutput("depend/shader_variant/variants.json", manifest),
        )


def _input() -> ResourceBuildInput:
    """构造固定 Windows 主资源输入。"""
    return ResourceBuildInput(
        "source-1", "shader-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-2", None
    )


def _context() -> BuildContext:
    """构造确定性资源构建上下文。"""
    return BuildContext("a" * 64, "r100", "b" * 64, None, 1)


def test_shader_variant_plans_collect_operation_and_commits_exact_outputs(tmp_path: Path) -> None:
    """验证任务绑定 Shader 工程、输入快照和唯一输出所有权。"""
    source = tmp_path / "source"
    source.mkdir()
    output_root = tmp_path / "output"
    builder = _Builder(output_root)
    store = _Store()
    task = ShaderVariantResourceTask(
        _input(),
        source,
        BlobCommitter(store),
        builder=builder,
        output_root=output_root,
    )

    plan = task.plan(_context())
    assert plan.spec.outputs == frozenset(
        {
            "depend/shader_variant/variants.bin",
            "depend/shader_variant/variants.json",
        }
    )
    request = builder.plan_requests[0]
    assert request.operation.name == "collect_variant"
    assert request.operation.project_role is UnityProjectRole.SHADER
    assert request.resource_input.resource_snapshot_id == "shader-1"
    assert request.resource_input.rule_version == "rules-2"
    assert builder.build_requests == []

    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))

    assert tuple(item.logical_path for item in result.result.outputs) == (
        "depend/shader_variant/variants.bin",
        "depend/shader_variant/variants.json",
    )
    assert len(store.requests) == 2
    assert len(builder.build_requests) == 1


def test_shader_variant_rejects_builder_output_drift_before_cas_write(tmp_path: Path) -> None:
    """验证 builder 少产出时任务不会登记部分 Blob。"""

    class _DriftingBuilder(_Builder):
        """返回不完整变体输出的替身。"""

        def build(self, request: ShaderVariantBuildRequest) -> tuple[ShaderVariantBuildOutput, ...]:
            """故意只返回一个规划输出。"""
            del request
            return (
                ShaderVariantBuildOutput(
                    "depend/shader_variant/variants.bin", self.output_root / "missing"
                ),
            )

    source = tmp_path / "source"
    source.mkdir()
    output_root = tmp_path / "output"
    store = _Store()
    task = ShaderVariantResourceTask(
        _input(),
        source,
        BlobCommitter(store),
        builder=_DriftingBuilder(output_root),
        output_root=output_root,
    )

    with pytest.raises(ValueError, match="规划输出"):
        ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))
    assert store.requests == []


def test_shader_variant_compatibility_mode_rejects_ignored_operation_arguments(
    tmp_path: Path,
) -> None:
    """验证无 builder 时不会静默忽略 Unity 操作参数。"""
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="operation_arguments"):
        ShaderVariantResourceTask(
            _input(),
            source,
            BlobCommitter(_Store()),
            operation_arguments=(("quality", "release"),),
        )
