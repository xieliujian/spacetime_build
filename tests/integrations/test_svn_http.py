"""验证 SVN 读取适配器和标准库 HTTP 传输的边界行为。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import integrations.http as http_module
from core.errors import ToolExecutionError
from integrations.http import UrllibHttpTransport
from configuration.model import SecretRef
from integrations.svn import SvnSourceProvider
from ports.http import HttpMethod, HttpRequest
from ports.process import (
    ProcessOutcome,
    ProcessRequest,
    ProcessResult,
    SecretBindingTarget,
)
from ports.process import CancellationToken
from ports.secrets import SecretLease, SecretLeaseRequest
from ports.source import SourceRef


class _SvnRunner:
    """向 SVN 适配器提供受控 XML 和 export fixture。"""

    def __init__(self, tmp_path: Path) -> None:
        """保存 fixture 根目录和请求记录。"""
        self.tmp_path = tmp_path
        self.requests: list[ProcessRequest] = []

    def run(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        """按命令类型写入受控 stdout 并返回成功结果。"""
        del cancellation
        self.requests.append(request)
        if request.arguments[0] == "info":
            request.stdout_path.write_text(
                '<info><entry revision="42"><repository><uuid>repo-1</uuid></repository></entry></info>',
                encoding="utf-8",
            )
        else:
            password_index = (
                request.arguments.index("--password")
                if "--password" in request.arguments
                else len(request.arguments)
            )
            destination = Path(request.arguments[password_index - 1])
            (destination / "source.txt").write_text("source", encoding="utf-8")
            request.stdout_path.write_bytes(b"")
        return ProcessResult(
            ProcessOutcome.COMPLETED,
            0,
            0.01,
            request.stdout_path,
            request.stderr_path,
            0,
            0,
        )


class _SvnLease(SecretLease):
    """记录 SVN 一次进程调用的秘密解析和关闭次数。"""

    def __init__(self) -> None:
        """初始化租约状态。"""
        self.resolve_calls: list[str] = []
        self.close_calls = 0

    def resolve(self, binding_id: str) -> str:
        """按 opaque binding ID 返回测试密码。"""
        self.resolve_calls.append(binding_id)
        return "svn-secret"

    def close(self) -> None:
        """记录 ProcessRunner 关闭租约。"""
        self.close_calls += 1

    def __repr__(self) -> str:
        """返回不含秘密的租约表示。"""
        return "SvnLease(<redacted>)"


class _SvnSecretProvider:
    """为每次 SVN 命令申请一个独立短期租约。"""

    def __init__(self) -> None:
        """初始化租约申请记录。"""
        self.requests: list[SecretLeaseRequest] = []
        self.leases: list[_SvnLease] = []

    def acquire(self, request: SecretLeaseRequest) -> SecretLease:
        """保存租约请求并返回只供本次命令使用的租约。"""
        self.requests.append(request)
        lease = _SvnLease()
        self.leases.append(lease)
        return lease


class _CredentialSvnRunner(_SvnRunner):
    """在执行边界解析 SVN binding 并模拟 ProcessRunner 的关闭责任。"""

    def run(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        """确认密码只在 ProcessRunner 边界可见，并关闭请求租约。"""
        assert len(request.secret_bindings) == 1
        binding = request.secret_bindings[0]
        assert binding.target is SecretBindingTarget.ARGUMENT
        assert binding.slot == str(len(request.arguments) - 1)
        assert request.arguments[-1] == ""
        assert request.redacted_argument_indexes == frozenset({len(request.arguments) - 1})
        assert request.secret_lease is not None
        assert request.secret_lease.resolve(binding.binding_id) == "svn-secret"
        request.secret_lease.close()
        return super().run(request, cancellation)


class _FailingSvnRunner:
    """在 ProcessRunner 接管租约前注入命令启动失败。"""

    def run(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        """抛出不含秘密的启动异常。"""
        del request, cancellation
        raise RuntimeError("svn process unavailable")


class _Response:
    """模拟 urllib 响应的最小上下文管理协议。"""

    status = 200
    headers = {"Content-Length": "2"}

    def __enter__(self) -> "_Response":
        """返回自身。"""
        return self

    def __exit__(self, *_args: object) -> None:
        """结束模拟响应。"""

    def read(self, _size: int = -1) -> bytes:
        """返回固定响应体后结束。"""
        if hasattr(self, "_read"):
            return b""
        self._read = True
        return b"ok"


def test_svn_provider_fixes_head_revision_and_hashes_export_tree(tmp_path: Path) -> None:
    """验证 SVN HEAD 解析为固定 revision，export 结果生成稳定树摘要。"""
    runner = _SvnRunner(tmp_path)
    provider = SvnSourceProvider(Path("C:/svn.exe"), tmp_path / "commands", runner)
    resolved = provider.resolve_revision(SourceRef("svn", "https://svn.example/repo", "HEAD"))
    destination = tmp_path / "checkout"
    snapshot = provider.materialize(resolved, destination)
    assert resolved.revision == 42
    assert snapshot.root == destination
    assert len(snapshot.tree_sha256) == 64
    assert runner.requests[0].arguments[:2] == ("info", "--xml")


def test_svn_provider_binds_short_lived_password_for_each_command(tmp_path: Path) -> None:
    """验证 SVN info/export 都只通过脱敏 argv binding 使用短期密码。"""
    runner = _CredentialSvnRunner(tmp_path)
    secret_provider = _SvnSecretProvider()
    provider = SvnSourceProvider(
        Path("C:/svn.exe"),
        tmp_path / "commands",
        runner,
        credential=SecretRef("secret://env/SVN_PASSWORD"),
        secret_provider=secret_provider,
    )

    resolved = provider.resolve_revision(SourceRef("svn", "https://svn.example/repo", "HEAD"))
    provider.materialize(resolved, tmp_path / "checkout")

    assert len(secret_provider.requests) == 2
    assert all(request.binding_ids == ("svn-password",) for request in secret_provider.requests)
    assert all(lease.resolve_calls == ["svn-password"] for lease in secret_provider.leases)
    assert all(lease.close_calls == 1 for lease in secret_provider.leases)
    assert all("svn-secret" not in repr(request) for request in runner.requests)


def test_svn_provider_closes_lease_when_process_runner_fails(tmp_path: Path) -> None:
    """验证 SVN 进程端口在接管租约前失败时适配器仍然清理租约。"""
    secret_provider = _SvnSecretProvider()
    provider = SvnSourceProvider(
        Path("C:/svn.exe"),
        tmp_path / "commands",
        _FailingSvnRunner(),
        credential=SecretRef("secret://env/SVN_PASSWORD"),
        secret_provider=secret_provider,
    )

    with pytest.raises(RuntimeError, match="process unavailable"):
        provider.resolve_revision(SourceRef("svn", "https://svn.example/repo", "HEAD"))

    assert secret_provider.leases[0].close_calls == 1


def test_urllib_transport_rejects_response_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 HTTP 传输在读取前后都执行响应大小限制。"""

    def fake_urlopen(_request: object, **_kwargs: Any) -> _Response:
        """返回固定的超限响应。"""
        return _Response()

    monkeypatch.setattr(http_module.urllib_request, "urlopen", fake_urlopen)
    with pytest.raises(ToolExecutionError, match="响应超过"):
        UrllibHttpTransport().send(
            HttpRequest(HttpMethod.GET, "https://example.test", max_response_bytes=1)
        )
