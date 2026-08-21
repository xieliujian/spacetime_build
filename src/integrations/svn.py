"""SVN 读取侧命令行适配器。

适配器只执行受控的 ``info`` 和 ``export`` 参数序列，并把 XML 解析为结构化源码身份。
它不执行 commit、copy 或 relocate；这些写操作属于后续分支构建能力，不能从普通资源任务
隐式触发。SVN 密码只通过进程端口的短期 opaque binding 传递。
"""

from __future__ import annotations

import hashlib
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from configuration.model import SecretRef
from core.errors import SourceError
from integrations.secrets import SecretLeaseGuard
from ports.process import (
    ProcessOutcome,
    ProcessRequest,
    ProcessRunner,
    SecretBindingTarget,
    SecretProcessBinding,
)
from ports.secrets import SecretLeaseRequest, SecretProvider
from ports.source import ResolvedSource, SourceProvider, SourceRef, SourceSnapshot


class SvnSourceProvider(SourceProvider):
    """通过注入的 ProcessRunner 解析和物化 SVN revision。"""

    def __init__(
        self,
        executable: Path,
        temp_root: Path,
        process_runner: ProcessRunner,
        *,
        credential: SecretRef | None = None,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        """保存 SVN 工具、临时根目录和可选的短期密码依赖。"""
        if not executable.is_absolute() or not temp_root.is_absolute():
            raise ValueError("executable 和 temp_root 必须是绝对路径")
        if credential is not None and not isinstance(credential, SecretRef):
            raise TypeError("credential 必须是 SecretRef 或 None")
        if credential is None and secret_provider is not None:
            raise ValueError("未提供 credential 时不得绑定 secret_provider")
        if credential is not None and not callable(getattr(secret_provider, "acquire", None)):
            raise ValueError("使用 credential 时必须提供 SecretProvider")
        self._executable = executable
        self._temp_root = temp_root
        self._process_runner = process_runner
        self._credential = credential
        self._secret_provider = secret_provider

    def resolve_revision(self, source: SourceRef) -> ResolvedSource:
        """使用 svn info XML 将 HEAD 固定为仓库 revision。"""
        if not isinstance(source, SourceRef):
            raise TypeError("source 必须是 SourceRef")
        if source.provider.casefold() != "svn":
            raise ValueError("SvnSourceProvider 只接受 svn provider")
        arguments = ["info", "--xml"]
        if source.revision != "HEAD":
            arguments.extend(("--revision", source.revision))
        arguments.append(source.url)
        output = self._run(tuple(arguments))
        try:
            root = ElementTree.fromstring(output)
            entry = root.find("entry")
            if entry is None:
                raise ValueError("缺少 entry")
            revision_text = entry.get("revision")
            if revision_text is None:
                raise ValueError("缺少 entry revision")
            repository = entry.find("repository")
            uuid = repository.findtext("uuid") if repository is not None else None
            if not uuid:
                raise ValueError("缺少 repository uuid")
            return ResolvedSource("svn", source.url, int(revision_text), uuid)
        except (ElementTree.ParseError, ValueError) as exc:
            raise SourceError("SVN info XML 无法解析") from exc

    def materialize(self, source: ResolvedSource, destination: Path) -> SourceSnapshot:
        """把固定 revision export 到目标目录并生成确定性树摘要。"""
        if not isinstance(source, ResolvedSource):
            raise TypeError("source 必须是 ResolvedSource")
        if not destination.is_absolute():
            raise ValueError("destination 必须是绝对路径")
        destination.mkdir(parents=True, exist_ok=False)
        self._run(
            ("export", "--force", "--revision", str(source.revision), source.url, str(destination))
        )
        digest = hashlib.sha256()
        for path in sorted(
            destination.rglob("*"),
            key=lambda item: item.relative_to(destination).as_posix().encode("utf-8"),
        ):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(destination).as_posix().encode("utf-8")
            content = path.read_bytes()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return SourceSnapshot(source, destination, digest.hexdigest())

    def _run(self, arguments: tuple[str, ...]) -> bytes:
        """执行 SVN 参数序列并读取 stdout，失败只返回稳定业务错误。"""
        self._temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._temp_root) as directory:
            root = Path(directory)
            lease: SecretLeaseGuard | None = None
            try:
                request_arguments = arguments
                bindings: tuple[SecretProcessBinding, ...] = ()
                redacted_indexes = frozenset[int]()
                if self._credential is not None:
                    provider = self._secret_provider
                    if provider is None:
                        raise RuntimeError("SVN 凭据 provider 未装配")
                    raw_lease = provider.acquire(
                        SecretLeaseRequest(
                            self._credential,
                            "svn-command",
                            ("svn-password",),
                        )
                    )
                    lease = SecretLeaseGuard(raw_lease)
                    password_index = len(arguments) + 1
                    request_arguments = (*arguments, "--password", "")
                    bindings = (
                        SecretProcessBinding(
                            "svn-password",
                            SecretBindingTarget.ARGUMENT,
                            str(password_index),
                        ),
                    )
                    redacted_indexes = frozenset({password_index})
                request = ProcessRequest(
                    self._executable,
                    request_arguments,
                    root,
                    root / "stdout.log",
                    root / "stderr.log",
                    timeout_seconds=300,
                    redacted_argument_indexes=redacted_indexes,
                    secret_bindings=bindings,
                    secret_lease=lease,
                )
                result = self._process_runner.run(request)
                if result.outcome is not ProcessOutcome.COMPLETED or result.exit_code != 0:
                    raise SourceError("SVN 命令执行失败")
                return request.stdout_path.read_bytes()
            finally:
                if lease is not None:
                    lease.close()
