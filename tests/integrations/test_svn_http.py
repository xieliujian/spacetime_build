"""验证 SVN 读取适配器和标准库 HTTP 传输的边界行为。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import integrations.http as http_module
from core.errors import ToolExecutionError
from integrations.http import UrllibHttpTransport
from integrations.svn import SvnSourceProvider
from ports.http import HttpMethod, HttpRequest
from ports.process import ProcessOutcome, ProcessRequest, ProcessResult
from ports.process import CancellationToken
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
            destination = Path(request.arguments[-1])
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
