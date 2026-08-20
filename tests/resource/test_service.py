"""单任务执行服务和显式聚合契约测试。"""

from pathlib import Path

import pytest

from core.platforms import BuildPlatform
from core.tasks import ArtifactCollection, BuildContext
from ports.storage import PutObjectRequest, StoredObject
from release.entries import ResourceVariant
from resource.aggregation import ResourceManifestAggregator
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput
from resource.tasks.config import ConfigResourceTask
from resource.tasks.lua import LuaResourceTask
from resource.task_service import ResourceBuildService


class _Store:
    """提供确定性内存对象存储的测试替身。"""

    def put(self, request: PutObjectRequest) -> StoredObject:
        """返回与请求一致的持久对象引用。"""
        return StoredObject(request.key, request.sha256, len(request.content))


def _context() -> BuildContext:
    """构造固定任务上下文。"""
    return BuildContext("a" * 64, "r100", "b" * 64, None, 1)


def _input() -> ResourceBuildInput:
    """构造固定资源输入。"""
    return ResourceBuildInput(
        "source-1", "resource-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-1", None
    )


def test_file_resource_tasks_discover_and_commit_exact_outputs(tmp_path: Path) -> None:
    """验证任务输出按 UTF-8 排序并转成持久 CAS 产物。"""
    source = tmp_path / "config"
    source.mkdir()
    (source / "z.bin").write_bytes(b"z")
    (source / "a.bin").write_bytes(b"a")
    task = ConfigResourceTask(_input(), source, BlobCommitter(_Store()))
    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))
    assert tuple(sorted(result.plan.spec.outputs)) == ("config/a.bin", "config/z.bin")
    assert tuple(item.logical_path for item in result.result.outputs) == (
        "config/a.bin",
        "config/z.bin",
    )
    assert all(item.blob.locator.startswith("blobs/") for item in result.result.outputs)


def test_service_rejects_output_pollution_and_aggregator_requires_explicit_tasks(
    tmp_path: Path,
) -> None:
    """验证实际输出集合不得污染声明，正式聚合拒绝缺失任务。"""
    source = tmp_path / "lua"
    source.mkdir()
    (source / "main.lua").write_text("return 1", encoding="utf-8")
    task = LuaResourceTask(_input(), source, BlobCommitter(_Store()))
    service = ResourceBuildService()
    result = service.build(task, _context(), ArtifactCollection.from_artifacts(()))
    with pytest.raises(ValueError):
        ResourceManifestAggregator(required_tasks=("lua", "config")).aggregate(
            _context(), (result,)
        )
