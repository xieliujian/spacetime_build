"""运行级日志会话和统一生命周期输出。

本模块把 ``LogContext``、主日志 handler 与外部 stdout/stderr/Unity writer 组合成一
个有明确所有权的运行会话。会话负责输出开始、阶段、成功、失败和取消事件，并在
完成时记录耗时；外部流始终经过同一个流式脱敏边界。模块导入不会读取当前时间、
创建目录或配置全局 logging，所有文件副作用都发生在显式调用 ``open``、写入和
关闭方法中。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from collections.abc import Callable, Generator
from typing import TextIO, cast

from observability.context import LogContext, LogPaths, build_log_paths
from observability.failures import ErrorCode, build_failure_from_exception
from observability.handlers import ExternalStreamWriter, open_run_handlers
from observability.logger import CompositeLogger, LogEvent, LogLevel


class RunOutcome(str, Enum):
    """运行会话对外使用的三种终态。"""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def _validate_timestamp(value: datetime, field_name: str) -> None:
    """确认时间是带有效时区的 ``datetime``。

    参数：
        value: 待校验时间。
        field_name: 异常消息中的字段名称。

    返回：
        校验成功时返回 ``None``。

    异常、约束与副作用：
        类型非法、没有时区或时区偏移不可计算时抛 ``ValueError``。函数只处理
        内存值，不读取系统时间或文件系统。
    """
    value_object = cast(object, value)
    if not isinstance(value_object, datetime):
        raise ValueError(f"{field_name} 必须是 datetime")
    try:
        offset = value_object.utcoffset()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须包含有效时区") from exc
    if value_object.tzinfo is None or offset is None:
        raise ValueError(f"{field_name} 必须是 timezone-aware datetime")


def _now() -> datetime:
    """返回当前 UTC 时间，集中隔离运行时钟读取。"""
    return datetime.now(timezone.utc)


def _merge_fields(
    fields: tuple[tuple[str, str], ...],
    reserved: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """把调用方字段和生命周期字段合并，并拒绝键覆盖。

    参数：
        fields: 调用方提供的扩展字段。
        reserved: 会话内部必须写入的字段。

    返回：
        拼接后的字段元组；排序与脱敏交给 ``LogEvent``。

    异常、约束与副作用：
        任意键重复或容器类型不符时抛 ``ValueError``。函数不修改输入，不执行
        日志和 I/O。
    """
    if not isinstance(fields, tuple):
        raise ValueError("fields 必须是字符串键值元组")
    keys: set[str] = set()
    for entry in (*fields, *reserved):
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ValueError("fields 必须包含二元组")
        key, value = entry
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("fields 键和值必须是 str")
        if key in keys:
            raise ValueError(f"fields 存在重复键: {key!r}")
        keys.add(key)
    return (*fields, *reserved)


@dataclass(frozen=True, slots=True)
class RunLogResult:
    """完成一次运行后可交给上层审计和汇总的日志回执。

    参数：
        context: 本次运行的不可变日志上下文。
        paths: 主日志和三个外部日志的精确路径。
        started_at、finished_at: 会话开始和完成时间。
        outcome: 运行终态。
        duration_ms: 由两个显式时间计算的毫秒耗时。

    返回：
        无；对象冻结且不持有打开的文件句柄。

    异常、约束与副作用：
        字段非法时抛 ``ValueError``；构造本身不访问文件系统。
    """

    context: LogContext
    paths: LogPaths
    started_at: datetime
    finished_at: datetime
    outcome: RunOutcome
    duration_ms: int


class RunLogSession:
    """拥有一次运行全部日志资源并输出生命周期事件的会话。

    参数：
        context: 构建、任务和 run 身份。
        paths: 由 ``build_log_paths`` 生成的日志路径。
        logger: 主日志和控制台组合 logger。
        stdout_writer、stderr_writer、unity_writer: 外部输出 writer。
        started_at: 会话开始时间。

    返回：
        通过 ``open`` 创建并返回一个已打开的会话。

    异常、约束与副作用：
        ``open`` 可能创建四个独占日志文件；文件碰撞或目录安全检查失败会直接
        抛出。会话关闭后拒绝继续写入；调用方必须显式调用 ``finish`` 和 ``close``，
        或使用上下文管理器。不会配置全局 logging。
    """

    __slots__ = (
        "_closed",
        "_finished",
        "_logger",
        "_started",
        "_stderr_writer",
        "_stdout_writer",
        "_unity_writer",
        "context",
        "paths",
        "started_at",
    )

    def __init__(
        self,
        context: LogContext,
        paths: LogPaths,
        logger: CompositeLogger,
        stdout_writer: ExternalStreamWriter,
        stderr_writer: ExternalStreamWriter,
        unity_writer: ExternalStreamWriter,
        started_at: datetime,
    ) -> None:
        """绑定已打开的日志资源并初始化生命周期状态。

        参数和副作用：
            参数必须是对应的日志值对象或已打开资源；构造不创建新文件、不写入
            日志。资源所有权转移给新会话，后续由 ``close`` 释放。
        """
        if not isinstance(context, LogContext):
            raise ValueError("context 必须是 LogContext")
        if not isinstance(paths, LogPaths):
            raise ValueError("paths 必须是 LogPaths")
        if not isinstance(logger, CompositeLogger):
            raise ValueError("logger 必须是 CompositeLogger")
        for name, writer in (
            ("stdout_writer", stdout_writer),
            ("stderr_writer", stderr_writer),
            ("unity_writer", unity_writer),
        ):
            if not isinstance(writer, ExternalStreamWriter):
                raise ValueError(f"{name} 必须是 ExternalStreamWriter")
        _validate_timestamp(started_at, "started_at")
        self.context = context
        self.paths = paths
        self._logger = logger
        self._stdout_writer = stdout_writer
        self._stderr_writer = stderr_writer
        self._unity_writer = unity_writer
        self.started_at = started_at
        self._closed = False
        self._started = False
        self._finished = False

    @classmethod
    def open(
        cls,
        root: Path,
        context: LogContext,
        *,
        started_at: datetime | None = None,
        console_stream: TextIO | None = None,
        console_level: LogLevel = LogLevel.INFO,
        file_level: LogLevel = LogLevel.DEBUG,
        secret_values: tuple[str, ...] = (),
    ) -> RunLogSession:
        """创建并打开一次运行的主、控制台和外部流日志资源。

        参数：
            root、context: 日志根目录和运行身份。
            started_at: 可选显式开始时间；缺省读取一次当前 UTC 时间。
            console_stream: 可选控制台文本流；缺省使用 ``sys.stderr``。
            console_level、file_level: 控制台和主文件最低级别。
            secret_values: 外部流中必须持续隐藏的秘密值。

        返回：
            已创建四个独占文件并持有资源的 ``RunLogSession``。

        异常、约束与副作用：
            路径、上下文、级别或流非法时抛 ``ValueError``；文件碰撞、目录链接和
            I/O 错误直接传播。模块导入和调用前不会创建任何日志文件。
        """
        if started_at is None:
            started_at = _now()
        _validate_timestamp(started_at, "started_at")
        if console_stream is None:
            import sys

            console_stream = sys.stderr
        if not isinstance(cast(object, secret_values), tuple) or any(
            not isinstance(value, str) for value in secret_values
        ):
            raise ValueError("secret_values 必须是 tuple[str, ...]")
        paths = build_log_paths(root, context, started_at)
        logger, stdout_writer, stderr_writer, unity_writer = open_run_handlers(
            paths,
            cast(TextIO, console_stream),
            console_level,
            file_level,
            secret_values,
        )
        return cls(
            context,
            paths,
            logger,
            stdout_writer,
            stderr_writer,
            unity_writer,
            started_at,
        )

    @property
    def logger(self) -> CompositeLogger:
        """返回主日志 logger，供真实进程适配器记录启动和结束事件。"""
        return self._logger

    def emit(
        self,
        level: LogLevel,
        message: str,
        *,
        step_name: str | None = None,
        fields: tuple[tuple[str, str], ...] = (),
        error_code: ErrorCode | None = None,
        diagnostic_paths: tuple[Path, ...] = (),
        timestamp: datetime | None = None,
    ) -> None:
        """向控制台和主日志同步输出一条带上下文事件。"""
        if self._closed:
            raise RuntimeError("RunLogSession 已关闭")
        event_context = (
            replace(self.context, step_name=step_name) if step_name is not None else self.context
        )
        event = LogEvent(
            timestamp=timestamp if timestamp is not None else _now(),
            level=level,
            context=event_context,
            message=message,
            fields=fields,
            error_code=error_code,
            diagnostic_paths=diagnostic_paths,
        )
        self._logger.emit(event)

    def start(
        self,
        *,
        fields: tuple[tuple[str, str], ...] = (),
        timestamp: datetime | None = None,
    ) -> None:
        """记录唯一的运行开始事件。"""
        if self._closed or self._started or self._finished:
            raise RuntimeError("运行已经开始、结束或会话已关闭")
        self.emit(LogLevel.INFO, "运行开始", fields=fields, timestamp=timestamp)
        self._started = True

    @contextmanager
    def phase(
        self,
        name: str,
        *,
        fields: tuple[tuple[str, str], ...] = (),
    ) -> Generator[None, None, None]:
        """以上下文管理器记录阶段开始、成功、失败和耗时。

        参数：
            name: 单个安全阶段名，例如 ``preflight``、``unity`` 或 ``upload``。
            fields: 阶段开始和成功事件都要携带的公开审计字段。

        返回：
            ``yield`` 给阶段业务代码；正常退出后写入阶段完成事件。

        异常、约束与副作用：
            阶段体抛异常时写入脱敏 ERROR、稳定错误码和根因摘要后原样重抛；阶段
            日志 I/O 失败会直接传播，不能把未记录的阶段伪报成功。
        """
        phase_started_at = _now()
        self.emit(
            LogLevel.INFO, "阶段开始", step_name=name, fields=fields, timestamp=phase_started_at
        )
        try:
            yield
        except BaseException as exc:
            failure = build_failure_from_exception(
                exc,
                build_id=self.context.build_id,
                task_name=self.context.task_name,
                step_name=name,
            )
            self.emit(
                LogLevel.ERROR,
                failure.message,
                step_name=name,
                error_code=failure.error_code,
                diagnostic_paths=failure.diagnostic_paths,
                timestamp=_now(),
            )
            raise
        else:
            phase_finished_at = _now()
            duration_ms = int((phase_finished_at - phase_started_at).total_seconds() * 1000)
            self.emit(
                LogLevel.INFO,
                "阶段完成",
                step_name=name,
                fields=_merge_fields(fields, (("duration_ms", str(max(0, duration_ms))),)),
                timestamp=phase_finished_at,
            )

    def finish(
        self,
        outcome: RunOutcome,
        *,
        finished_at: datetime | None = None,
        message: str | None = None,
        fields: tuple[tuple[str, str], ...] = (),
        error_code: ErrorCode | None = None,
        diagnostic_paths: tuple[Path, ...] = (),
    ) -> RunLogResult:
        """记录终态事件并返回日志路径、耗时和终态回执。

        参数：
            outcome: 成功、失败或取消终态。
            finished_at: 可选显式完成时间；缺省读取当前 UTC 时间。
            message: 终态摘要；缺省按终态生成中文摘要。
            fields: 额外审计字段；方法会追加 ``outcome`` 和 ``duration_ms``。
            error_code: 失败时的稳定错误码；缺省为 ``INTERNAL_ERROR``。
            diagnostic_paths: 失败时保留的诊断路径。

        返回：
            ``RunLogResult``；不关闭文件资源，调用方仍可追加外部流并须调用 ``close``。

        异常、约束与副作用：
            未调用 start、重复 finish、时间倒退、终态类型非法或字段冲突时抛
            ``ValueError``/``RuntimeError``；失败日志写入失败不会返回成功回执。
        """
        if self._closed:
            raise RuntimeError("RunLogSession 已关闭")
        if not self._started:
            raise RuntimeError("必须先记录运行开始")
        if self._finished:
            raise RuntimeError("RunLogSession 已完成")
        if not isinstance(outcome, RunOutcome):
            raise ValueError("outcome 必须是 RunOutcome")
        completed_at = finished_at if finished_at is not None else _now()
        _validate_timestamp(completed_at, "finished_at")
        elapsed = completed_at - self.started_at
        if elapsed.total_seconds() < 0:
            raise ValueError("finished_at 不得早于 started_at")
        duration_ms = int(elapsed.total_seconds() * 1000)
        level = (
            LogLevel.ERROR
            if outcome is RunOutcome.FAILED
            else LogLevel.WARNING
            if outcome is RunOutcome.CANCELLED
            else LogLevel.INFO
        )
        selected_error_code = error_code
        if outcome is RunOutcome.FAILED and selected_error_code is None:
            selected_error_code = ErrorCode.INTERNAL_ERROR
        if outcome is not RunOutcome.FAILED and selected_error_code is not None:
            raise ValueError("只有 FAILED 终态可以携带 error_code")
        default_message = {
            RunOutcome.SUCCEEDED: "运行完成",
            RunOutcome.FAILED: "运行失败",
            RunOutcome.CANCELLED: "运行取消",
        }[outcome]
        self.emit(
            level,
            message or default_message,
            fields=_merge_fields(
                fields,
                (("outcome", outcome.value), ("duration_ms", str(duration_ms))),
            ),
            error_code=selected_error_code,
            diagnostic_paths=diagnostic_paths,
            timestamp=completed_at,
        )
        self._finished = True
        return RunLogResult(
            self.context,
            self.paths,
            self.started_at,
            completed_at,
            outcome,
            duration_ms,
        )

    def write_stdout(self, text: str) -> str:
        """写入已脱敏的外部标准输出流。"""
        self._ensure_open()
        return self._stdout_writer.write_text(text)

    def write_stderr(self, text: str) -> str:
        """写入已脱敏的外部标准错误流。"""
        self._ensure_open()
        return self._stderr_writer.write_text(text)

    def write_unity(self, text: str) -> str:
        """写入已脱敏的 Unity 原始输出流。"""
        self._ensure_open()
        return self._unity_writer.write_text(text)

    def close(self) -> None:
        """关闭全部外部流和主 logger，并尽力释放本会话所有资源。"""
        if self._closed:
            return
        first_error: BaseException | None = None
        if not self._finished:
            try:
                if not self._started:
                    self.start()
                self.finish(RunOutcome.FAILED, message="运行未显式完成")
            except BaseException as exc:
                first_error = exc
        for resource in (
            self._unity_writer,
            self._stderr_writer,
            self._stdout_writer,
            self._logger,
        ):
            try:
                resource.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        self._closed = True
        if first_error is not None:
            raise RuntimeError("运行日志资源关闭失败") from first_error

    def __enter__(self) -> RunLogSession:
        """返回自身，支持 with 语法管理日志资源。"""
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """关闭会话；上下文中的原始异常仍由 Python 继续向外传播。"""
        self.close()

    def _ensure_open(self) -> None:
        """拒绝向已关闭会话写外部流。"""
        if self._closed:
            raise RuntimeError("RunLogSession 已关闭")


class CommandLogRunner:
    """为任意 CLI/application handler 添加统一命令生命周期日志。

    参数：
        session_factory: 接收命令名和解析参数、返回已打开 ``RunLogSession`` 的工厂。

    返回：
        ``run`` 返回 handler 的原始结果；失败异常不会被 middleware 吞掉。

    异常、约束与副作用：
        工厂必须返回真实会话。handler 返回的 ``state`` 为 FAILED/CONFLICTED 时也
        会输出 ERROR；异常会转换为稳定失败码、脱敏消息和根因日志后继续向外抛出。
        会话始终在 finally 中关闭，关闭错误会报告为日志基础设施错误。
    """

    def __init__(
        self,
        session_factory: Callable[[str, object], RunLogSession],
        *,
        collector: object | None = None,
    ) -> None:
        """绑定显式会话工厂和可选汇总 collector，不创建日志资源。"""
        if not callable(session_factory):
            raise TypeError("session_factory 必须可调用")
        if collector is not None and not all(
            callable(getattr(collector, name, None)) for name in ("record", "write")
        ):
            raise TypeError("collector 必须提供 record/write 方法")
        self._session_factory = session_factory
        self._collector = collector
        self._last_result: RunLogResult | None = None

    @property
    def last_log_locator(self) -> str | None:
        """返回最近一次已完成命令的主日志路径，供 CLI 错误输出定位。"""
        if self._last_result is None:
            return None
        return self._last_result.paths.main.as_posix()

    def run(
        self,
        command: str,
        args: object,
        handler: Callable[[object], object],
    ) -> object:
        """执行一个 handler 并记录开始、结果、异常和资源关闭。

        参数：
            command: 固定命令名。
            args: parser/application 传入的参数对象。
            handler: 接收 args 并返回业务结果的调用对象。

        返回：
            handler 原样返回的结果对象。

        异常、约束与副作用：
            命令文本、会话工厂和 handler 类型非法时抛 ``ValueError``/``TypeError``；
            handler 的异常经过日志转换后原样重抛。日志写入和关闭失败不被伪装成
            业务成功。
        """
        if (
            not isinstance(command, str)
            or not command
            or any(character in command for character in "\r\n")
        ):
            raise ValueError("command 必须是非空且不含换行的字符串")
        if not callable(handler):
            raise TypeError("handler 必须可调用")
        session = self._session_factory(command, args)
        if not isinstance(session, RunLogSession):
            raise TypeError("session_factory 必须返回 RunLogSession")
        session.start(fields=_command_start_fields(command, args))
        try:
            try:
                with session.phase("handler", fields=(("command", command),)):
                    business_result = handler(args)
            except KeyboardInterrupt:
                log_result = session.finish(
                    RunOutcome.CANCELLED,
                    message="命令取消",
                    fields=(("command", command),),
                )
                self._last_result = log_result
                self._record_summary(command, log_result, "命令取消", None)
                raise
            except BaseException as exc:
                failure = build_failure_from_exception(
                    exc,
                    build_id=session.context.build_id,
                    task_name=session.context.task_name,
                )
                log_result = session.finish(
                    RunOutcome.FAILED,
                    message=failure.message,
                    fields=(
                        ("command", command),
                        (
                            "exception_type",
                            failure.cause.exception_type if failure.cause else type(exc).__name__,
                        ),
                    ),
                    error_code=failure.error_code,
                    diagnostic_paths=failure.diagnostic_paths,
                )
                self._last_result = log_result
                self._record_summary(
                    command,
                    log_result,
                    failure.message,
                    failure.error_code,
                )
                raise
            _outcome, message, error_code, log_result = self._finish_result(
                session, command, business_result
            )
            self._last_result = log_result
            self._record_summary(command, log_result, message, error_code)
            return business_result
        finally:
            session.close()

    @staticmethod
    def _finish_result(
        session: RunLogSession,
        command: str,
        result: object,
    ) -> tuple[RunOutcome, str, ErrorCode | None, RunLogResult]:
        """把 handler 返回值中的可选 state/error 转成终态事件。"""
        state_object = getattr(result, "state", None)
        state_value = getattr(state_object, "value", state_object)
        state = state_value if isinstance(state_value, str) else None
        fields: tuple[tuple[str, str], ...] = (("command", command),)
        if state is not None:
            fields += (("state", state),)
        error_object = getattr(result, "error", None)
        message = error_object if isinstance(error_object, str) and error_object else None
        if state in {"FAILED", "CONFLICTED"}:
            code = (
                ErrorCode.PUBLISH_CAS_CONFLICT
                if state == "CONFLICTED"
                else ErrorCode.INTERNAL_ERROR
            )
            selected_message = message or "命令失败"
            log_result = session.finish(
                RunOutcome.FAILED,
                message=selected_message,
                fields=fields,
                error_code=code,
            )
            return RunOutcome.FAILED, selected_message, code, log_result
        elif state in {"CANCELLED", "CANCELED"}:
            selected_message = message or "命令取消"
            log_result = session.finish(
                RunOutcome.CANCELLED,
                message=selected_message,
                fields=fields,
            )
            return RunOutcome.CANCELLED, selected_message, None, log_result
        else:
            selected_message = message or "命令完成"
            log_result = session.finish(
                RunOutcome.SUCCEEDED,
                message=selected_message,
                fields=fields,
            )
            return RunOutcome.SUCCEEDED, selected_message, None, log_result

    def _record_summary(
        self,
        command: str,
        result: RunLogResult,
        message: str,
        error_code: ErrorCode | None,
    ) -> None:
        """把已完成会话交给可选 build.log collector 并刷新原子汇总。"""
        if self._collector is None:
            return
        record = getattr(self._collector, "record")
        write = getattr(self._collector, "write")
        record(result, message=f"{command}: {message}", error_code=error_code)
        write(finished_at=result.finished_at, outcome=result.outcome)


def _command_start_fields(command: str, args: object) -> tuple[tuple[str, str], ...]:
    """提取命令开始时可公开记录的配置、Profile、平台和 dry-run 字段。"""
    fields: list[tuple[str, str]] = [("command", command)]
    names = (
        ("profile", "profile"),
        ("revision", "source_revision"),
        ("platform", "platform"),
        ("config", "config_locator"),
        ("dry_run", "dry_run"),
    )
    for attribute, field_name in names:
        value = getattr(args, attribute, None)
        if value is None:
            continue
        if isinstance(value, bool):
            value_text = str(value).lower()
        elif isinstance(value, str) and value:
            value_text = value
        else:
            continue
        fields.append((field_name, value_text))
    return tuple(fields)


__all__ = ["CommandLogRunner", "RunLogResult", "RunLogSession", "RunOutcome"]
