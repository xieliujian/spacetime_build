"""本地隔离构建工作区适配器。

适配器只在配置根目录下创建以 build ID 命名的独占目录，并以内存租约表阻止同一进程
重复租用。成功任务的目录会清理；失败任务按申请时的保留策略保留诊断材料。
"""

from __future__ import annotations

import shutil
import threading
import uuid
from ports.workspace import WorkspaceLease, WorkspaceProvider, WorkspaceRequest


class LocalWorkspaceProvider(WorkspaceProvider):
    """使用本地文件系统实现隔离工作区租约。"""

    def __init__(self) -> None:
        """创建没有活动租约的工作区提供器。"""
        self._lock = threading.Lock()
        self._active: dict[str, tuple[WorkspaceLease, bool]] = {}

    def acquire(self, request: WorkspaceRequest) -> WorkspaceLease:
        """在根目录下排他创建当前 build 的工作区。"""
        if not isinstance(request, WorkspaceRequest):
            raise TypeError("request 必须是 WorkspaceRequest")
        root = request.root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = (root / request.build_id).resolve()
        if path.parent != root:
            raise ValueError("工作区路径越出配置根目录")
        lease = WorkspaceLease(request.build_id, path, uuid.uuid4().hex)
        with self._lock:
            if lease.lease_id in self._active or path.exists():
                raise FileExistsError(f"工作区已存在或正在租用: {path}")
            path.mkdir()
            self._active[lease.lease_id] = (lease, request.preserve_on_failure)
        return lease

    def release(self, lease: WorkspaceLease, *, failed: bool) -> None:
        """校验租约所有权并按策略清理精确目录。"""
        if not isinstance(lease, WorkspaceLease):
            raise TypeError("lease 必须是 WorkspaceLease")
        if not isinstance(failed, bool):
            raise TypeError("failed 必须是 bool")
        with self._lock:
            record = self._active.pop(lease.lease_id, None)
        if record is None or record[0] != lease:
            raise KeyError(f"未知或已释放的工作区租约: {lease.lease_id}")
        preserve = failed and record[1]
        if preserve:
            return
        path = lease.path
        if path.is_symlink() or not path.is_dir():
            raise ValueError("工作区租约路径不是普通目录")
        if path.exists():
            shutil.rmtree(path)
