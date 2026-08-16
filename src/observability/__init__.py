"""可观测性领域的公开上下文、日志、失败模型与脱敏 API。

本包公开稳定失败模型、日志上下文、确定性日志路径、规范日志事件、显式 handler
装配和凭据脱敏函数。导入本包不会访问文件系统、读取环境变量、配置全局 logging
或产生其他外部副作用；文件只会在调用具体 handler 或装配函数时创建。
"""

from observability.context import LogContext, LogPaths, build_log_paths
from observability.failures import (
    BuildFailure,
    ErrorCode,
    FailureCause,
    build_failure_from_exception,
    failure_cause_from_exception,
)
from observability.handlers import (
    ConsoleHandler,
    ExclusiveTextFileHandler,
    ExternalStreamWriter,
    open_run_handlers,
)
from observability.logger import (
    CompositeLogger,
    LogEvent,
    LogHandler,
    Logger,
    LogLevel,
    format_log_event,
)
from observability.redaction import (
    MIN_STREAMING_PENDING_CHARS,
    StreamingRedactor,
    redact_arguments,
    redact_environment,
    redact_text,
)

__all__ = [
    "BuildFailure",
    "CompositeLogger",
    "ConsoleHandler",
    "ErrorCode",
    "ExclusiveTextFileHandler",
    "ExternalStreamWriter",
    "FailureCause",
    "LogContext",
    "LogEvent",
    "LogHandler",
    "LogLevel",
    "LogPaths",
    "Logger",
    "MIN_STREAMING_PENDING_CHARS",
    "StreamingRedactor",
    "build_failure_from_exception",
    "build_log_paths",
    "failure_cause_from_exception",
    "format_log_event",
    "open_run_handlers",
    "redact_arguments",
    "redact_environment",
    "redact_text",
]
