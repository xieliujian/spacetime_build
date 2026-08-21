"""本地外部进程执行适配器。

本模块把 ``ProcessRunner`` 端口落实为跨平台本地执行器：以参数列表启动独立进程
组，并发排空标准输出与标准错误，对文本脱敏后排他写入诊断文件；同时处理超时、
协作取消、输出失败和进程树终止。模块不解析秘密绑定，也不记录环境变量。
"""

from __future__ import annotations

import codecs
import math
import os
import signal
import subprocess
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Protocol, cast

from core.errors import ConfigurationError
from observability.context import LogContext
from observability.failures import ErrorCode
from observability.handlers import ExternalStreamWriter
from observability.logger import LogEvent, Logger, LogLevel
from observability.redaction import redact_arguments, redact_text
from ports.process import (
    CancellationToken,
    ProcessOutcome,
    ProcessRequest,
    ProcessResult,
    ProcessRunner,
    ProcessTextSink,
    SecretBindingTarget,
)

_READ_CHUNK_BYTES = 64 * 1024
_MAX_RESULT_TAIL_BYTES = 65_536


class _Decoder(Protocol):
    """UTF-8 增量解码器所需的最小内部协议。

    参数与返回：
        由 ``codecs`` 工厂创建；``decode`` 接收字节并返回文本。

    异常、约束与副作用：
        解码错误策略由构造参数固定为替换；协议自身无外部副作用。
    """

    def decode(self, input: bytes, final: bool = False) -> str:
        """增量解码一段字节。

        参数：
            input: 当前字节块。
            final: 是否为流末尾刷新。

        返回：
            已完整解码的 Unicode 文本。

        异常、约束与副作用：
            实现异常由 reader 捕获；只维护解码状态，不执行 I/O。
        """
        ...


def _safe_error_text(error: BaseException | str) -> str:
    """把异常或诊断文本安全转换为非空脱敏字符串。

    参数：
        error: 原异常或内部诊断字符串。

    返回：
        不包含常见凭据形式的非空文本；异常无法字符串化时使用稳定占位符。

    异常、约束与副作用：
        吞掉异常 ``__str__`` 的二次失败；不记录日志且无 I/O。
    """
    if isinstance(error, str):
        raw_text = error
        fallback = "进程执行失败"
    else:
        fallback = f"<{type(error).__name__} 无法安全字符串化>"
        try:
            raw_text = str(error)
        except BaseException:
            raw_text = fallback
    redacted = redact_text(raw_text)
    return redacted if redacted.strip() else fallback


def _combine_diagnostics(*messages: str) -> str:
    """合并多个可选诊断并再次执行整体脱敏。

    参数：
        messages: 按发生顺序排列的诊断文本。

    返回：
        忽略空白项后以分号连接的非空安全文本；全部为空时返回稳定兜底消息。

    异常、约束与副作用：
        输入由内部调用方保证为字符串；函数只分配内存，无 I/O。
    """
    useful_messages = tuple(message for message in messages if message.strip())
    if not useful_messages:
        return "进程执行失败"
    return redact_text("; ".join(useful_messages))


def _validate_positive_float(value: object, *, field_name: str) -> float:
    """校验构造参数是有限正数并规范化为浮点数。

    参数：
        value: 待校验对象。
        field_name: 用于异常消息的字段名。

    返回：
        等值 ``float``。

    异常、约束与副作用：
        类型非法时抛出 ``TypeError``；非有限或非正时抛出 ``ValueError``。无 I/O。
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{field_name} 必须是 int 或 float")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field_name} 必须是有限正数")
    return normalized


def _utf8_tail(text: str, limit_bytes: int) -> str:
    """按 UTF-8 字节数保留文本尾部且不产生残缺码点。

    参数：
        text: 已由唯一脱敏流产出的完整合法文本。
        limit_bytes: 严格正的最大 UTF-8 字节数。

    返回：
        编码长度不超过上限、从完整 Unicode 码点开始的最长可用尾部。

    异常、约束与副作用：
        参数由内部调用方保证有效；函数只分配内存，不执行 I/O。截断点落在多字节
        码点内部时跳过开头 continuation bytes，绝不使用替换字符掩盖边界错误。
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text
    tail = encoded[-limit_bytes:]
    first_complete = 0
    while first_complete < len(tail) and tail[first_complete] & 0xC0 == 0x80:
        first_complete += 1
    return tail[first_complete:].decode("utf-8")


