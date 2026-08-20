"""跨节点交接用 TaskResultPackage 的确定性模型、TOML 编解码和 framing。

任务产物 manifest 只保存可复现输入、计划、身份和 Blob；执行记录单独保存运行态。
两个 TOML 文件按固定文件名和大端长度 framing 计算 ``result_digest``，读取时重新
计算并验证任务身份、输出所有权和 Blob 字段，防止聚合 Job 接收陈旧或被篡改结果。
本模块不扫描目录、不执行任务、不上传对象。
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

import tomli
import tomli_w

from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from core.manifest_codec import canonical_json_bytes
from core.tasks import BuildContext, TaskIdentity, TaskPlan, TaskResult, TaskSpec

_SCHEMA_VERSION = 1
_MANIFEST_FILE = "task-artifact-manifest.toml"
_EXECUTION_FILE = "task-execution-record.toml"
_DIGEST_FILE = "result.sha256"


class TaskExecutionStatus(Enum):
    """任务交接记录的运行状态。"""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class TaskExecutionRecord:
    """不进入产物身份的单次任务运行记录。

    职责：
        保存 Jenkins/应用运行态、时间和日志 locator，与可复现 manifest 分离。

    参数：
        build_id/run_id/request_id: 运行关联与幂等身份。
        status: 成功、失败或取消状态。
        started_at/finished_at: 时区感知起止时间。
        log_locator: 脱敏日志持久定位。

    返回：
        无；不可变记录。

    异常：
        空身份、非法状态、时间倒退或不安全 locator 时抛出 ``ValueError``。

    约束与副作用：
        不保存凭据、发布版本或本地临时路径。
    """

    build_id: str
    run_id: str
    request_id: str
    status: TaskExecutionStatus
    started_at: datetime
    finished_at: datetime
    log_locator: str

    def __post_init__(self) -> None:
        """校验运行记录身份、时间和日志定位。"""
        for name in ("build_id", "run_id", "request_id", "log_locator"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or any(c in value for c in "\r\n"):
                raise ValueError(f"{name} 必须是非空无换行字符串")
        if not isinstance(self.status, TaskExecutionStatus):
            raise TypeError("status 必须是 TaskExecutionStatus")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at 不得早于 started_at")


@dataclass(frozen=True, slots=True)
class TaskArtifactManifestPayload:
    """可复现任务产物 manifest payload。"""

    schema_version: int
    build_context: BuildContext
    task_plan: TaskPlan
    task_identity: TaskIdentity
    explicit_input_digests: tuple[str, ...]
    artifacts: tuple[LogicalArtifact, ...]


@dataclass(frozen=True, slots=True)
class TaskArtifactManifest:
    """绑定 payload 和内容寻址 manifest ID 的任务清单。"""

    manifest_id: str
    payload: TaskArtifactManifestPayload

    def write(self, path: Path) -> None:
        """以稳定 TOML 原子写入任务产物 manifest。"""
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path 必须是绝对 Path")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = tomli_w.dumps(_toml_payload_dict(self.payload, self.manifest_id)).encode("utf-8")
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)


@dataclass(frozen=True, slots=True)
class TaskResultPackage:
    """包含双 TOML 文件和 framing 摘要的不可变结果包。"""

    directory: Path
    manifest: TaskArtifactManifest
    execution: TaskExecutionRecord
    result_digest: str

    @classmethod
    def write(
        cls,
        directory: Path,
        manifest: TaskArtifactManifest,
        execution: TaskExecutionRecord,
    ) -> TaskResultPackage:
        """原子写入任务结果包并返回 framing 摘要。"""
        if not isinstance(directory, Path) or not directory.is_absolute():
            raise ValueError("directory 必须是绝对 Path")
        if not isinstance(manifest, TaskArtifactManifest):
            raise TypeError("manifest 必须是 TaskArtifactManifest")
        if not isinstance(execution, TaskExecutionRecord):
            raise TypeError("execution 必须是 TaskExecutionRecord")
        if directory.exists():
            raise FileExistsError(f"结果包目录已存在: {directory}")
        directory.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{directory.name}.", dir=directory.parent))
        try:
            manifest_bytes = tomli_w.dumps(
                _toml_payload_dict(manifest.payload, manifest.manifest_id)
            ).encode("utf-8")
            execution_bytes = tomli_w.dumps(_execution_dict(execution)).encode("utf-8")
            (temporary / _MANIFEST_FILE).write_bytes(manifest_bytes)
            (temporary / _EXECUTION_FILE).write_bytes(execution_bytes)
            digest = _framed_digest(manifest_bytes, execution_bytes)
            (temporary / _DIGEST_FILE).write_text(digest + "\n", encoding="ascii")
            os.replace(temporary, directory)
        except Exception:
            if temporary.exists():
                for child in temporary.iterdir():
                    child.unlink()
                temporary.rmdir()
            raise
        return cls(directory, manifest, execution, digest)

    @classmethod
    def read(cls, directory: Path) -> TaskResultPackage:
        """读取结果包并校验 framing 摘要和任务 manifest。"""
        if not isinstance(directory, Path) or not directory.is_absolute():
            raise ValueError("directory 必须是绝对 Path")
        manifest_path = directory / _MANIFEST_FILE
        execution_path = directory / _EXECUTION_FILE
        digest_path = directory / _DIGEST_FILE
        manifest_bytes = manifest_path.read_bytes()
        execution_bytes = execution_path.read_bytes()
        expected = _framed_digest(manifest_bytes, execution_bytes)
        actual = digest_path.read_text(encoding="ascii").strip()
        if actual != expected:
            raise ValueError("TaskResultPackage result.sha256 校验失败")
        manifest = read_task_artifact_manifest(manifest_path)
        execution = _parse_execution(tomli.loads(execution_bytes.decode("utf-8")))
        return cls(directory, manifest, execution, expected)


class TaskArtifactManifestFactory:
    """由任务计划、身份和产物创建确定性 manifest。"""

    @staticmethod
    def create(
        context: BuildContext,
        plan: TaskPlan,
        identity: TaskIdentity,
        result: TaskResult,
        *,
        explicit_input_digests: tuple[str, ...] = (),
    ) -> TaskArtifactManifest:
        """校验任务结果并计算 payload SHA256。"""
        if plan.spec.dependencies:
            raise ValueError("任务 manifest 不允许非空 dependencies")
        expected = TaskIdentity.from_plan(plan, context, ())
        if expected != identity:
            raise ValueError("task_identity 与计划重新计算结果不一致")
        paths = [artifact.logical_path for artifact in result.outputs]
        if len(paths) != len(set(paths)) or frozenset(paths) != plan.spec.outputs:
            raise ValueError("任务实际输出与 TaskSpec.outputs 不一致")
        if any(
            len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            for digest in explicit_input_digests
        ):
            raise ValueError("explicit_input_digests 必须是小写 SHA256 元组")
        payload = TaskArtifactManifestPayload(
            _SCHEMA_VERSION, context, plan, identity, explicit_input_digests, result.outputs
        )
        manifest_id = hashlib.sha256(canonical_json_bytes(_payload_dict(payload, None))).hexdigest()
        return TaskArtifactManifest(manifest_id, payload)


def _artifact_dict(artifact: LogicalArtifact) -> dict[str, object]:
    """将逻辑产物转换为 TOML/JSON 共用的字典。"""
    return {
        "logical_path": artifact.logical_path,
        "kind": artifact.kind.value,
        "blob": {
            "locator": artifact.blob.locator,
            "sha256": artifact.blob.sha256,
            "size": artifact.blob.size,
        },
        "dependencies": list(artifact.dependencies),
        "subpackage_ids": sorted(artifact.subpackage_ids),
        "metadata": {
            "source_task": artifact.metadata.source_task,
            "source_revision": artifact.metadata.source_revision,
            "toolchain_digest": artifact.metadata.toolchain_digest,
            "attributes": {key: value for key, value in artifact.metadata.attributes},
        },
    }


def _payload_dict(
    payload: TaskArtifactManifestPayload, manifest_id: str | None
) -> dict[str, object]:
    """将任务 manifest payload 转为确定性字典。"""
    context = payload.build_context
    spec = payload.task_plan.spec
    result: dict[str, object] = {
        "schema_version": payload.schema_version,
        "build_context": {
            "schema_version": context.schema_version,
            "request_digest": context.request_digest,
            "revision": context.revision,
            "toolchain_digest": context.toolchain_digest,
            "baseline_id": context.baseline_id,
        },
        "task_plan": {
            "spec": {
                "name": spec.name,
                "dependencies": list(spec.dependencies),
                "outputs": sorted(spec.outputs, key=lambda value: value.encode("utf-8")),
                "implementation_version": spec.implementation_version,
                "execution_attributes": [list(pair) for pair in spec.execution_attributes],
            },
            "resolved_input_digest": payload.task_plan.resolved_input_digest,
            "config_digest": payload.task_plan.config_digest,
        },
        "task_identity": payload.task_identity.digest,
        "explicit_input_digests": list(payload.explicit_input_digests),
        "artifacts": [_artifact_dict(item) for item in payload.artifacts],
    }
    if manifest_id is not None:
        result["manifest_id"] = manifest_id
    return result


def _toml_payload_dict(
    payload: TaskArtifactManifestPayload,
    manifest_id: str | None,
) -> dict[str, object]:
    """生成不含 TOML 不支持的 None 值的 payload 字典。"""
    result = _payload_dict(payload, manifest_id)
    context_obj = result["build_context"]
    if not isinstance(context_obj, dict):
        raise TypeError("build_context 必须是 TOML table")
    context: dict[str, object] = cast(dict[str, object], context_obj)
    if context.get("baseline_id") is None:
        del context["baseline_id"]
    return result


def _execution_dict(record: TaskExecutionRecord) -> dict[str, object]:
    """将运行记录转为稳定 TOML 字典。"""
    return {
        "build_id": record.build_id,
        "run_id": record.run_id,
        "request_id": record.request_id,
        "status": record.status.value,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "log_locator": record.log_locator,
    }


def _framed_digest(manifest: bytes, execution: bytes) -> str:
    """按固定文件名和 UInt64 大端长度计算结果包摘要。"""
    chunks: list[bytes] = []
    for name, content in ((_MANIFEST_FILE, manifest), (_EXECUTION_FILE, execution)):
        name_bytes = name.encode("utf-8")
        chunks.extend(
            (
                struct.pack(">Q", len(name_bytes)),
                name_bytes,
                struct.pack(">Q", len(content)),
                content,
            )
        )
    return hashlib.sha256(b"".join(chunks)).hexdigest()


def read_task_artifact_manifest(path: Path) -> TaskArtifactManifest:
    """读取 TOML 任务 manifest 并重算 ID 与 TaskIdentity。"""
    raw = tomli.loads(path.read_text(encoding="utf-8"))
    root = raw
    stored_id = _required_str(root, "manifest_id")
    payload = _parse_payload(root)
    expected_id = hashlib.sha256(canonical_json_bytes(_payload_dict(payload, None))).hexdigest()
    if stored_id != expected_id:
        raise ValueError("task manifest_id 与 payload 不一致")
    expected_identity = TaskIdentity.from_plan(payload.task_plan, payload.build_context, ())
    if expected_identity != payload.task_identity:
        raise ValueError("task manifest 的 task_identity 不一致")
    if frozenset(item.logical_path for item in payload.artifacts) != payload.task_plan.spec.outputs:
        raise ValueError("task manifest artifacts 与 outputs 不一致")
    return TaskArtifactManifest(stored_id, payload)


def _parse_payload(root: dict[str, Any]) -> TaskArtifactManifestPayload:
    """从 TOML 根字典重建任务 manifest payload。"""
    context_data = cast(dict[str, Any], root["build_context"])
    context = BuildContext(
        _required_str(context_data, "request_digest"),
        _required_str(context_data, "revision"),
        _required_str(context_data, "toolchain_digest"),
        cast(str | None, context_data.get("baseline_id")),
        _required_int(context_data, "schema_version"),
    )
    plan_data = cast(dict[str, Any], root["task_plan"])
    spec_data = cast(dict[str, Any], plan_data["spec"])
    attributes: tuple[tuple[str, str], ...] = tuple(
        (pair[0], pair[1]) for pair in cast(list[list[str]], spec_data["execution_attributes"])
    )
    spec = TaskSpec(
        _required_str(spec_data, "name"),
        tuple(cast(list[str], spec_data["dependencies"])),
        frozenset(cast(list[str], spec_data["outputs"])),
        _required_str(spec_data, "implementation_version"),
        attributes,
    )
    plan = TaskPlan(
        spec,
        _required_str(plan_data, "resolved_input_digest"),
        _required_str(plan_data, "config_digest"),
    )
    artifacts = tuple(_parse_artifact(raw) for raw in cast(list[dict[str, Any]], root["artifacts"]))
    return TaskArtifactManifestPayload(
        _required_int(root, "schema_version"),
        context,
        plan,
        TaskIdentity(_required_str(root, "task_identity")),
        tuple(cast(list[str], root["explicit_input_digests"])),
        artifacts,
    )


def _parse_artifact(raw: dict[str, Any]) -> LogicalArtifact:
    """从 TOML 字典重建逻辑产物。"""
    blob = cast(dict[str, Any], raw["blob"])
    metadata = cast(dict[str, Any], raw["metadata"])
    attributes = cast(dict[str, str], metadata["attributes"])
    return LogicalArtifact(
        _required_str(raw, "logical_path"),
        ArtifactKind(_required_str(raw, "kind")),
        BlobRef(
            _required_str(blob, "locator"),
            _required_str(blob, "sha256"),
            _required_int(blob, "size"),
        ),
        tuple(cast(list[str], raw["dependencies"])),
        frozenset(cast(list[int], raw["subpackage_ids"])),
        ArtifactMetadata(
            _required_str(metadata, "source_task"),
            _required_str(metadata, "source_revision"),
            _required_str(metadata, "toolchain_digest"),
            tuple(sorted(attributes.items(), key=lambda item: item[0].encode("utf-8"))),
        ),
    )


def _parse_execution(raw: dict[str, Any]) -> TaskExecutionRecord:
    """从 TOML 字典重建运行记录。"""
    return TaskExecutionRecord(
        _required_str(raw, "build_id"),
        _required_str(raw, "run_id"),
        _required_str(raw, "request_id"),
        TaskExecutionStatus(_required_str(raw, "status")),
        cast(datetime, raw["started_at"]),
        cast(datetime, raw["finished_at"]),
        _required_str(raw, "log_locator"),
    )


def _required_str(data: dict[str, Any], key: str) -> str:
    """读取必需字符串字段。"""
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"缺少或非法字符串字段: {key}")
    return value


def _required_int(data: dict[str, Any], key: str) -> int:
    """读取必需整数域字段。"""
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"缺少或非法整数域字段: {key}")
    return value
