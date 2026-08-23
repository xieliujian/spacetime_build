"""SDK 结构化 hook 的隔离工作区原子应用器。

应用器只消费 ``SdkHookPlan`` 的白名单操作，目标必须在调用方指定的 workspace 内。
写入和删除失败时恢复本次已修改文件；重复应用相同内容保持幂等。SecretRef 只作为
descriptor 身份存在，本模块不会解析秘密或执行任何脚本。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sdk.model import SdkOperationKind
from sdk.planner import SdkHookPlan


def _tree_digest(root: Path) -> str:
    """计算 workspace 普通文件树摘要。"""
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file() and not item.is_symlink()),
        key=lambda item: item.relative_to(root).as_posix().encode("utf-8"),
    ):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _target(root: Path, logical_path: str) -> Path:
    """将已经校验的逻辑路径解析为 workspace 内普通目标。"""
    candidate = (root / logical_path).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise ValueError(f"SDK 目标越出 workspace: {logical_path}")
    current = root
    for part in logical_path.split("/"):
        current = current / part
        if current.is_symlink():
            raise ValueError(f"SDK 目标路径不得经过符号链接: {logical_path}")
    return candidate


@dataclass(frozen=True, slots=True)
class SdkApplyResult:
    """保存 SDK 应用前后摘要和变更路径。"""

    plan_id: str
    applied: bool
    before_tree_sha256: str
    after_tree_sha256: str
    changed_paths: tuple[str, ...]


class SdkHookApplier:
    """在隔离 workspace 内原子应用 SDK 白名单操作。"""

    def apply(self, plan: SdkHookPlan, workspace: Path) -> SdkApplyResult:
        """执行计划；失败时恢复原文件并重新抛出异常。"""
        if not isinstance(plan, SdkHookPlan):
            raise TypeError("plan 必须是 SdkHookPlan")
        if not isinstance(workspace, Path) or not workspace.is_absolute():
            raise ValueError("workspace 必须是绝对 Path")
        if workspace.exists() and (not workspace.is_dir() or workspace.is_symlink()):
            raise ValueError("workspace 必须是普通目录")
        workspace.mkdir(parents=True, exist_ok=True)
        before_digest = _tree_digest(workspace)
        targets = tuple(sorted({operation.target for operation in plan.operations}))
        backups: dict[Path, bytes | None] = {}
        try:
            for operation in plan.operations:
                path = _target(workspace, operation.target)
                if path not in backups:
                    if path.is_dir() and not path.is_symlink():
                        raise ValueError(f"SDK 目标不得是目录: {operation.target}")
                    backups[path] = path.read_bytes() if path.is_file() else None
                if operation.kind is SdkOperationKind.DELETE_FILE:
                    if path.exists():
                        path.unlink()
                    continue
                if operation.value is None:
                    raise ValueError(f"SDK 操作缺少 value: {operation.target}")
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
                ) as temporary:
                    temporary.write(operation.value.encode("utf-8"))
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_path = Path(temporary.name)
                os.replace(temporary_path, path)
        except BaseException:
            for path, content in backups.items():
                if content is None:
                    if path.is_file() or path.is_symlink():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            raise
        after_digest = _tree_digest(workspace)
        return SdkApplyResult(plan.plan_id, True, before_digest, after_digest, targets)


__all__ = ["SdkApplyResult", "SdkHookApplier"]