class _StreamCapture:
    """单个外部输出流的线程安全捕获状态。

    参数：
        writer: 已排他创建的脱敏输出 writer。
        tail_limit_chars: 兼容保留的参数名，实际表示内存尾部 UTF-8 字节上限。
        changed: reader 状态变化时唤醒主循环的事件。

    返回：
        无；实例供唯一 reader 写入、主线程读取结果。

    异常、约束与副作用：
        ``append`` 可能传播 writer I/O 异常；写文件副作用仅由 writer 完成。
    """

    __slots__ = ("_changed", "_error", "_lock", "_tail", "_tail_limit_bytes", "writer")

    def __init__(
        self,
        writer: ProcessTextSink,
        tail_limit_chars: int,
        changed: threading.Event,
    ) -> None:
        """保存 writer、尾部上限和共享唤醒事件。

        参数：
            writer: 当前流专属 writer。
            tail_limit_chars: 已校验的正 UTF-8 字节上限。
            changed: 与主循环共享的条件事件。

        返回：
            无。

        异常、约束与副作用：
            参数由 ``LocalProcessRunner`` 保证有效；仅创建锁和内存状态。
        """
        self.writer = writer
        self._tail_limit_bytes = tail_limit_chars
        self._changed = changed
        self._tail = ""
        self._error: BaseException | None = None
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        """写入脱敏文件并更新脱敏后的有界尾部。

        参数：
            text: UTF-8 增量解码得到的文本片段。

        返回：
            writer 写入成功后返回 ``None``。

        异常、约束与副作用：
            writer 脱敏或 I/O 异常直接传播；成功时追加输出文件并更新内存尾部。
        """
        emitted = self.writer.write_text(text)
        self._append_emitted(emitted)

    def close(self) -> None:
        """关闭 writer，并把流式脱敏器 finalize 的文本并入同一 tail。

        没有参数和返回值。首次关闭可能执行文件写入并更新 tail；writer 的幂等关闭
        允许 finally 再次调用。close 或脱敏错误原样传播给主线程统一映射。
        """
        emitted = self.writer.close()
        self._append_emitted(emitted)

    def _append_emitted(self, emitted: str) -> None:
        """只把 writer 已实际产出的安全文本追加到 UTF-8 有界 tail。

        参数：
            emitted: ``write_text`` 或 ``close`` 返回的脱敏文本。

        返回：
            无。

        异常、约束与副作用：
            writer 契约保证输入为字符串；仅在锁内更新内存，不再次脱敏或写文件。
        """
        if not emitted:
            return
        with self._lock:
            self._tail = _utf8_tail(self._tail + emitted, self._tail_limit_bytes)

    def fail(self, error: BaseException) -> None:
        """记录首个 reader 异常并唤醒主循环。

        参数：
            error: reader 捕获的异常。

        返回：
            无；后续异常不会覆盖首个根因。

        异常、约束与副作用：
            不抛出异常；只修改内存状态并设置事件。
        """
        with self._lock:
            if self._error is None:
                self._error = error
        self._changed.set()

    @property
    def error(self) -> BaseException | None:
        """返回首个 reader 异常。

        无参数；返回异常对象仅供本模块安全字符串化。线程安全，无外部副作用。
        """
        with self._lock:
            return self._error

    @property
    def tail(self) -> str:
        """返回当前脱敏有界尾部。

        无参数；返回不可变字符串快照。线程安全，不访问输出文件。
        """
        with self._lock:
            return self._tail


def _read_stream(
    stream: BinaryIO,
    capture: _StreamCapture,
    changed: threading.Event,
) -> None:
    """持续排空一个二进制管道并写入对应捕获器。

    参数：
        stream: 外部程序 stdout 或 stderr 二进制管道。
        capture: 当前流专属捕获状态。
        changed: reader 完成或失败时唤醒主循环的事件。

    返回：
        到达 EOF 或捕获失败后返回 ``None``。

    异常、约束与副作用：
        所有 ``BaseException`` 都转存到 capture，绝不跨线程抛出；读取管道并写入
        脱敏诊断文件。UTF-8 非法字节固定替换，不导致 reader 崩溃。
    """
    try:
        decoder_factory = codecs.getincrementaldecoder("utf-8")
        decoder = cast(_Decoder, decoder_factory(errors="replace"))
        while True:
            chunk = stream.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            decoded = decoder.decode(chunk)
            if decoded:
                capture.append(decoded)
        final_text = decoder.decode(b"", final=True)
        if final_text:
            capture.append(final_text)
    except BaseException as exc:
        capture.fail(exc)
    finally:
        changed.set()


