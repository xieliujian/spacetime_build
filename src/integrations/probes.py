"""外部系统探针与可审计验收证据。

本模块把 SVN、Unity、Jenkins、Secrets 和 ObjectStore/CDN 的最小真实调用统一成
``ProbeEvidence``。探针只接收已经装配的端口，不猜测可执行文件、地址或凭据；缺少
装配项返回 ``PENDING``，调用失败返回 ``FAILED``，只有完成结构化回读和摘要校验才
返回 ``PASSED``。证据文件只保存公开摘要，不保存秘密、原始日志和秘密 locator。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

from configuration.model import SecretRef
from integrations.unity import UnityBatchRunner
from observability.redaction import redact_text
from ports.ci import CiJobClient, CiJobRequest, CiJobState
from ports.secrets import SecretLeaseRequest, SecretProvider
from ports.source import SourceProvider, SourceRef
from ports.storage import ObjectStore, PutObjectRequest, StoredObject
from ports.unity import UnityBatchRequest


class ProbeStatus(str, Enum):
    """外部探针的稳定结果状态。"""

    PASSED = "passed"
    PENDING = "pending"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProbeEvidence:
    """一项不含秘密的外部系统验收证据。"""

    name: str
    status: ProbeStatus
    summary: str
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """校验身份字段并对探针摘要做统一脱敏。"""
        if (
            not isinstance(self.name, str)
            or not self.name
            or any(character in self.name for character in "\r\n")
        ):
            raise ValueError("name 必须是非空且不含换行的字符串")
        if not isinstance(self.status, ProbeStatus):
            raise TypeError("status 必须是 ProbeStatus")
        if not isinstance(self.summary, str) or not self.summary:
            raise ValueError("summary 必须是非空字符串")
        if not isinstance(self.details, tuple):
            raise TypeError("details 必须是 tuple")
        normalized: list[tuple[str, str]] = []
        for key, value in self.details:
            if not isinstance(key, str) or not key or any(character in key for character in "\r\n"):
                raise ValueError("details key 非法")
            if not isinstance(value, str) or any(character in value for character in "\r\n"):
                raise ValueError("details value 非法")
            normalized.append((key, redact_text(value)))
        object.__setattr__(self, "summary", redact_text(self.summary))
        object.__setattr__(self, "details", tuple(sorted(normalized)))


class _EvidenceWriter(Protocol):
    """可替换的证据写入端口。"""

    def write(self, evidence: tuple[ProbeEvidence, ...]) -> Path:
        """持久化一组确定性证据并返回文件路径。"""
        ...


class JsonProbeEvidenceWriter:
    """以规范 JSON 原子写入探针证据。"""

    def __init__(self, path: Path) -> None:
        """绑定绝对证据文件路径，不在构造阶段写文件。"""
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path 必须是绝对 Path")
        self._path = path

    def write(self, evidence: tuple[ProbeEvidence, ...]) -> Path:
        """以临时文件加原子替换写入公开摘要。"""
        if not isinstance(evidence, tuple) or any(
            not isinstance(item, ProbeEvidence) for item in evidence
        ):
            raise TypeError("evidence 必须是 tuple[ProbeEvidence, ...]")
        payload = {
            "schema_version": 1,
            "probes": [
                {
                    "details": dict(item.details),
                    "name": item.name,
                    "status": item.status.value,
                    "summary": item.summary,
                }
                for item in sorted(evidence, key=lambda item: item.name.encode("utf-8"))
            ],
        }
        content = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return self._path


def pending_probe(name: str, reason: str) -> ProbeEvidence:
    """创建明确表示外部装配缺失的 PENDING 证据。"""
    return ProbeEvidence(name, ProbeStatus.PENDING, reason)


def probe_svn(
    provider: SourceProvider | None,
    source: SourceRef | None,
    destination: Path | None,
) -> ProbeEvidence:
    """解析并物化一次固定 SVN revision，验证快照摘要。"""
    name = "svn"
    if provider is None or source is None or destination is None:
        return pending_probe(name, "未提供 SVN provider、source 或隔离 destination")
    try:
        if not destination.is_absolute() or destination.exists():
            raise ValueError("SVN destination 必须是尚不存在的绝对目录")
        resolved = provider.resolve_revision(source)
        snapshot = provider.materialize(resolved, destination)
        return ProbeEvidence(
            name,
            ProbeStatus.PASSED,
            "SVN revision 已解析并完成隔离快照",
            (
                ("provider", resolved.provider),
                ("revision", str(resolved.revision)),
                (
                    "repository_id_sha256",
                    hashlib.sha256(resolved.repository_id.encode()).hexdigest(),
                ),
                ("tree_sha256", snapshot.tree_sha256),
            ),
        )
    except Exception as exc:
        return ProbeEvidence(name, ProbeStatus.FAILED, "SVN 探针失败", (("error", str(exc)),))


def probe_unity(
    runner: UnityBatchRunner | None,
    request: UnityBatchRequest | None,
) -> ProbeEvidence:
    """执行一次显式 Unity batch 请求并确认结构化结果。"""
    name = "unity"
    if runner is None or request is None:
        return pending_probe(name, "未提供 Unity runner 或 batch request")
    try:
        result = runner.run(request)
        if not result.success:
            missing = ",".join(str(path) for path in result.missing_outputs)
            return ProbeEvidence(
                name,
                ProbeStatus.FAILED,
                "Unity batch 未通过",
                (("exit_code", str(result.exit_code)), ("missing_outputs", missing)),
            )
        return ProbeEvidence(
            name,
            ProbeStatus.PASSED,
            "Unity batch 已完成且输出存在",
            (("output_count", str(len(request.expected_outputs))),),
        )
    except Exception as exc:
        return ProbeEvidence(name, ProbeStatus.FAILED, "Unity 探针失败", (("error", str(exc)),))


def probe_jenkins(
    client: CiJobClient | None,
    request: CiJobRequest | None,
    *,
    status_reader: Callable[[object], object] | None = None,
) -> ProbeEvidence:
    """触发 Jenkins Job 并验证一次终态查询。"""
    name = "jenkins"
    if client is None or request is None:
        return pending_probe(name, "未提供 Jenkins client 或 Job request")
    try:
        handle = client.trigger(request)
        status = status_reader(handle) if status_reader is not None else client.get_status(handle)
        state = getattr(status, "state", None)
        if state is CiJobState.SUCCESS:
            return ProbeEvidence(name, ProbeStatus.PASSED, "Jenkins Job 已成功完成")
        if state in {CiJobState.QUEUED, CiJobState.RUNNING}:
            return ProbeEvidence(name, ProbeStatus.PENDING, "Jenkins Job 尚未进入成功终态")
        return ProbeEvidence(
            name,
            ProbeStatus.FAILED,
            "Jenkins Job 未成功完成",
            (("state", str(getattr(state, "value", state))),),
        )
    except Exception as exc:
        return ProbeEvidence(name, ProbeStatus.FAILED, "Jenkins 探针失败", (("error", str(exc)),))


def probe_secret(
    provider: SecretProvider | None,
    reference: SecretRef | None,
) -> ProbeEvidence:
    """申请并关闭秘密租约，只验证租约生命周期，不记录秘密值。"""
    name = "secrets"
    if provider is None or reference is None:
        return pending_probe(name, "未提供 SecretProvider 或 SecretRef")
    lease = None
    try:
        lease = provider.acquire(SecretLeaseRequest(reference, "external-probe", ("probe",)))
        value = lease.resolve("probe")
        if not isinstance(value, str) or value == "":
            raise ValueError("秘密租约返回空值")
        return ProbeEvidence(name, ProbeStatus.PASSED, "秘密租约可申请、解析并关闭")
    except Exception as exc:
        return ProbeEvidence(name, ProbeStatus.FAILED, "Secrets 探针失败", (("error", str(exc)),))
    finally:
        if lease is not None:
            lease.close()


def probe_object_store(
    store: ObjectStore | None,
    key: str | None,
    content: bytes = b"spacetime-build-external-probe\n",
) -> ProbeEvidence:
    """写入并回读一个显式探针对象，作为供应商 CDN/CAS 端口证据。"""
    name = "object-store"
    if store is None or key is None:
        return pending_probe(name, "未提供 ObjectStore 和 probe key")
    try:
        digest = hashlib.sha256(content).hexdigest()
        reference = store.put(PutObjectRequest(key, content, digest))
        observed = store.verify(StoredObject(key, digest, len(content)))
        if reference != StoredObject(key, digest, len(content)):
            raise ValueError("对象存储 PUT 回执不一致")
        if not observed.exists or observed.sha256 != digest or observed.size != len(content):
            raise ValueError("对象存储回读摘要或大小不一致")
        return ProbeEvidence(
            name,
            ProbeStatus.PASSED,
            "对象已写入并完成摘要回读",
            (("key_sha256", hashlib.sha256(key.encode()).hexdigest()), ("size", str(len(content)))),
        )
    except Exception as exc:
        return ProbeEvidence(
            name, ProbeStatus.FAILED, "ObjectStore/CDN 探针失败", (("error", str(exc)),)
        )


class ExternalProbeSuite:
    """顺序执行五类外部探针并可选写入统一证据文件。"""

    def run(
        self,
        probes: tuple[Callable[[], ProbeEvidence], ...],
        *,
        writer: _EvidenceWriter | None = None,
    ) -> tuple[ProbeEvidence, ...]:
        """执行探针、保留每项状态，并可原子写入证据。"""
        if not isinstance(probes, tuple):
            raise TypeError("probes 必须是 tuple")
        evidence = tuple(probe() for probe in probes)
        if writer is not None:
            writer.write(evidence)
        return evidence


__all__ = [
    "ExternalProbeSuite",
    "JsonProbeEvidenceWriter",
    "ProbeEvidence",
    "ProbeStatus",
    "pending_probe",
    "probe_jenkins",
    "probe_object_store",
    "probe_secret",
    "probe_svn",
    "probe_unity",
]
