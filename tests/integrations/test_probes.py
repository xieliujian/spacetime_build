"""外部系统探针状态、秘密边界和确定性证据文件测试。"""

import json
from pathlib import Path

from configuration.model import SecretRef
from integrations.probes import (
    ExternalProbeSuite,
    JsonProbeEvidenceWriter,
    ProbeStatus,
    probe_object_store,
    probe_secret,
    probe_svn,
)
from integrations.secrets import EnvironmentSecretProvider
from integrations.storage import FileSystemObjectStore
from ports.ci import CiJobHandle, CiJobRequest, CiJobState, CiJobStatus
from ports.source import ResolvedSource, SourceRef, SourceSnapshot


class _Source:
    """返回确定性 SVN 快照的源码端口替身。"""

    def resolve_revision(self, source: SourceRef) -> ResolvedSource:
        """固定一个测试 revision。"""
        return ResolvedSource(source.provider, source.url, 42, "repo-uuid")

    def materialize(self, source: ResolvedSource, destination: Path) -> SourceSnapshot:
        """创建隔离目录并返回固定树摘要。"""
        destination.mkdir()
        return SourceSnapshot(source, destination, "a" * 64)


class _Ci:
    """返回成功终态的 Jenkins client 替身。"""

    def trigger(self, request: CiJobRequest) -> CiJobHandle:
        """返回固定句柄。"""
        return CiJobHandle(request.job_name, "7", 11)

    def get_status(self, handle: CiJobHandle) -> CiJobStatus:
        """返回成功状态。"""
        return CiJobStatus(handle, CiJobState.SUCCESS, "SUCCESS")

    def cancel(self, handle: CiJobHandle) -> bool:
        """测试替身不执行取消。"""
        del handle
        return False


def test_svn_probe_records_revision_without_repository_identity(tmp_path: Path) -> None:
    """验证 SVN 证据只保留 revision 和摘要，不保留 repository ID 原文。"""
    evidence = probe_svn(
        _Source(),
        SourceRef("svn", "https://svn.example/project", "HEAD"),
        tmp_path / "probe-source",
    )

    assert evidence.status is ProbeStatus.PASSED
    assert dict(evidence.details)["revision"] == "42"
    assert "repo-uuid" not in str(evidence)


def test_missing_probe_dependencies_are_pending() -> None:
    """验证没有真实外部装配时只能返回 PENDING。"""
    assert probe_svn(None, None, None).status is ProbeStatus.PENDING
    assert probe_object_store(None, None).status is ProbeStatus.PENDING


def test_secret_probe_closes_environment_lease(monkeypatch) -> None:
    """验证秘密可用性探针不返回秘密值。"""
    monkeypatch.setenv("SPACETIME_PROBE_SECRET", "value-that-must-not-appear")

    evidence = probe_secret(
        EnvironmentSecretProvider(),
        SecretRef("secret://env/SPACETIME_PROBE_SECRET"),
    )

    assert evidence.status is ProbeStatus.PASSED
    assert "value-that-must-not-appear" not in str(evidence)


def test_object_store_probe_round_trips_and_writer_is_atomic(tmp_path: Path) -> None:
    """验证本地 CDN 替身写入、回读和 JSON 证据写入。"""
    store = FileSystemObjectStore(tmp_path / "objects")
    evidence = ExternalProbeSuite().run(
        (lambda: probe_object_store(store, "probes/object-store.txt"),),
        writer=JsonProbeEvidenceWriter(tmp_path / "evidence" / "external.json"),
    )

    assert evidence[0].status is ProbeStatus.PASSED
    payload = json.loads((tmp_path / "evidence" / "external.json").read_text(encoding="utf-8"))
    assert payload["probes"][0]["status"] == "passed"


def test_jenkins_probe_success_is_available_through_suite() -> None:
    """验证 Jenkins 成功状态可以作为统一探针回执。"""
    from integrations.probes import probe_jenkins

    evidence = probe_jenkins(_Ci(), CiJobRequest("release", (), "run-1"))

    assert evidence.status is ProbeStatus.PASSED