class LocalProcessRunner(ProcessRunner):
    """安全执行本地外部程序的 ``ProcessRunner`` 实现。

    参数：
        logger: 可选结构化 logger；命令事件只包含 ``redact_arguments`` 结果。
        log_context: 可选真实运行日志上下文，必须与 logger 同时提供或同时省略。
        poll_interval_seconds: 主循环条件轮询的有限正间隔。
        tail_limit_chars: 兼容保留的参数名，表示 stdout/stderr 内存尾部 UTF-8 字节
            上限，范围 ``1..65536``。
        termination_grace_seconds: 温和终止和 reader 收尾的有限正宽限秒数。

    返回：
        无；构造成功后可重复调用 ``run``，每次调用状态互相隔离。

    异常、约束与副作用：
        构造参数非法时抛出 ``TypeError`` 或 ``ValueError``。构造不创建文件、不启
        动外部程序；``run`` 的文件和进程副作用严格限制在请求路径与进程树。
    """

    __slots__ = (
        "_logger",
        "_log_context",
        "_poll_interval_seconds",
        "_tail_limit_chars",
        "_termination_grace_seconds",
    )

    def __init__(
        self,
        logger: Logger | None = None,
        log_context: LogContext | None = None,
        poll_interval_seconds: float = 0.05,
        tail_limit_chars: int = 65_536,
        termination_grace_seconds: float = 2.0,
    ) -> None:
        """校验并保存依赖与等待边界。

        参数：
            logger: 可选 ``Logger`` 实现。
            log_context: 与 logger 配套的真实 ``LogContext``。
            poll_interval_seconds: 条件轮询间隔。
            tail_limit_chars: 内存尾部 UTF-8 字节上限，参数名为兼容旧调用保留。
            termination_grace_seconds: 终止与收尾宽限。

        返回：
            无。

        异常、约束与副作用：
            logger 不符合协议或数值非法时抛出 ``TypeError`` / ``ValueError``；仅保
            存内存配置，不调用 logger、不创建文件也不启动程序。
        """
        logger_object = cast(object, logger)
        if logger_object is not None and not isinstance(logger_object, Logger):
            raise TypeError("logger 必须实现 Logger 或为 None")
        context_object = cast(object, log_context)
        if context_object is not None and not isinstance(context_object, LogContext):
            raise TypeError("log_context 必须是 LogContext 或 None")
        if (logger_object is None) != (context_object is None):
            raise ValueError("logger 与 log_context 必须同时提供或同时为 None")
        tail_limit_object = cast(object, tail_limit_chars)
        if not isinstance(tail_limit_object, int) or isinstance(tail_limit_object, bool):
            raise TypeError("tail_limit_chars 必须是 int")
        if tail_limit_object <= 0 or tail_limit_object > _MAX_RESULT_TAIL_BYTES:
            raise ValueError("tail_limit_chars 必须位于 1..65536")

        self._logger = logger
        self._log_context = log_context
        self._poll_interval_seconds = _validate_positive_float(
            poll_interval_seconds,
            field_name="poll_interval_seconds",
        )
        self._tail_limit_chars = tail_limit_object
        self._termination_grace_seconds = _validate_positive_float(
            termination_grace_seconds,
            field_name="termination_grace_seconds",
        )

    def run(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        """解析短期秘密 binding，执行请求并在所有路径关闭租约。"""
        request_object = cast(object, request)
        if not isinstance(request_object, ProcessRequest):
            raise TypeError("request 必须是 ProcessRequest")
        cancellation_object = cast(object, cancellation)
        if cancellation_object is not None and not isinstance(
            cancellation_object, CancellationToken
        ):
            raise TypeError("cancellation 必须是 CancellationToken 或 None")
        lease = request_object.secret_lease
        temporary_paths: list[Path] = []
        try:
            if cancellation_object is not None and cancellation_object.is_cancelled:
                no_secret_request = replace(request_object, secret_bindings=(), secret_lease=None)
                return self._run_request(no_secret_request, cancellation_object)
            prepared, stdin_secret, temporary_paths, secret_values = self._prepare_secret_request(
                request_object
            )
            return self._run_request(
                prepared,
                cancellation_object,
                stdin_secret=stdin_secret,
                secret_values=secret_values,
            )
        finally:
            for path in temporary_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if lease is not None:
                lease.close()

    def _prepare_secret_request(
        self,
        request: ProcessRequest,
    ) -> tuple[ProcessRequest, bytes | None, list[Path], tuple[str, ...]]:
        """把租约秘密绑定到白名单进程槽位并返回无秘密请求副本。"""
        if bool(request.secret_bindings) != (request.secret_lease is not None):
            raise ConfigurationError("秘密 binding 与 lease 必须同时提供或同时为空")
        if not request.secret_bindings:
            return request, None, [], ()
        assert request.secret_lease is not None
        arguments = list(request.arguments)
        environment = dict(request.environment)
        stdin_secret: bytes | None = None
        temporary_paths: list[Path] = []
        secret_values: list[str] = []
        try:
            for binding in request.secret_bindings:
                value = request.secret_lease.resolve(binding.binding_id)
                if not isinstance(value, str) or not value:
                    raise ConfigurationError("秘密 binding 解析结果必须是非空字符串")
                secret_values.append(value)
                if binding.target is SecretBindingTarget.ARGUMENT:
                    index = int(binding.slot)
                    if index >= len(arguments) or index not in request.redacted_argument_indexes:
                        raise ConfigurationError("秘密参数必须绑定到存在且已脱敏的 argv 索引")
                    arguments[index] = value
                elif binding.target is SecretBindingTarget.ENVIRONMENT:
                    if binding.slot in environment:
                        raise ConfigurationError("秘密环境槽位不得覆盖显式环境变量")
                    environment[binding.slot] = value
                elif binding.target is SecretBindingTarget.STDIN:
                    if stdin_secret is not None:
                        raise ConfigurationError("一个进程只能有一个秘密 stdin binding")
                    stdin_secret = value.encode("utf-8")
                else:
                    path = request.working_directory / f".secret-{binding.binding_id}.tmp"
                    resolved = path.resolve()
                    if resolved.parent != request.working_directory.resolve():
                        raise ConfigurationError("秘密临时文件路径越出工作目录")
                    path.write_text(value, encoding="utf-8")
                    temporary_paths.append(path)
                    environment[binding.slot] = str(path)
            return (
                replace(
                    request,
                    arguments=tuple(arguments),
                    environment=tuple(environment.items()),
                    secret_bindings=(),
                    secret_lease=None,
                ),
                stdin_secret,
                temporary_paths,
                tuple(secret_values),
            )
        except BaseException:
            for path in temporary_paths:
                path.unlink(missing_ok=True)
            raise

    def _run_request(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None = None,
        *,
        stdin_secret: bytes | None = None,
        secret_values: tuple[str, ...] = (),
    ) -> ProcessResult:
        """执行一个已校验请求并收集有界、脱敏诊断。

        参数：
            request: ``ProcessRequest``，环境按请求显式替换，不继承当前环境。
            cancellation: 可选协作式取消令牌。

        返回：
            覆盖完成、超时、取消、终止、启动和输出失败的 ``ProcessResult``。

        异常、约束与副作用：
            请求类型非法时抛出 ``TypeError``；其他运行失败转换为结果。方法
            可能创建两个精确输出文件并启动独立进程组，但不记录环境或秘密值。
        """
        request_object = cast(object, request)
        if not isinstance(request_object, ProcessRequest):
            raise TypeError("request 必须是 ProcessRequest")
        cancellation_object = cast(object, cancellation)
        if cancellation_object is not None and not isinstance(
            cancellation_object,
            CancellationToken,
        ):
            raise TypeError("cancellation 必须是 CancellationToken 或 None")

        stdout_sink = request_object.stdout_sink
        stderr_sink = request_object.stderr_sink
        provided_writers: list[ProcessTextSink] = (
            [stdout_sink, stderr_sink]
            if stdout_sink is not None and stderr_sink is not None
            else []
        )

        started_at = time.monotonic()
        if cancellation_object is not None and cancellation_object.is_cancelled:
            close_error = self._close_writers(provided_writers)
            if close_error is None:
                result = self._make_result(
                    request_object,
                    started_at,
                    ProcessOutcome.CANCELLED,
                    None,
                    None,
                    None,
                    ErrorCode.PROCESS_CANCELLED,
                    "进程在启动前已取消",
                )
            else:
                result = self._make_result(
                    request_object,
                    started_at,
                    ProcessOutcome.OUTPUT_FAILED,
                    None,
                    None,
                    None,
                    ErrorCode.INTERNAL_ERROR,
                    _combine_diagnostics("预取消时输出 sink 关闭失败", close_error),
                )
            logger_error = self._emit_result(result, diagnostic_paths_available=False)
            if logger_error is not None:
                return self._make_result(
                    request_object,
                    started_at,
                    ProcessOutcome.START_FAILED,
                    None,
                    None,
                    None,
                    ErrorCode.INTERNAL_ERROR,
                    _combine_diagnostics("启动前日志发送失败", logger_error),
                )
            return result

        command_error = self._emit(
            LogLevel.INFO,
            self._redacted_command_message(request_object),
        )
        if command_error is not None:
            close_error = self._close_writers(provided_writers)
            return self._make_result(
                request_object,
                started_at,
                ProcessOutcome.START_FAILED,
                None,
                None,
                None,
                ErrorCode.INTERNAL_ERROR,
                _combine_diagnostics(
                    "启动前命令日志发送失败",
                    command_error,
                    close_error or "",
                ),
            )

        writers: list[ProcessTextSink] = []
        created_paths: list[Path] = []
        try:
            if provided_writers:
                stdout_writer, stderr_writer = provided_writers
                writers.extend(provided_writers)
            else:
                stdout_writer = ExternalStreamWriter(
                    request_object.stdout_path,
                    secret_values=secret_values,
                )
                writers.append(stdout_writer)
                created_paths.append(request_object.stdout_path)
                stderr_writer = ExternalStreamWriter(
                    request_object.stderr_path,
                    secret_values=secret_values,
                )
                writers.append(stderr_writer)
                created_paths.append(request_object.stderr_path)
        except BaseException as exc:
            cleanup_error = self._rollback_unpreserved(writers, created_paths)
            diagnostic = _combine_diagnostics(
                "输出文件初始化失败",
                _safe_error_text(exc),
                cleanup_error or "",
            )
            result = self._make_result(
                request_object,
                started_at,
                ProcessOutcome.OUTPUT_FAILED,
                None,
                None,
                None,
                ErrorCode.INTERNAL_ERROR,
                diagnostic,
            )
            logger_error = self._emit_result(result, diagnostic_paths_available=False)
            if logger_error is not None:
                return self._make_result(
                    request_object,
                    started_at,
                    ProcessOutcome.START_FAILED,
                    None,
                    None,
                    None,
                    ErrorCode.INTERNAL_ERROR,
                    _combine_diagnostics(diagnostic, "启动前日志发送失败", logger_error),
                )
            return result

        process: subprocess.Popen[bytes]
        try:
            process = self._spawn(request_object, stdin_secret=stdin_secret)
        except BaseException as exc:
            close_error = self._close_writers(writers)
            result = self._make_result(
                request_object,
                started_at,
                ProcessOutcome.START_FAILED,
                None,
                None,
                None,
                ErrorCode.INTERNAL_ERROR,
                _combine_diagnostics(
                    "外部程序启动失败",
                    _safe_error_text(exc),
                    close_error or "",
                ),
            )
            self._emit_result(result)
            return result

        return self._run_spawned(
            request_object,
            cancellation_object,
            started_at,
            process,
            stdout_writer,
            stderr_writer,
        )

    def _spawn(
        self,
        request: ProcessRequest,
        *,
        stdin_secret: bytes | None = None,
    ) -> subprocess.Popen[bytes]:
        """以独立进程组和双二进制管道启动请求。

        参数：
            request: 已校验且不含秘密字段的请求。
            stdin_secret: 可选的 UTF-8 秘密 stdin 内容。

        返回：
            已启动、stdout/stderr 均为 PIPE 的 ``Popen`` 对象。

        异常、约束与副作用：
            启动异常原样传播给 ``run`` 转换；使用 ``shell=False``、显式 cwd/env、
            管道或 DEVNULL stdin，并按平台创建可整体终止的进程组。
        """
        arguments = [str(request.executable), *request.arguments]
        stdin = subprocess.PIPE if stdin_secret is not None else subprocess.DEVNULL
        if os.name == "nt":
            process = subprocess.Popen(
                arguments,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=request.working_directory,
                env=dict(request.environment),
                shell=False,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = subprocess.Popen(
                arguments,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=request.working_directory,
                env=dict(request.environment),
                shell=False,
                start_new_session=True,
            )
        if stdin_secret is not None and process.stdin is not None:
            try:
                process.stdin.write(stdin_secret)
                process.stdin.close()
            except BaseException:
                try:
                    process.kill()
                    process.wait(timeout=self._termination_grace_seconds)
                except BaseException:
                    pass
                raise
        return process

    def _run_spawned(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None,
        started_at: float,
        process: subprocess.Popen[bytes],
        stdout_writer: ProcessTextSink,
        stderr_writer: ProcessTextSink,
    ) -> ProcessResult:
        """监控已启动进程，并保证进程、管道、reader 和 writer 收尾。

        参数：
            request: 当前请求。
            cancellation: 可选取消令牌。
            started_at: ``run`` 入口单调时钟。
            process: 已启动进程。
            stdout_writer: 标准输出 writer。
            stderr_writer: 标准错误 writer。

        返回：
            完整 ``ProcessResult``；logger 和运行异常不会逸出。

        异常、约束与副作用：
            内部异常转换为失败结果。方法并发读取管道、写输出文件，必要时终止进程
            树；finally 仍会关闭精确资源并尽力回收残留父进程。
        """
        changed = threading.Event()
        stdout_capture = _StreamCapture(stdout_writer, self._tail_limit_chars, changed)
        stderr_capture = _StreamCapture(stderr_writer, self._tail_limit_chars, changed)
        captures = (stdout_capture, stderr_capture)
        threads: list[threading.Thread] = []
        pipes: list[BinaryIO] = []
        trigger = "output"
        termination_error: str | None = None
        internal_output_error: str | None = None
        deadline = time.monotonic() + request.timeout_seconds

        try:
            started_log_error = self._emit(
                LogLevel.INFO,
                "外部进程已启动",
                fields=(("pid", str(process.pid)),),
            )
            if started_log_error is not None:
                internal_output_error = _combine_diagnostics(
                    "启动后日志发送失败",
                    started_log_error,
                )
            else:
                if process.stdout is None or process.stderr is None:
                    internal_output_error = "进程输出管道未创建"
                else:
                    stdout_pipe = cast(BinaryIO, process.stdout)
                    stderr_pipe = cast(BinaryIO, process.stderr)
                    pipes = [stdout_pipe, stderr_pipe]
                    candidate_threads = [
                        threading.Thread(
                            target=_read_stream,
                            args=(stdout_pipe, stdout_capture, changed),
                            name=f"process-{process.pid}-stdout",
                            daemon=True,
                        ),
                        threading.Thread(
                            target=_read_stream,
                            args=(stderr_pipe, stderr_capture, changed),
                            name=f"process-{process.pid}-stderr",
                            daemon=True,
                        ),
                    ]
                    try:
                        for thread in candidate_threads:
                            thread.start()
                            threads.append(thread)
                    except BaseException as exc:
                        internal_output_error = _combine_diagnostics(
                            "进程输出 reader 启动失败",
                            _safe_error_text(exc),
                        )

            if internal_output_error is None:
                trigger = self._wait_for_trigger(
                    process,
                    captures,
                    cancellation,
                    changed,
                    deadline,
                )

            if trigger != "natural":
                termination_error = self._terminate_tree(process)

            reader_error = self._finish_readers(threads, pipes, changed)
            capture_error = self._first_capture_error(captures)
            close_error = self._close_captures(captures)
            internal_output_error = (
                _combine_diagnostics(
                    internal_output_error or "",
                    reader_error or "",
                    capture_error or "",
                    close_error or "",
                )
                if any((internal_output_error, reader_error, capture_error, close_error))
                else None
            )

            result = self._select_result(
                request,
                started_at,
                process,
                stdout_capture,
                stderr_capture,
                trigger,
                termination_error,
                internal_output_error,
            )
            final_log_error = self._emit_result(result)
            if (
                final_log_error is not None
                and result.outcome is not ProcessOutcome.TERMINATION_FAILED
            ):
                result = self._make_result(
                    request,
                    started_at,
                    ProcessOutcome.OUTPUT_FAILED,
                    process.poll(),
                    stdout_capture,
                    stderr_capture,
                    ErrorCode.INTERNAL_ERROR,
                    _combine_diagnostics(
                        result.diagnostic_message,
                        "启动后结果日志发送失败",
                        final_log_error,
                    ),
                )
            return result
        finally:
            # 所有正常分支已关闭资源；异常分支继续做幂等关闭与父进程兜底回收。
            for pipe in pipes:
                try:
                    pipe.close()
                except BaseException:
                    pass
            self._close_writers([stdout_writer, stderr_writer])
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=self._termination_grace_seconds)
                except BaseException:
                    pass

    def _wait_for_trigger(
        self,
        process: subprocess.Popen[bytes],
        captures: tuple[_StreamCapture, _StreamCapture],
        cancellation: CancellationToken | None,
        changed: threading.Event,
        deadline: float,
    ) -> str:
        """按固定优先级等待自然退出、输出失败、取消或超时。

        参数：
            process: 当前外部进程。
            captures: stdout/stderr 捕获状态。
            cancellation: 可选取消令牌。
            changed: reader 条件事件。
            deadline: 基于单调时钟的进程超时截止点。

        返回：
            ``natural``、``output``、``cancel`` 或 ``timeout``。

        异常、约束与副作用：
            不抛出业务异常。每轮重新读取实时条件，并用 Event 有界等待，不调用任
            意 sleep；同轮取消优先于超时。
        """
        while True:
            if process.poll() is not None:
                return "natural"
            if any(capture.error is not None for capture in captures):
                return "output"
            if cancellation is not None and cancellation.is_cancelled:
                return "cancel"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            changed.clear()
            changed.wait(min(self._poll_interval_seconds, remaining))

    def _terminate_tree(self, process: subprocess.Popen[bytes]) -> str | None:
        """按平台终止完整进程树并验证父进程已退出。

        参数：
            process: 需要终止的外部进程。

        返回：
            成功为 ``None``；任何命令、信号或等待失败返回脱敏诊断。

        异常、约束与副作用：
            所有异常转为文本。Windows 仅调用 SystemRoot 下固定 taskkill；POSIX 对
            独立进程组先 TERM、宽限后 KILL，不执行 shell。
        """
        try:
            if os.name == "nt":
                system_root = os.environ.get("SystemRoot")
                if not system_root:
                    return "SystemRoot 未配置，无法定位 taskkill.exe"
                taskkill_path = Path(system_root) / "System32" / "taskkill.exe"
                completed = subprocess.run(
                    [str(taskkill_path), "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    timeout=self._termination_grace_seconds,
                    check=False,
                )
                if completed.returncode != 0:
                    if process.poll() is not None:
                        return None
                    try:
                        process.wait(timeout=self._termination_grace_seconds)
                    except subprocess.TimeoutExpired:
                        return f"taskkill.exe 退出码为 {completed.returncode}"
                    except BaseException as exc:
                        return _combine_diagnostics(
                            "taskkill.exe 非零后回收父进程失败",
                            _safe_error_text(exc),
                        )
                    if process.poll() is not None:
                        return None
                    return f"taskkill.exe 退出码为 {completed.returncode}"
                process.wait(timeout=self._termination_grace_seconds)
                if process.poll() is None:
                    return "taskkill.exe 成功后父进程仍在运行"
                return None

            # spawn 时以父 PID 创建了本次独立 session/process group；整个终止函数
            # 只使用这个固定 pgid，观察到组消失后立即返回，绝不继续向可能复用的
            # 数字 ID 发送信号。
            pgid = process.pid
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                return self._reap_posix_parent(process)

            disappeared, probe_error = self._wait_for_posix_group_exit(
                process,
                pgid,
                time.monotonic() + self._termination_grace_seconds,
            )
            if probe_error is not None:
                return probe_error
            if disappeared:
                return self._reap_posix_parent(process)

            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                return self._reap_posix_parent(process)

            disappeared, probe_error = self._wait_for_posix_group_exit(
                process,
                pgid,
                time.monotonic() + self._termination_grace_seconds,
            )
            if probe_error is not None:
                return probe_error
            if not disappeared:
                return "POSIX 进程组在 SIGKILL 后仍然存在"
            return self._reap_posix_parent(process)
        except BaseException as exc:
            return _combine_diagnostics("进程树终止失败", _safe_error_text(exc))

    def _wait_for_posix_group_exit(
        self,
        process: subprocess.Popen[bytes],
        pgid: int,
        deadline: float,
    ) -> tuple[bool, str | None]:
        """以信号 0 有界探测整个 POSIX 进程组是否消失。

        参数：
            process: 本次 spawn 的父进程，仅用于非阻塞回收其退出状态。
            pgid: spawn 时固定为父 PID 的本次独立进程组 ID。
            deadline: 当前 TERM 或 KILL 阶段的单调时钟截止点。

        返回：
            ``(True, None)`` 表示 ``ProcessLookupError`` 已确认组消失；截止仍存在为
            ``(False, None)``；权限或其他 OS 探测错误返回 ``(False, 诊断)``。

        异常、约束与副作用：
            不向外抛出探测异常。父 ``wait(timeout=0)`` 只回收父进程，绝不作为组
            消失证据；轮询使用 ``Event.wait``，不调用任意 sleep。观察到组消失后
            立即停止探测，避免在 pgid 数字被复用后继续操作。
        """
        changed = threading.Event()
        kill_process_group = cast(Callable[[int, int], None], getattr(os, "killpg"))
        while True:
            try:
                process.wait(timeout=0)
            except subprocess.TimeoutExpired:
                pass
            except BaseException as exc:
                return False, _combine_diagnostics(
                    "POSIX 父进程回收失败",
                    _safe_error_text(exc),
                )

            try:
                kill_process_group(pgid, 0)
            except ProcessLookupError:
                return True, None
            except PermissionError as exc:
                return False, _combine_diagnostics(
                    "POSIX 进程组探测权限不足",
                    _safe_error_text(exc),
                )
            except OSError as exc:
                return False, _combine_diagnostics(
                    "POSIX 进程组探测失败",
                    _safe_error_text(exc),
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, None
            changed.wait(min(self._poll_interval_seconds, remaining))

    def _reap_posix_parent(self, process: subprocess.Popen[bytes]) -> str | None:
        """在组已确认消失后有界回收本次父进程。

        参数：
            process: 本次 spawn 的父 ``Popen`` 对象。

        返回：
            父进程已回收为 ``None``；等待失败或宽限后仍无退出码时返回安全诊断。

        异常、约束与副作用：
            所有异常转换为诊断；这里只回收父进程句柄，不据此判断完整组状态，也
            不再发送任何信号。
        """
        try:
            process.wait(timeout=self._termination_grace_seconds)
            if process.poll() is None:
                return "POSIX 进程组消失后父进程仍无法回收"
            return None
        except BaseException as exc:
            return _combine_diagnostics("POSIX 父进程回收失败", _safe_error_text(exc))

    def _finish_readers(
        self,
        threads: list[threading.Thread],
        pipes: list[BinaryIO],
        changed: threading.Event,
    ) -> str | None:
        """有界等待 reader，并关闭导致 EOF 无法到达的父侧管道。

        参数：
            threads: 已创建的 reader 线程。
            pipes: 与 reader 对应的父侧二进制管道。
            changed: reader 状态变化事件。

        返回：
            全部退出为 ``None``；关闭或线程残留返回脱敏诊断。

        异常、约束与副作用：
            不向外抛出。先按实际线程完成条件等待，超限后关闭精确管道再做一次有界
            等待，避免后代继承 pipe 时无限挂起。
        """
        if not threads:
            for pipe in pipes:
                try:
                    pipe.close()
                except BaseException as exc:
                    return _combine_diagnostics("输出管道关闭失败", _safe_error_text(exc))
            return None

        deadline = time.monotonic() + self._termination_grace_seconds
        self._wait_for_threads(threads, changed, deadline)
        errors: list[str] = []
        if any(thread.is_alive() for thread in threads):
            for pipe in pipes:
                try:
                    pipe.close()
                except BaseException as exc:
                    errors.append(_safe_error_text(exc))
            second_deadline = time.monotonic() + self._termination_grace_seconds
            self._wait_for_threads(threads, changed, second_deadline)
        else:
            for pipe in pipes:
                try:
                    pipe.close()
                except BaseException as exc:
                    errors.append(_safe_error_text(exc))
        if any(thread.is_alive() for thread in threads):
            errors.append("输出 reader 未在有界收尾期内退出")
        if errors:
            return _combine_diagnostics("输出 reader 收尾失败", *errors)
        return None

    def _wait_for_threads(
        self,
        threads: list[threading.Thread],
        changed: threading.Event,
        deadline: float,
    ) -> None:
        """以事件条件等待 reader 全部结束或到达截止点。

        参数：
            threads: 待观察线程。
            changed: reader 完成事件。
            deadline: 单调时钟绝对截止点。

        返回：
            全部结束或到期后返回 ``None``。

        异常、约束与副作用：
            不抛出业务异常；不使用 sleep，只等待状态事件并用 ``join(0)`` 回收已完
            成线程的资源。
        """
        while any(thread.is_alive() for thread in threads):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            changed.clear()
            changed.wait(min(self._poll_interval_seconds, remaining))
        for thread in threads:
            thread.join(timeout=0)

    def _select_result(
        self,
        request: ProcessRequest,
        started_at: float,
        process: subprocess.Popen[bytes],
        stdout_capture: _StreamCapture,
        stderr_capture: _StreamCapture,
        trigger: str,
        termination_error: str | None,
        output_error: str | None,
    ) -> ProcessResult:
        """按终止、输出和原触发优先级选择结果。

        参数：
            request: 当前请求。
            started_at: 调用开始单调时钟。
            process: 已执行进程。
            stdout_capture: stdout 捕获状态。
            stderr_capture: stderr 捕获状态。
            trigger: 主循环退出原因。
            termination_error: 可选树终止失败诊断。
            output_error: 可选输出处理失败诊断。

        返回：
            满足端口 outcome/error_code 不变量的 ``ProcessResult``。

        异常、约束与副作用：
            输入由内部流程保证；仅创建内存结果，不访问文件或记录日志。
        """
        exit_code = process.poll()
        if termination_error is not None:
            return self._make_result(
                request,
                started_at,
                ProcessOutcome.TERMINATION_FAILED,
                exit_code,
                stdout_capture,
                stderr_capture,
                ErrorCode.INTERNAL_ERROR,
                termination_error,
            )
        if output_error is not None or trigger == "output":
            diagnostic = output_error or "输出 reader 失败"
            return self._make_result(
                request,
                started_at,
                ProcessOutcome.OUTPUT_FAILED,
                exit_code,
                stdout_capture,
                stderr_capture,
                ErrorCode.INTERNAL_ERROR,
                diagnostic,
            )
        if trigger == "cancel":
            return self._make_result(
                request,
                started_at,
                ProcessOutcome.CANCELLED,
                exit_code,
                stdout_capture,
                stderr_capture,
                ErrorCode.PROCESS_CANCELLED,
                "外部进程已取消",
            )
        if trigger == "timeout":
            return self._make_result(
                request,
                started_at,
                ProcessOutcome.TIMED_OUT,
                exit_code,
                stdout_capture,
                stderr_capture,
                ErrorCode.PROCESS_TIMEOUT,
                "外部进程执行超时",
            )
        if exit_code is None:
            return self._make_result(
                request,
                started_at,
                ProcessOutcome.TERMINATION_FAILED,
                None,
                stdout_capture,
                stderr_capture,
                ErrorCode.INTERNAL_ERROR,
                "自然完成后仍无法取得退出码",
            )
        return self._make_result(
            request,
            started_at,
            ProcessOutcome.COMPLETED,
            exit_code,
            stdout_capture,
            stderr_capture,
            None,
            "",
        )

    def _make_result(
        self,
        request: ProcessRequest,
        started_at: float,
        outcome: ProcessOutcome,
        exit_code: int | None,
        stdout_capture: _StreamCapture | None,
        stderr_capture: _StreamCapture | None,
        error_code: ErrorCode | None,
        diagnostic_message: str,
    ) -> ProcessResult:
        """从当前捕获快照创建不可变结果。

        参数：
            request: 当前请求与输出路径来源。
            started_at: 调用开始单调时钟。
            outcome: 结果类别。
            exit_code: 可选退出码。
            stdout_capture: 可选 stdout 捕获状态。
            stderr_capture: 可选 stderr 捕获状态。
            error_code: 与 outcome 对应的错误码。
            diagnostic_message: 失败诊断或完成时空字符串。

        返回：
            ``ProcessResult``。

        异常、约束与副作用：
            由 ``ProcessResult`` 校验内部不变量；只读取 writer 内存计数，无 I/O。
        """
        return ProcessResult(
            outcome=outcome,
            exit_code=exit_code,
            duration_seconds=max(0.0, time.monotonic() - started_at),
            stdout_path=request.stdout_path,
            stderr_path=request.stderr_path,
            stdout_bytes=stdout_capture.writer.byte_count if stdout_capture is not None else 0,
            stderr_bytes=stderr_capture.writer.byte_count if stderr_capture is not None else 0,
            stdout_tail=stdout_capture.tail if stdout_capture is not None else "",
            stderr_tail=stderr_capture.tail if stderr_capture is not None else "",
            error_code=error_code,
            diagnostic_message=_safe_error_text(diagnostic_message)
            if outcome is not ProcessOutcome.COMPLETED
            else "",
        )

    def _redacted_command_message(self, request: ProcessRequest) -> str:
        """使用 ``redact_arguments`` 构造唯一允许的命令日志摘要。

        参数：
            request: 当前请求。

        返回：
            可安全记录的 argv 元组文本；不包含环境字段。

        异常、约束与副作用：
            请求已校验，通常不抛出异常；纯内存处理，不访问环境或执行程序。
        """
        argv = (str(request.executable), *request.arguments)
        shifted_indexes = frozenset(index + 1 for index in request.redacted_argument_indexes)
        redacted_argv = redact_arguments(argv, redacted_indexes=shifted_indexes)
        return f"执行外部命令 argv={redacted_argv!r}"

    def _emit(
        self,
        level: LogLevel,
        message: str,
        *,
        fields: tuple[tuple[str, str], ...] = (),
        error_code: ErrorCode | None = None,
        diagnostic_paths: tuple[Path, ...] = (),
    ) -> str | None:
        """发送 aware UTC 日志事件并把 logger 失败转为安全文本。

        参数：
            level: 日志级别。
            message: 已安全组织的消息。
            fields: 不含环境或秘密的字符串字段。
            error_code: ERROR 事件错误码。
            diagnostic_paths: 已保留的精确诊断文件路径。

        返回：
            未配置 logger 或发送成功为 ``None``；失败为脱敏诊断。

        异常、约束与副作用：
            捕获事件构造及 logger 的全部异常，不向运行主路径抛出；配置 logger 时
            可能产生其声明的日志副作用。
        """
        if self._logger is None or self._log_context is None:
            return None
        try:
            self._logger.emit(
                LogEvent(
                    timestamp=datetime.now(timezone.utc),
                    level=level,
                    context=self._log_context,
                    message=redact_text(message),
                    fields=fields,
                    error_code=error_code,
                    diagnostic_paths=diagnostic_paths,
                )
            )
        except BaseException as exc:
            return _safe_error_text(exc)
        return None

    def _emit_result(
        self,
        result: ProcessResult,
        *,
        diagnostic_paths_available: bool = True,
    ) -> str | None:
        """发送结果事件，且绝不记录环境或未脱敏 argv。

        参数：
            result: 已创建的进程结果。
            diagnostic_paths_available: 两个输出文件是否已成功创建并作为诊断保留。

        返回：
            成功或无 logger 为 ``None``；logger 失败返回安全诊断。

        异常、约束与副作用：
            不向外抛出。配置 logger 时发送一条 INFO 或 ERROR 事件。
        """
        if result.outcome is ProcessOutcome.COMPLETED:
            return self._emit(
                LogLevel.INFO,
                "外部进程执行完成",
                fields=(("exit_code", str(result.exit_code)),),
            )
        return self._emit(
            LogLevel.ERROR,
            result.diagnostic_message,
            fields=(("outcome", result.outcome.value),),
            error_code=result.error_code,
            diagnostic_paths=(result.stdout_path, result.stderr_path)
            if diagnostic_paths_available
            else (),
        )

    @staticmethod
    def _first_capture_error(
        captures: tuple[_StreamCapture, _StreamCapture],
    ) -> str | None:
        """返回两个捕获器中的首个安全错误文本。

        参数：
            captures: stdout/stderr 捕获状态。

        返回：
            没有错误为 ``None``，否则为首个异常的脱敏文本。

        异常、约束与副作用：
            不抛出异常；仅读取线程安全内存状态。
        """
        for capture in captures:
            if capture.error is not None:
                return _safe_error_text(capture.error)
        return None

    @staticmethod
    def _close_writers(writers: list[ProcessTextSink]) -> str | None:
        """逆序关闭 writer，并在全部尝试后返回首个错误。

        参数：
            writers: 本次运行成功创建的 writer 列表。

        返回：
            全部关闭成功为 ``None``；否则为首个脱敏错误。

        异常、约束与副作用：
            不向外抛出；逐个关闭精确 writer，不删除文件。
        """
        first_error: str | None = None
        for writer in reversed(writers):
            try:
                writer.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = _safe_error_text(exc)
        return first_error

    @staticmethod
    def _close_captures(captures: tuple[_StreamCapture, _StreamCapture]) -> str | None:
        """逆序关闭捕获器，并保留各 sink finalize 后实际写出的安全文本。

        参数：
            captures: stdout/stderr 捕获状态。

        返回：
            全部关闭成功为 ``None``；否则返回首个已脱敏错误。

        异常、约束与副作用：
            不向外抛出；两项均会尝试关闭。成功关闭会同步更新对应 tail，失败交由
            主线程映射为 OUTPUT_FAILED，且不会删除调用方拥有的文件。
        """
        first_error: str | None = None
        for capture in reversed(captures):
            try:
                capture.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = _safe_error_text(exc)
        return first_error

    @classmethod
    def _rollback_unpreserved(
        cls,
        writers: list[ProcessTextSink],
        created_paths: list[Path],
    ) -> str | None:
        """回滚输出初始化期间尚未作为诊断保留的精确文件。

        参数：
            writers: 已创建 writer。
            created_paths: 与 writer 一一对应、本次精确创建的路径。

        返回：
            全部清理成功为 ``None``；否则为首个脱敏错误。

        异常、约束与副作用：
            不向外抛出。先关闭 writer，再仅删除列表中的精确文件；不删目录、不使
            用 glob，也不触碰发生碰撞的既有文件。
        """
        first_error = cls._close_writers(writers)
        for path in reversed(created_paths):
            try:
                path.unlink(missing_ok=True)
            except BaseException as exc:
                if first_error is None:
                    first_error = _safe_error_text(exc)
        return first_error
