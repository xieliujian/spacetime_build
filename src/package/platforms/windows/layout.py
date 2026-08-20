"""Windows Player 文件布局的确定性计划和 workspace 内安全应用器。"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from package.platforms.windows.path_rules import validate_windows_relative_path


def _validate_destination(value: str) -> str:
    """校验 Windows 目标为安全的相对正斜杠路径。"""
    return validate_windows_relative_path(value, label="布局目标")


def _validate_workspace(workspace: Path) -> Path:
    """校验并规范化已存在的绝对 workspace 目录。"""
    if not isinstance(workspace, Path) or not workspace.is_absolute() or not workspace.is_dir():
        raise ValueError("workspace 必须是已存在的绝对目录")
    return workspace.resolve()


def _validate_source(source: Path) -> Path:
    """校验源文件是普通非符号链接文件。"""
    if not isinstance(source, Path) or not source.is_absolute():
        raise ValueError("copy source 必须是绝对 Path")
    if source.is_symlink():
        raise ValueError("布局源文件不得是符号链接")
    if not source.is_file():
        raise ValueError("布局 source 必须是普通文件")
    return source


@dataclass(frozen=True, slots=True)
class LayoutCopy:
    """把一个已知普通源文件复制到 workspace 逻辑路径的操作。"""

    source: Path
    destination: str


@dataclass(frozen=True, slots=True)
class LayoutWrite:
    """把内存中的固定字节写入 workspace 逻辑路径的操作。"""

    destination: str
    content: bytes

    def __post_init__(self) -> None:
        """校验写入内容不会隐式转换或携带可变对象。"""
        if not isinstance(self.content, bytes):
            raise TypeError("LayoutWrite.content 必须是 bytes")


@dataclass(frozen=True, slots=True)
class LayoutDelete:
    """删除 workspace 内一个已有文件的操作。"""

    destination: str


@dataclass(frozen=True, slots=True)
class WindowsLayoutPlan:
    """冻结 workspace 根和三类确定性布局操作。"""

    workspace: Path
    copies: tuple[LayoutCopy, ...]
    writes: tuple[LayoutWrite, ...]
    deletes: tuple[LayoutDelete, ...]


class WindowsLayoutPlanner:
    """生成不依赖发布目录扫描的 Windows 文件布局计划。"""

    @staticmethod
    def plan(
        workspace: Path,
        *,
        copies: tuple[LayoutCopy, ...] = (),
        writes: tuple[LayoutWrite, ...] = (),
        deletes: tuple[LayoutDelete, ...] = (),
    ) -> WindowsLayoutPlan:
        """校验并按声明顺序冻结 copy/write/delete 计划。"""
        root = _validate_workspace(workspace)
        for field_name, value in (("copies", copies), ("writes", writes), ("deletes", deletes)):
            if not isinstance(value, tuple):
                raise TypeError(f"{field_name} 必须是 tuple")
        normalized_copies: list[LayoutCopy] = []
        targets: list[str] = []
        for item in copies:
            if not isinstance(item, LayoutCopy):
                raise TypeError("copies 的每一项必须是 LayoutCopy")
            normalized_copies.append(
                LayoutCopy(_validate_source(item.source), _validate_destination(item.destination))
            )
            targets.append(item.destination)
        normalized_writes: list[LayoutWrite] = []
        for item in writes:
            if not isinstance(item, LayoutWrite):
                raise TypeError("writes 的每一项必须是 LayoutWrite")
            destination = _validate_destination(item.destination)
            normalized_writes.append(LayoutWrite(destination, item.content))
            targets.append(destination)
        normalized_deletes: list[LayoutDelete] = []
        for item in deletes:
            if not isinstance(item, LayoutDelete):
                raise TypeError("deletes 的每一项必须是 LayoutDelete")
            destination = _validate_destination(item.destination)
            normalized_deletes.append(LayoutDelete(destination))
            targets.append(destination)
        _validate_target_set(targets)
        return WindowsLayoutPlan(
            root,
            tuple(normalized_copies),
            tuple(normalized_writes),
            tuple(normalized_deletes),
        )


class WindowsLayoutApplier:
    """在 workspace 内以 staging 和回滚保护应用布局计划。"""

    @staticmethod
    def apply(plan: WindowsLayoutPlan) -> None:
        """原子准备并应用布局；失败时恢复既有目标并删除临时目录。"""
        if not isinstance(plan, WindowsLayoutPlan):
            raise TypeError("plan 必须是 WindowsLayoutPlan")
        root = _validate_workspace(plan.workspace)
        staging = root / ".spacetime-layout-staging"
        if staging.exists():
            raise ValueError("workspace 已存在布局 staging 目录")
        staged_targets: list[tuple[str, Path | None]] = []
        changed_targets: list[Path] = []
        backups: dict[Path, Path] = {}
        try:
            staging.mkdir()
            input_directory = staging / "inputs"
            backup_directory = staging / "backups"
            input_directory.mkdir()
            backup_directory.mkdir()
            operations = (
                tuple((item.destination, item.source) for item in plan.copies)
                + tuple((item.destination, item.content) for item in plan.writes)
                + tuple((item.destination, None) for item in plan.deletes)
            )
            for index, (destination, source_or_content) in enumerate(operations):
                target = _target_path(root, destination)
                _validate_target_parents(root, target)
                if target.exists() or target.is_symlink():
                    if target.is_symlink() or not target.is_file():
                        raise ValueError(f"布局目标必须是普通文件: {destination}")
                    backup = backup_directory / str(index)
                    shutil.copyfile(target, backup)
                    backups[target] = backup
                if isinstance(source_or_content, Path):
                    staged = input_directory / str(index)
                    shutil.copyfile(source_or_content, staged)
                    staged_targets.append((destination, staged))
                elif isinstance(source_or_content, bytes):
                    staged = input_directory / str(index)
                    staged.write_bytes(source_or_content)
                    staged_targets.append((destination, staged))
                else:
                    staged_targets.append((destination, None))
            for destination, staged in staged_targets:
                target = _target_path(root, destination)
                if staged is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged, target)
                changed_targets.append(target)
        except BaseException:
            for target in reversed(changed_targets):
                backup = backups.get(target)
                try:
                    if backup is not None and backup.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(backup, target)
                    elif target.exists() or target.is_symlink():
                        target.unlink()
                except OSError:
                    pass
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)


def _validate_target_set(targets: list[str]) -> None:
    """拒绝重复或父子冲突的逻辑目标，避免应用顺序改变语义。"""
    normalized = sorted((target.casefold(), target) for target in targets)
    for index, (folded, original) in enumerate(normalized):
        if index and folded == normalized[index - 1][0]:
            raise ValueError(f"布局目标大小写折叠后冲突: {original}")
        if index:
            previous = normalized[index - 1][0]
            if folded.startswith(previous + "/"):
                raise ValueError(f"布局目标存在父子冲突: {original}")


def _target_path(root: Path, destination: str) -> Path:
    """将已通过语法校验的逻辑目标解析到 workspace 内。"""
    candidate = (root / destination).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"布局目标逃逸 workspace: {destination}") from exc
    return candidate


def _validate_target_parents(root: Path, target: Path) -> None:
    """拒绝目标父目录中的符号链接和普通文件遮蔽。"""
    relative_parent = target.parent.relative_to(root)
    current = root
    for segment in relative_parent.parts:
        current = current / segment
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError(f"布局目标父路径不安全: {current}")


__all__ = [
    "LayoutCopy",
    "LayoutDelete",
    "LayoutWrite",
    "WindowsLayoutApplier",
    "WindowsLayoutPlan",
    "WindowsLayoutPlanner",
]
