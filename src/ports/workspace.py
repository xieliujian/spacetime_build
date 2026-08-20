"""隔离构建工作区的端口契约。

工作区是运行态资源，不能进入 BuildManifest 或发布模型。租约绑定 build ID，适配器必须
在根目录边界内创建、保留或清理目录。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WorkspaceRequest:
    """申请隔离工作区的请求。"""

    root: Path
    build_id: str
    preserve_on_failure: bool = True

    def __post_init__(self) -> None:
        """校验工作区根路径、构建 ID 和保留策略。"""
        if not isinstance(self.root, Path) or not self.root.is_absolute():
            raise ValueError("root 必须是绝对 Path")
        if not isinstance(self.build_id, str) or not self.build_id:
            raise ValueError("build_id 必须是非空字符串")
        if any(c in self.build_id for c in "/\\.\r\n"):
            raise ValueError("build_id 必须是单一路径段")
        if not isinstance(self.preserve_on_failure, bool):
            raise TypeError("preserve_on_failure 必须是 bool")


@dataclass(frozen=True, slots=True)
class WorkspaceLease:
    """已取得的隔离工作区租约。"""

    build_id: str
    path: Path
    lease_id: str

    def __post_init__(self) -> None:
        """校验租约身份和绝对工作区路径。"""
        if not isinstance(self.build_id, str) or not self.build_id:
            raise ValueError("build_id 必须是非空字符串")
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("path 必须是绝对 Path")
        if not isinstance(self.lease_id, str) or not self.lease_id:
            raise ValueError("lease_id 必须是非空字符串")


class WorkspaceProvider(Protocol):
    """隔离工作区创建与释放协议。"""

    def acquire(self, request: WorkspaceRequest) -> WorkspaceLease:
        """创建并返回当前 build 的独占工作区。"""
        ...

    def release(self, lease: WorkspaceLease, *, failed: bool) -> None:
        """按失败策略释放或保留工作区。"""
        ...
