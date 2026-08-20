"""TaskResultPackage 确定性编码与篡改检测测试。"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.platforms import BuildPlatform
from core.tasks import ArtifactCollection, BuildContext
from ports.storage import PutObjectRequest, StoredObject
from release.entries import ResourceVariant
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput
from resource.result_package import (
    TaskArtifactManifestFactory,
    TaskExecutionRecord,
    TaskExecutionStatus,
    TaskResultPackage,
    read_task_artifact_manifest,
)
from resource.task_service import ResourceBuildService
from resource.tasks.config import ConfigResourceTask


class _Store:
    """提供确定性对象存储回执。"""

    def put(self, request: PutObjectRequest) -> StoredObject:
        """返回与请求一致的对象引用。"""
        return StoredObject(request.key, request.sha256, len(request.content))


def _context() -> BuildContext:
    """构造固定上下文。"""
    return BuildContext("a" * 64, "r100", "b" * 64, None, 1)


def test_result_manifest_round_trips_and_recomputes_identity(tmp_path: Path) -> None:
    """验证结果 manifest 读回时重算任务身份和 manifest ID。"""
    source = tmp_path / "config"
    source.mkdir()
    (source / "settings.bin").write_bytes(b"settings")
    task = ConfigResourceTask(
        ResourceBuildInput(
            "source-1", "resource-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-1", None
        ),
        source,
        BlobCommitter(_Store()),
    )
    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))
    manifest = TaskArtifactManifestFactory.create(
        _context(), result.plan, result.identity, result.result, explicit_input_digests=("c" * 64,)
    )
    path = tmp_path / "task-artifact-manifest.toml"
    manifest.write(path)
    loaded = read_task_artifact_manifest(path)
    assert loaded.manifest_id == manifest.manifest_id
    assert loaded.payload.task_identity == result.identity
    assert loaded.payload.explicit_input_digests == ("c" * 64,)


def test_result_package_framing_is_deterministic_and_detects_tamper(tmp_path: Path) -> None:
    """验证双文件 framing 稳定，修改执行记录会使 result.sha256 不再匹配。"""
    source = tmp_path / "config"
    source.mkdir()
    (source / "settings.bin").write_bytes(b"settings")
    task = ConfigResourceTask(
        ResourceBuildInput(
            "source-1", "resource-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-1", None
        ),
        source,
        BlobCommitter(_Store()),
    )
    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))
    manifest = TaskArtifactManifestFactory.create(
        _context(), result.plan, result.identity, result.result
    )
    execution = TaskExecutionRecord(
        build_id="build-1",
        run_id="run-1",
        request_id="request-1",
        status=TaskExecutionStatus.SUCCESS,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        log_locator="logs/run-1.log",
    )
    package = TaskResultPackage.write(tmp_path / "package", manifest, execution)
    first_digest = package.result_digest
    package_again = TaskResultPackage.write(tmp_path / "package-again", manifest, execution)
    assert package_again.result_digest == first_digest
    execution_path = tmp_path / "package" / "task-execution-record.toml"
    execution_path.write_bytes(execution_path.read_bytes().replace(b"SUCCESS", b"FAILED"))
    with pytest.raises(ValueError):
        TaskResultPackage.read(tmp_path / "package")
