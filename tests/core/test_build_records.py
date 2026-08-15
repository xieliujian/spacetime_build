"""验证构建清单 payload 与执行记录的职责分离契约。

本模块按第二阶段 Task 5 分步覆盖可复现 ``BuildManifestPayload`` 与运行态
``BuildExecutionRecord``。测试不访问 SVN、Unity、Jenkins 或 CDN，也不写入
构建产物或持久化执行记录。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    BlobRef,
    LogicalArtifact,
)
from core.build_records import (
    BUILD_EXECUTION_SCHEMA_VERSION,
    BuildExecutionRecord,
    BuildManifestPayload,
    BuildStatus,
)
from core.errors import ArtifactValidationError

_VALID_SHA256 = "b" * 64

# payload 类型签名中禁止出现的运行态 / 身份字段名。
_FORBIDDEN_PAYLOAD_FIELD_NAMES = frozenset(
    {
        "manifest_id",
        "build_id",
        "status",
        "started_at",
        "finished_at",
        "duration",
        "duration_ms",
        "elapsed",
        "elapsed_ms",
        "log_locator",
        "logs",
        "log",
    }
)


def _valid_artifact() -> LogicalArtifact:
    """构造测试用合法 ``LogicalArtifact``。

    返回：
        含最小合法字段的不可变逻辑产物。
    """
    return LogicalArtifact(
        logical_path="scene/a.assetbundle",
        kind=ArtifactKind.ASSET_BUNDLE,
        blob=BlobRef(
            locator=f"sha256:{_VALID_SHA256}",
            sha256=_VALID_SHA256,
            size=2048,
        ),
        dependencies=("shared/base",),
        subpackage_ids=frozenset({1}),
        metadata=ArtifactMetadata(
            source_task="scene.build",
            source_revision="r100",
            toolchain_digest="toolchain-v1",
            attributes=(("platform", "android"),),
        ),
    )


def test_build_manifest_payload_contains_only_reproducible_content() -> None:
    """验证 ``BuildManifestPayload`` 仅含可复现内容且对象不可变。

    测试无参数和返回值。断言：

    - 可构造 ``BuildManifestPayload(schema_version, request_digest, revision,
      toolchain_digest, baseline_id, artifacts, task_identities)``，表达固定
      请求摘要、revision、工具链、可选基线、产物元组与任务身份元组；
    - ``baseline_id`` 可为 ``None``；
    - 类型签名（dataclass 字段名）中不存在 ``manifest_id``、``build_id``、
      状态、时间、耗时或日志相关字段；
    - 对象 ``frozen``（赋值触发 ``AttributeError`` / ``TypeError``）；
    - ``artifacts`` / ``task_identities`` 必须为元组，非法类型抛出
      ``ArtifactValidationError``。

    当 ``core.build_records`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入外不产生外部副作用。
    """
    artifact = _valid_artifact()
    artifacts = (artifact,)
    task_identities = ("scene.build:r100", "shared.build:r100")

    payload = BuildManifestPayload(
        schema_version=1,
        request_digest="request-digest-abc",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id="baseline-release-001",
        artifacts=artifacts,
        task_identities=task_identities,
    )
    assert payload.schema_version == 1
    assert payload.request_digest == "request-digest-abc"
    assert payload.revision == "r100"
    assert payload.toolchain_digest == "toolchain-v1"
    assert payload.baseline_id == "baseline-release-001"
    assert payload.artifacts == artifacts
    assert payload.task_identities == task_identities

    payload_without_baseline = BuildManifestPayload(
        schema_version=1,
        request_digest="request-digest-abc",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id=None,
        artifacts=(),
        task_identities=(),
    )
    assert payload_without_baseline.baseline_id is None
    assert payload_without_baseline.artifacts == ()
    assert payload_without_baseline.task_identities == ()

    field_names = {field.name for field in dataclasses.fields(BuildManifestPayload)}
    assert field_names.isdisjoint(_FORBIDDEN_PAYLOAD_FIELD_NAMES)
    assert "manifest_id" not in field_names
    assert "build_id" not in field_names

    with pytest.raises((AttributeError, TypeError)):
        payload.revision = "r999"  # type: ignore[misc]

    with pytest.raises(ArtifactValidationError):
        BuildManifestPayload(
            schema_version=1,
            request_digest="request-digest-abc",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id=None,
            artifacts=[artifact],  # type: ignore[arg-type]
            task_identities=task_identities,
        )

    with pytest.raises(ArtifactValidationError):
        BuildManifestPayload(
            schema_version=1,
            request_digest="request-digest-abc",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id=None,
            artifacts=artifacts,
            task_identities=["scene.build:r100"],  # type: ignore[arg-type]
        )

    with pytest.raises(ArtifactValidationError):
        BuildManifestPayload(
            schema_version=1,
            request_digest="request-digest-abc",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id=None,
            artifacts=(object(),),  # type: ignore[arg-type]
            task_identities=task_identities,
        )

    with pytest.raises(ArtifactValidationError):
        BuildManifestPayload(
            schema_version=1,
            request_digest="request-digest-abc",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id=None,
            artifacts=artifacts,
            task_identities=(1,),  # type: ignore[arg-type]
        )


def test_build_execution_record_owns_runtime_state_and_rejects_unknown_schema() -> None:
    """验证运行状态仅由 ``BuildExecutionRecord`` 承载，并拒绝未知 schema。

    测试无参数和返回值。断言：

    - ``BuildStatus`` 提供进行中与终态枚举成员；
    - 可构造 ``BuildExecutionRecord(schema_version, build_id, manifest_id,
      status, started_at, finished_at, log_locator)``；
    - ``schema_version`` 必须等于 ``BUILD_EXECUTION_SCHEMA_VERSION``，未知版本
      抛出 ``ArtifactValidationError``；
    - 进行中记录允许 ``manifest_id`` 与 ``finished_at`` 为 ``None``；
    - 当 ``finished_at`` 存在时不得早于 ``started_at``；
    - 对象不可变；运行态字段不出现在 ``BuildManifestPayload`` 字段集合中。

    当前一步最小 GREEN 只定义 payload 时，导入 ``BuildStatus`` /
    ``BuildExecutionRecord`` 应以 ``ImportError`` 失败。除导入外不产生外部副作用。
    """
    started = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)

    assert BUILD_EXECUTION_SCHEMA_VERSION == 1
    assert BuildStatus.PENDING is not None
    assert BuildStatus.RUNNING is not None
    assert BuildStatus.SUCCEEDED is not None
    assert BuildStatus.FAILED is not None

    in_progress = BuildExecutionRecord(
        schema_version=BUILD_EXECUTION_SCHEMA_VERSION,
        build_id="build-001",
        manifest_id=None,
        status=BuildStatus.RUNNING,
        started_at=started,
        finished_at=None,
        log_locator="logs/build-001.txt",
    )
    assert in_progress.build_id == "build-001"
    assert in_progress.manifest_id is None
    assert in_progress.status is BuildStatus.RUNNING
    assert in_progress.started_at == started
    assert in_progress.finished_at is None
    assert in_progress.log_locator == "logs/build-001.txt"

    completed = BuildExecutionRecord(
        schema_version=BUILD_EXECUTION_SCHEMA_VERSION,
        build_id="build-002",
        manifest_id="c" * 64,
        status=BuildStatus.SUCCEEDED,
        started_at=started,
        finished_at=finished,
        log_locator=None,
    )
    assert completed.manifest_id == "c" * 64
    assert completed.status is BuildStatus.SUCCEEDED
    assert completed.finished_at == finished
    assert completed.log_locator is None

    payload_field_names = {field.name for field in dataclasses.fields(BuildManifestPayload)}
    runtime_fields = {
        "build_id",
        "manifest_id",
        "status",
        "started_at",
        "finished_at",
        "log_locator",
    }
    assert runtime_fields.isdisjoint(payload_field_names)

    with pytest.raises((AttributeError, TypeError)):
        in_progress.status = BuildStatus.FAILED  # type: ignore[misc]

    with pytest.raises(ArtifactValidationError):
        BuildExecutionRecord(
            schema_version=999,
            build_id="build-003",
            manifest_id=None,
            status=BuildStatus.PENDING,
            started_at=started,
            finished_at=None,
            log_locator=None,
        )

    with pytest.raises(ArtifactValidationError):
        BuildExecutionRecord(
            schema_version=BUILD_EXECUTION_SCHEMA_VERSION,
            build_id="build-004",
            manifest_id="d" * 64,
            status=BuildStatus.FAILED,
            started_at=finished,
            finished_at=started,
            log_locator=None,
        )
