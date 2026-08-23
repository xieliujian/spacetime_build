"""正式构建的显式日志引用汇总和原子 ``build.log`` 输出。

本模块不扫描任务目录猜测结果；调用方必须传入每个任务的显式日志引用。汇总器
按时间、任务名和 run ID 稳定排序，使用现有规范文本事件格式写入单个构建摘要，
并通过同目录临时文件加原子替换完成更新。所有自由文本在事件格式化前执行脱敏，
不会生成 JSON 或 JSONL。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import cast

from observability.context import LogContext
from observability.failures import ErrorCode
from observability.logger import LogEvent, LogLevel, format_log_event
from observability.runtime import RunLogResult, RunOutcome


def _validate_timestamp(value: datetime, field_name: str) -> None:
    """确认汇总时间包含有效时区。"""
    value_object = cast(object, value)
    if not isinstance(value_object, datetime):
        raise ValueError(f"{field_name} 必须是 datetime")
    if value_object.tzinfo is None or value_object.utcoffset() is None:
        raise ValueError(f"{field_name} 必须是 timezone-aware datetime")


def _validate_text(value: str, field_name: str) -> None:
    """确认汇总身份和 locator 是非空且不含换行的文本。"""
    if not isinstance(value, str) or not value or any(character in value for character in "\r\n"):
        raise ValueError(f"{field_name} 必须是非空且不含换行的字符串")


@dataclass(frozen=True, slots=True)
class BuildLogEntry:
    """一次任务运行在构建汇总日志中的显式引用。

    参数：
        timestamp、task_name、run_id: 任务终态时间和运行身份。
        outcome: 任务终态。
        duration_ms: 任务耗时。
        log_locator: 主日志公开定位。
        diagnostic_locators: stdout/stderr/Unity 等诊断定位。
        message: 脱敏终态摘要。
        error_code: 失败任务的稳定错误码。

    返回：
        无；构造成功后得到可稳定排序的不可变引用。

    异常、约束与副作用：
        身份、时间、耗时或 locator 非法时抛 ``ValueError``；不读取 locator 指向的
        文件，不执行目录扫描和外部 I/O。
    """

    timestamp: datetime
    task_name: str
    run_id: str
    outcome: RunOutcome
    duration_ms: int
    log_locator: str
    diagnostic_locators: tuple[str, ...] = ()
    message: str = "任务完成"
    error_code: ErrorCode | None = None

    def __post_init__(self) -> None:
        """校验并冻结任务引用的所有字段。"""
        _validate_timestamp(self.timestamp, "timestamp")
        _validate_text(self.task_name, "task_name")
        _validate_text(self.run_id, "run_id")
        if not isinstance(self.outcome, RunOutcome):
            raise ValueError("outcome 必须是 RunOutcome")
        if (
            not isinstance(self.duration_ms, int)
            or isinstance(self.duration_ms, bool)
            or self.duration_ms < 0
        ):
            raise ValueError("duration_ms 必须是非负整数")
        _validate_text(self.log_locator, "log_locator")
        if not isinstance(self.diagnostic_locators, tuple):
            raise ValueError("diagnostic_locators 必须是 tuple[str, ...]")
        for locator in self.diagnostic_locators:
            _validate_text(locator, "diagnostic_locator")
        _validate_text(self.message, "message")
        if self.outcome is RunOutcome.FAILED:
            if self.error_code is None:
                object.__setattr__(self, "error_code", ErrorCode.INTERNAL_ERROR)
            elif not isinstance(self.error_code, ErrorCode):
                raise ValueError("error_code 必须是 ErrorCode")
        elif self.error_code is not None:
            raise ValueError("非 FAILED 任务不得提供 error_code")


class BuildLogAssembler:
    """根据显式任务引用生成一次构建的文本汇总日志。"""

    @staticmethod
    def write(
        root: Path,
        build_id: str,
        *,
        started_at: datetime,
        finished_at: datetime,
        outcome: RunOutcome,
        fields: tuple[tuple[str, str], ...] = (),
        entries: tuple[BuildLogEntry, ...] = (),
    ) -> Path:
        """原子写入 ``logs/{build_id}/build.log`` 并返回精确路径。

        参数：
            root、build_id: 日志根目录和构建身份。
            started_at、finished_at: 构建开始与结束时间。
            outcome: 构建整体终态。
            fields: Profile、revision、配置摘要等公开审计字段。
            entries: 每个任务恰好一条的显式运行日志引用。

        返回：
            已原子替换完成的 ``build.log`` 路径。

        异常、约束与副作用：
            时间倒退、重复任务引用、字段冲突、输出目录非法或写入失败时抛异常。
            只替换 ``root/build_id/build.log``，失败时删除本次精确临时文件，不删除
            历史 build.log 和任务日志。
        """
        if not isinstance(root, Path):
            raise ValueError("root 必须是 Path")
        build_context = LogContext(build_id, "build", build_id, "summary")
        _validate_timestamp(started_at, "started_at")
        _validate_timestamp(finished_at, "finished_at")
        if finished_at < started_at:
            raise ValueError("finished_at 不得早于 started_at")
        if not isinstance(outcome, RunOutcome):
            raise ValueError("outcome 必须是 RunOutcome")
        if not isinstance(fields, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 for item in fields
        ):
            raise ValueError("fields 必须是二元组元组")
        if not isinstance(entries, tuple) or any(
            not isinstance(item, BuildLogEntry) for item in entries
        ):
            raise ValueError("entries 必须是 BuildLogEntry 元组")
        identities = {(entry.task_name, entry.run_id) for entry in entries}
        if len(identities) != len(entries):
            raise ValueError("entries 存在重复任务 run 引用")
        ordered = tuple(
            sorted(
                entries,
                key=lambda item: (
                    item.timestamp,
                    item.task_name.encode("utf-8"),
                    item.run_id.encode("utf-8"),
                ),
            )
        )
        total_duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        lines = [
            format_log_event(
                LogEvent(
                    started_at,
                    LogLevel.INFO,
                    build_context,
                    "构建开始",
                    _merge_fields(fields, (("outcome", "STARTED"),)),
                )
            )
        ]
        for entry in ordered:
            level = (
                LogLevel.ERROR
                if entry.outcome is RunOutcome.FAILED
                else LogLevel.WARNING
                if entry.outcome is RunOutcome.CANCELLED
                else LogLevel.INFO
            )
            entry_fields: list[tuple[str, str]] = [
                ("outcome", entry.outcome.value),
                ("duration_ms", str(entry.duration_ms)),
                ("log_locator", entry.log_locator),
            ]
            entry_fields.extend(
                (f"diagnostic_locator_{index}", locator)
                for index, locator in enumerate(entry.diagnostic_locators)
            )
            entry_context = LogContext(build_id, entry.task_name, entry.run_id, "summary")
            lines.append(
                format_log_event(
                    LogEvent(
                        entry.timestamp,
                        level,
                        entry_context,
                        entry.message,
                        tuple(entry_fields),
                        entry.error_code,
                    )
                )
            )
        final_level = (
            LogLevel.ERROR
            if outcome is RunOutcome.FAILED
            else LogLevel.WARNING
            if outcome is RunOutcome.CANCELLED
            else LogLevel.INFO
        )
        final_error_code = ErrorCode.INTERNAL_ERROR if outcome is RunOutcome.FAILED else None
        final_message = {
            RunOutcome.SUCCEEDED: "构建完成",
            RunOutcome.FAILED: "构建失败",
            RunOutcome.CANCELLED: "构建取消",
        }[outcome]
        lines.append(
            format_log_event(
                LogEvent(
                    finished_at,
                    final_level,
                    build_context,
                    final_message,
                    (("outcome", outcome.value), ("duration_ms", str(total_duration_ms))),
                    final_error_code,
                )
            )
        )
        content = "\n".join(lines) + "\n"
        directory = root / build_id
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "build.log"
        if output.is_symlink():
            raise ValueError("build.log 不得是符号链接")
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".build-log-",
                suffix=".tmp",
                dir=directory,
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output)
            temporary = None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return output


class BuildLogCollector:
    """线程安全收集多个运行回执并增量更新同一个 ``build.log``。

    参数：
        root、build_id、started_at: 构建汇总输出边界。
        fields: 构建级公开审计字段。

    返回：
        ``record`` 只更新内存引用；``write`` 调用汇总器并返回 build.log 路径。

    异常、约束与副作用：
        同一 task/run 重复记录时抛 ``ValueError``；写入失败只影响本次汇总，不删除
        已存在的任务日志。实例锁保证并发任务完成时引用快照和原子汇总不交错。
    """

    def __init__(
        self,
        root: Path,
        build_id: str,
        *,
        started_at: datetime,
        fields: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """校验并保存构建汇总身份；构造不创建文件。"""
        if not isinstance(root, Path):
            raise ValueError("root 必须是 Path")
        LogContext(build_id, "build", build_id, "summary")
        _validate_timestamp(started_at, "started_at")
        if not isinstance(fields, tuple):
            raise ValueError("fields 必须是 tuple")
        self._root = root
        self._build_id = build_id
        self._started_at = started_at
        self._fields = fields
        self._entries: list[BuildLogEntry] = []
        self._identities: set[tuple[str, str]] = set()
        self._lock = RLock()

    def record(
        self,
        result: RunLogResult,
        *,
        message: str,
        error_code: ErrorCode | None = None,
    ) -> BuildLogEntry:
        """登记一个已完成运行，并拒绝重复 task/run 引用。

        参数：
            result: ``RunLogSession.finish`` 返回的完成回执。
            message: 汇总日志中显示的脱敏终态摘要。
            error_code: FAILED 运行的可选稳定错误码。

        返回：
            新登记的 ``BuildLogEntry``。

        异常、约束与副作用：
            回执类型、构建身份或重复身份非法时抛异常；只修改当前 collector 内存，
            不写文件。
        """
        if not isinstance(result, RunLogResult):
            raise TypeError("result 必须是 RunLogResult")
        if result.context.build_id != self._build_id:
            raise ValueError("result.build_id 与 collector 不一致")
        entry = BuildLogEntry(
            timestamp=result.finished_at,
            task_name=result.context.task_name,
            run_id=result.context.run_id,
            outcome=result.outcome,
            duration_ms=result.duration_ms,
            log_locator=result.paths.main.as_posix(),
            diagnostic_locators=(
                result.paths.stdout.as_posix(),
                result.paths.stderr.as_posix(),
                result.paths.unity.as_posix(),
            ),
            message=message,
            error_code=error_code,
        )
        with self._lock:
            identity = (entry.task_name, entry.run_id)
            if identity in self._identities:
                raise ValueError("collector 存在重复任务 run 引用")
            self._identities.add(identity)
            self._entries.append(entry)
        return entry

    def write(
        self,
        *,
        finished_at: datetime | None = None,
        outcome: RunOutcome = RunOutcome.SUCCEEDED,
    ) -> Path:
        """把当前引用快照原子写入构建汇总日志。"""
        completed_at = finished_at if finished_at is not None else datetime.now(timezone.utc)
        _validate_timestamp(completed_at, "finished_at")
        with self._lock:
            entries = tuple(self._entries)
            return BuildLogAssembler.write(
                self._root,
                self._build_id,
                started_at=self._started_at,
                finished_at=completed_at,
                outcome=outcome,
                fields=self._fields,
                entries=entries,
            )


def _merge_fields(
    fields: tuple[tuple[str, str], ...],
    reserved: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """合并汇总头字段并拒绝覆盖生命周期字段。"""
    if not isinstance(fields, tuple):
        raise ValueError("fields 必须是 tuple")
    all_fields = (*fields, *reserved)
    keys: list[str] = []
    for entry in all_fields:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ValueError("fields 必须包含二元组")
        key, value = entry
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("fields 键和值必须是 str")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise ValueError("fields 存在重复键")
    return all_fields


__all__ = ["BuildLogAssembler", "BuildLogCollector", "BuildLogEntry"]
