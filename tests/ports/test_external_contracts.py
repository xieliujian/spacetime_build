"""验证第二层外部系统端口的不可变数据和边界约束。

这些测试只覆盖内存契约，不启动网络、Unity、Jenkins 或版本控制命令；适配器测试将在
端口契约稳定后单独验证外部副作用和故障映射。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from configuration.model import SecretRef
from ports.ci import CiJobHandle, CiJobRequest, CiJobState, CiJobStatus
from ports.http import HttpMethod, HttpRequest, SecretHttpBinding, SecretHttpTarget
from ports.secrets import SecretLeaseRequest
from ports.source import ResolvedSource, SourceRef, SourceSnapshot
from ports.storage import CompareAndSwapRequest, PutObjectRequest, validate_object_key
from ports.unity import UnityBatchRequest
from ports.workspace import WorkspaceRequest


def test_http_request_rejects_userinfo_duplicate_headers_and_unbounded_response() -> None:
    """验证 HTTP 请求拒绝凭据 URL、重复头和非法响应上限。"""
    with pytest.raises(ValueError):
        HttpRequest(HttpMethod.GET, "https://user:password@example.test/data")
    with pytest.raises(ValueError):
        HttpRequest(HttpMethod.GET, "https://example.test", headers=(("X-A", "1"), ("x-a", "2")))
    with pytest.raises(ValueError):
        HttpRequest(HttpMethod.GET, "https://example.test", max_response_bytes=0)


def test_http_secret_binding_allows_only_authorization_slot() -> None:
    """验证 Authorization binding 不能伪装成任意头槽位。"""
    binding = SecretHttpBinding("token", SecretHttpTarget.AUTHORIZATION, "Authorization")
    assert binding.binding_id == "token"
    with pytest.raises(ValueError):
        SecretHttpBinding("token", SecretHttpTarget.AUTHORIZATION, "X-Token")


def test_secret_lease_request_is_frozen_and_does_not_expose_reference() -> None:
    """验证秘密租约请求不可变且字符串表示不暴露引用。"""
    request = SecretLeaseRequest(SecretRef("secret://ci/token"), "jenkins", ("token",))
    assert "ci/token" not in repr(request)
    with pytest.raises((AttributeError, TypeError)):
        request.purpose = "changed"  # type: ignore[misc]


def test_workspace_request_rejects_path_escape_build_ids(tmp_path: Path) -> None:
    """验证工作区 build ID 不能形成路径逃逸。"""
    with pytest.raises(ValueError):
        WorkspaceRequest(tmp_path, "../outside")
    request = WorkspaceRequest(tmp_path, "build-001")
    assert request.build_id == "build-001"


def test_source_revision_and_snapshot_require_fixed_identity(tmp_path: Path) -> None:
    """验证 HEAD 只能存在于待解析引用，快照必须携带固定 revision 和树摘要。"""
    source = SourceRef("svn", "https://svn.example/project", "HEAD")
    resolved = ResolvedSource(source.provider, source.url, 17, "repo-uuid")
    digest = hashlib.sha256(b"tree").hexdigest()
    snapshot = SourceSnapshot(resolved, tmp_path, digest)
    assert snapshot.source.revision == 17
    with pytest.raises(ValueError):
        ResolvedSource("svn", source.url, 0, "repo-uuid")


def test_unity_request_requires_absolute_paths_and_explicit_method(tmp_path: Path) -> None:
    """验证 Unity 请求必须固定工程、日志和执行方法。"""
    request = UnityBatchRequest(
        Path("C:/Unity/Unity.exe"),
        tmp_path,
        "Build.Entry",
        ("platform=Windows",),
        tmp_path / "unity.log",
        60,
    )
    assert request.method == "Build.Entry"
    with pytest.raises(ValueError):
        UnityBatchRequest(Path("Unity.exe"), tmp_path, "Build.Entry", (), tmp_path / "x.log", 1)


def test_ci_request_and_handle_reject_duplicate_parameters() -> None:
    """验证 CI 请求参数大小写不敏感去重并保留幂等键。"""
    request = CiJobRequest("resource-build", (("BUILD_ID", "b1"),), "request-1")
    assert request.idempotency_key == "request-1"
    assert (
        CiJobStatus(CiJobHandle("resource-build", "queue-1"), CiJobState.QUEUED).state
        is CiJobState.QUEUED
    )
    with pytest.raises(ValueError):
        CiJobRequest("resource-build", (("A", "1"), ("a", "2")), "request-1")


def test_object_key_and_put_request_require_deterministic_safe_identity() -> None:
    """验证对象键拒绝 dot 段、反斜杠和 URL 绕过，写入摘要必须固定长度。"""
    assert validate_object_key("version/1/file") == "version/1/file"
    with pytest.raises(ValueError):
        validate_object_key("version/../file")
    with pytest.raises(ValueError):
        validate_object_key("version/%2f/file")
    with pytest.raises(ValueError):
        PutObjectRequest("file.bin", b"data", "bad")


def test_cas_request_rejects_negative_generation() -> None:
    """验证版本入口 CAS 的期望代际不能为负数。"""
    with pytest.raises(ValueError):
        CompareAndSwapRequest("version/current", -1, b"payload")
