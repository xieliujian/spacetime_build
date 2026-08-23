"""日志归档后的安全保留清理。

本模块只接受调用方提供的精确运行目录清单，不通过扫描目录猜测运行状态。清理前
创建日志根目录维护锁，逐个跳过活动 lease、未归档、未完成发布和 retain 标记，
仅删除已过期且通过路径 containment 和链接安全检查的精确 run 目录。删除或锁
失败只进入报告，不改变构建或发布状态。
"""

from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from observability.redaction import redact_text


def _validate_timestamp(value: datetime, field_name: str) -> None:
    """确认清理时间是有效的 timezone-aware ``datetime``。"""
    value_object = cast(object, value)
    if not isinstance(value_object, datetime):
        raise ValueError(f"{field_name} 必须是 datetime")
    if value_object.tzinfo is None or value_object.utcoffset() is None:
        raise ValueError(f"{field_name} 必须是 timezone-aware datetime")


@dataclass(frozen=True, slots=True)
class LogRunMetadata:
    """维护任务用于判断一个运行目录是否可删除的显式元数据。"""

    directory: Path
    completed_at: datetime
    archived: bool
    active_lease: bool = False
    release_finished: bool = True
    retain: bool = False

    def __post_init__(self) -> None:
        """校验运行目录、完成时间和保护标记，不访问目录内容。"""
        if not isinstance(self.directory, Path):
            raise ValueError("directory 必须是 Path")
        _validate_timestamp(self.completed_at, "completed_at")
        for name in ("archived", "active_lease", "release_finished", "retain"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} 必须是 bool")


@dataclass(frozen=True, slots=True)
class RetentionSkip:
    """记录一个未被删除的运行目录和稳定原因。"""

    directory: Path
    reason: str


@dataclass(frozen=True, slots=True)
class RetentionFailure:
    """记录一个经过脱敏的清理失败摘要。"""

    directory: Path
    message: str

    def __post_init__(self) -> None:
        """校验并脱敏维护失败消息，避免异常文本带出凭据。"""
        if not isinstance(self.directory, Path):
            raise ValueError("directory 必须是 Path")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("message 必须是非空字符串")
        object.__setattr__(self, "message", redact_text(self.message))


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """一次维护任务删除、跳过和失败的不可变汇总。"""

    deleted: tuple[Path, ...]
    skipped: tuple[RetentionSkip, ...]
    failures: tuple[RetentionFailure, ...]


class LogRetentionCleaner:
    """执行带维护锁和多重保护条件的精确日志目录清理。"""

    @staticmethod
    def clean(
        root: Path,
        *,
        now: datetime,
        retain_days: int,
        runs: tuple[LogRunMetadata, ...],
    ) -> RetentionReport:
        """清理已归档且超过保留期限的运行目录。

        参数：
            root: 日志根目录；维护锁和所有允许清理的目录必须位于此目录内。
            now: 显式当前时间，便于定时任务稳定测试。
            retain_days: 非负保留天数。
            runs: application/归档服务提供的精确运行元数据元组。

        返回：
            删除、跳过和失败项组成的 ``RetentionReport``。

        异常、约束与副作用：
            根目录、参数、锁或路径安全边界非法时抛异常；单个运行删除失败会被
            记录在报告中并继续处理其他项。不会删除 build.log、任务父目录或根目录。
        """
        if not isinstance(root, Path):
            raise ValueError("root 必须是 Path")
        _validate_timestamp(now, "now")
        if not isinstance(retain_days, int) or isinstance(retain_days, bool) or retain_days < 0:
            raise ValueError("retain_days 必须是非负整数")
        if not isinstance(runs, tuple) or any(
            not isinstance(item, LogRunMetadata) for item in runs
        ):
            raise ValueError("runs 必须是 LogRunMetadata 元组")
        root.mkdir(parents=True, exist_ok=True)
        lock = root / ".maintenance.lock"
        try:
            with lock.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write("active\n")
        except FileExistsError as exc:
            raise RuntimeError("日志维护锁已被占用") from exc

        deleted: list[Path] = []
        skipped: list[RetentionSkip] = []
        failures: list[RetentionFailure] = []
        cutoff = now - timedelta(days=retain_days)
        try:
            for metadata in sorted(runs, key=lambda item: str(item.directory).encode("utf-8")):
                reason = _skip_reason(metadata, cutoff)
                if reason is not None:
                    skipped.append(RetentionSkip(metadata.directory, reason))
                    continue
                _validate_run_directory(root, metadata.directory)
                try:
                    shutil.rmtree(metadata.directory)
                except Exception as exc:
                    failures.append(RetentionFailure(metadata.directory, str(exc)))
                else:
                    deleted.append(metadata.directory)
        finally:
            lock.unlink(missing_ok=True)
        return RetentionReport(tuple(deleted), tuple(skipped), tuple(failures))


def _skip_reason(metadata: LogRunMetadata, cutoff: datetime) -> str | None:
    """按固定优先级返回一个运行目录的保护或未过期原因。"""
    if metadata.active_lease:
        return "active_lease"
    if not metadata.archived:
        return "not_archived"
    if not metadata.release_finished:
        return "release_not_finished"
    if metadata.retain:
        return "retain_marker"
    if metadata.completed_at >= cutoff:
        return "not_expired"
    return None


def _validate_run_directory(root: Path, directory: Path) -> None:
    """确认待删目录是 root 下的三层运行目录且不含链接实体。"""
    if not isinstance(directory, Path):
        raise ValueError("运行目录必须是 Path")
    root_absolute = root.absolute()
    directory_absolute = directory.absolute()
    try:
        relative = directory_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise ValueError("运行目录必须位于日志根目录内") from exc
    if len(relative.parts) != 3:
        raise ValueError("只能删除 build/task/run 精确目录")
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("运行目录必须是普通目录且不得是符号链接")
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"运行目录含符号链接: {path}")
        try:
            attributes = cast(int, getattr(path.lstat(), "st_file_attributes", 0))
        except FileNotFoundError:
            continue
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(f"运行目录含 reparse point: {path}")


__all__ = [
    "LogRetentionCleaner",
    "LogRunMetadata",
    "RetentionFailure",
    "RetentionReport",
    "RetentionSkip",
]
