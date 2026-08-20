"""版本控制读取和受控源码快照的端口契约。

源码端口把 ``HEAD`` 解析成固定 revision，业务层只接收结构化 revision 和快照摘要；所有
命令参数、认证细节和 working-copy 操作由适配器处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SourceRef:
    """描述一个待解析的源码仓库引用。"""

    provider: str
    url: str
    revision: str

    def __post_init__(self) -> None:
        """校验 provider、仓库 URL 和 revision 形式。"""
        if not isinstance(self.provider, str) or not self.provider:
            raise ValueError("provider 必须是非空字符串")
        if not isinstance(self.url, str) or not self.url or any(c in self.url for c in "\r\n"):
            raise ValueError("url 必须是非空且不含换行的字符串")
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError("revision 必须是非空字符串")
        if self.revision != "HEAD" and re.fullmatch(r"[1-9][0-9]*", self.revision) is None:
            raise ValueError("revision 必须是 HEAD 或正整数")


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """已经固定 revision 的源码引用。"""

    provider: str
    url: str
    revision: int
    repository_id: str

    def __post_init__(self) -> None:
        """校验固定 revision 和仓库身份。"""
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision <= 0
        ):
            raise ValueError("revision 必须是正整数")
        if not isinstance(self.repository_id, str) or not self.repository_id:
            raise ValueError("repository_id 必须是非空字符串")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """已经物化到隔离目录的源码快照摘要。"""

    source: ResolvedSource
    root: Path
    tree_sha256: str

    def __post_init__(self) -> None:
        """校验快照根路径和 SHA256 摘要。"""
        if not isinstance(self.root, Path) or not self.root.is_absolute():
            raise ValueError("root 必须是绝对 Path")
        if not isinstance(self.tree_sha256, str) or len(self.tree_sha256) != 64:
            raise ValueError("tree_sha256 必须是 64 位十六进制摘要")
        if any(c not in "0123456789abcdef" for c in self.tree_sha256):
            raise ValueError("tree_sha256 必须是小写十六进制摘要")


class SourceProvider(Protocol):
    """源码 revision 解析和快照物化协议。"""

    def resolve_revision(self, source: SourceRef) -> ResolvedSource:
        """将 HEAD 或显式 revision 解析为固定源码身份。"""
        ...

    def materialize(self, source: ResolvedSource, destination: Path) -> SourceSnapshot:
        """将固定源码物化到隔离目录并返回树摘要。"""
        ...
