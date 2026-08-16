"""验证日志事件、确定性单行格式与组合 logger 生命周期。

本模块仅通过公开协议观察排序、脱敏、级别约束、发送次序和关闭语义。测试使用
轻量内存 fake 记录真实协议调用，不检查私有实现细节。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from observability import (
    CompositeLogger,
    ErrorCode,
    LogContext,
    LogEvent,
    LogLevel,
    format_log_event,
)


class _RecordingHandler:
    """实现真实 LogHandler 协议并记录可观察调用顺序的内存 fake。"""

    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        failing_close_count: int = 0,
    ) -> None:
        """保存名称、独立调用记录和剩余关闭失败次数。

        ``name`` 标识实例，``calls`` 接收调用结果，``failing_close_count`` 控制
        前几次关闭抛出 ``OSError``。构造无 I/O。
        """
        self.name = name
        self.calls = calls
        self.failing_close_count = failing_close_count

    def emit(self, event: LogEvent) -> None:
        """记录一次事件发送。

        ``event`` 是真实日志事件；方法无返回值，只追加可观察记录。
        """
        self.calls.append(f"emit:{self.name}:{event.message}")

    def close(self) -> None:
        """记录一次关闭并按配置模拟真实资源关闭失败。

        无参数和返回值；剩余失败次数为正时递减并抛出 ``OSError``。
        """
        self.calls.append(f"close:{self.name}")
        if self.failing_close_count > 0:
            self.failing_close_count -= 1
            raise OSError(f"close failed: {self.name}")


def _event(
    *,
    level: LogLevel = LogLevel.INFO,
    message: str = "构建完成",
    error_code: ErrorCode | None = None,
) -> LogEvent:
    """创建具有固定时间和上下文的日志事件。

    ``level``、``message`` 和 ``error_code`` 传入目标场景；返回真实 ``LogEvent``。
    非法组合由生产构造器抛出，辅助函数无 I/O。
    """
    return LogEvent(
        timestamp=datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc),
        level=level,
        context=LogContext("build-1", "scene", "run-1", "collect"),
        message=message,
        error_code=error_code,
    )


def test_log_level_parse_is_strict_and_stable() -> None:
    """验证四级名称精确解析且拒绝大小写或空白宽松输入。

    无参数和返回值；解析只处理内存枚举，不配置全局日志。
    """
    assert [LogLevel.parse(name) for name in ("DEBUG", "INFO", "WARNING", "ERROR")] == list(
        LogLevel
    )
    for invalid in ("debug", " INFO", "CRITICAL"):
        with pytest.raises(ValueError):
            LogLevel.parse(invalid)


def test_log_event_is_frozen_and_normalizes_fields_and_diagnostic_paths() -> None:
    """验证事件不可变，字段和值脱敏后按 UTF-8 排序，诊断路径稳定排序。

    无参数和返回值；构造只处理内存数据且不访问诊断路径实体。
    """
    event = LogEvent(
        timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
        level=LogLevel.INFO,
        context=LogContext("build", "task", "run"),
        message="完成",
        fields=(("中", "visible"), ("a", "password=field-secret")),
        diagnostic_paths=(Path("z.log"), Path("a.log")),
    )

    assert event.fields == (("a", "password=<redacted>"), ("中", "visible"))
    assert event.diagnostic_paths == (Path("a.log"), Path("z.log"))
    assert not hasattr(event, "__dict__")
    with pytest.raises(FrozenInstanceError):
        event.message = "changed"  # type: ignore[misc]


def test_log_event_requires_error_code_only_for_error_level() -> None:
    """验证 ERROR 必须携带稳定错误码且其他级别禁止携带。

    无参数和返回值；非法组合抛出 ``ValueError``，合法错误事件保留枚举值。
    """
    with pytest.raises(ValueError, match="ERROR.*ErrorCode"):
        _event(level=LogLevel.ERROR)
    with pytest.raises(ValueError, match="非 ERROR"):
        _event(error_code=ErrorCode.INTERNAL_ERROR)

    event = _event(level=LogLevel.ERROR, error_code=ErrorCode.TASK_OUTPUT_MISSING)
    assert event.error_code is ErrorCode.TASK_OUTPUT_MISSING


def test_format_log_event_has_exact_stable_single_line_order_and_escaping() -> None:
    """验证规范格式的毫秒时间、字段顺序、引号转义和无末尾换行。

    无参数和返回值；输入包含控制字符和反斜杠，输出必须保持单行且顺序固定。
    """
    event = LogEvent(
        timestamp=datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc),
        level=LogLevel.INFO,
        context=LogContext("build-1", "scene", "run-1", "collect"),
        message='line1\n"line2"\t\\done',
        fields=(("zeta", "last"), ("alpha", "first")),
        diagnostic_paths=(Path("z.log"), Path("a.log")),
    )

    formatted = format_log_event(event)

    assert formatted == (
        '2026-01-02 03:04:05.678 [INFO] build_id="build-1" task="scene" '
        'run_id="run-1" step="collect" alpha="first" zeta="last" '
        'diagnostic_path="a.log" diagnostic_path="z.log" '
        'message="line1\\n\\"line2\\"\\t\\\\done"'
    )
    assert "\n" not in formatted


def test_format_log_event_redacts_message_and_path_without_breaking_quotes() -> None:
    """验证消息和诊断路径的秘密被隐藏后仍保持完整规范键值结构。

    无参数和返回值；输出不得泄漏秘密，必须以闭合 message 引号结束且路径项不能
    吞掉后续字段。
    """
    event = LogEvent(
        timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
        level=LogLevel.ERROR,
        context=LogContext("build", "task", "run"),
        message="password=message-secret",
        error_code=ErrorCode.INTERNAL_ERROR,
        diagnostic_paths=(Path("diagnostics/token=path-secret.log"),),
    )

    formatted = format_log_event(event)

    assert "message-secret" not in formatted
    assert "path-secret" not in formatted
    expected_path = str(Path("diagnostics/token=<redacted>")).replace("\\", "\\\\")
    assert f'diagnostic_path="{expected_path}"' in formatted
    assert formatted.endswith('message="password=<redacted>"')


def test_composite_logger_emits_in_order_and_closes_in_reverse_order() -> None:
    """验证组合 logger 发送顺序与声明一致，关闭顺序严格相反且幂等。

    无参数和返回值；内存 fake 记录真实协议调用，不模拟生产内部实现。
    """
    calls: list[str] = []
    first = _RecordingHandler("first", calls)
    second = _RecordingHandler("second", calls)
    logger = CompositeLogger((first, second))

    logger.emit(_event(message="one"))
    logger.close()
    logger.close()

    assert calls == (["emit:first:one", "emit:second:one", "close:second", "close:first"])
    with pytest.raises(RuntimeError, match="关闭"):
        logger.emit(_event())


def test_composite_logger_close_failure_stops_and_allows_retry() -> None:
    """验证逆序关闭失败立即传播，未完全关闭状态允许幂等 handler 重试。

    无参数和返回值；后声明 handler 首次关闭失败，较早 handler 当次不应关闭；
    第二次关闭成功完成生命周期。
    """
    calls: list[str] = []
    first = _RecordingHandler("first", calls)
    second = _RecordingHandler("second", calls, failing_close_count=1)
    logger = CompositeLogger((first, second))

    with pytest.raises(OSError, match="second"):
        logger.close()
    assert calls == ["close:second"]

    logger.close()
    assert calls == ["close:second", "close:second", "close:first"]


def test_log_event_rejects_invalid_timestamp_level_context_and_message() -> None:
    """验证事件核心字段在运行时严格校验类型、时区和非空消息。

    无参数和返回值；每个非法构造均应抛出 ``ValueError``，不读取当前时间或文件。
    """
    context = LogContext("build", "task", "run")
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="timestamp"):
        LogEvent("bad", LogLevel.INFO, context, "message")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        LogEvent(datetime(2026, 1, 1), LogLevel.INFO, context, "message")
    with pytest.raises(ValueError, match="level"):
        LogEvent(aware, 20, context, "message")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="context"):
        LogEvent(aware, LogLevel.INFO, object(), "message")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="message"):
        LogEvent(aware, LogLevel.INFO, context, "")


def test_log_event_rejects_invalid_field_and_diagnostic_collections() -> None:
    """验证扩展字段与诊断路径必须使用合法不可变容器和成员。

    无参数和返回值；列表容器、非法二元组、重复键和非 Path 成员应抛出
    ``ValueError``，且不访问诊断路径。
    """
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = LogContext("build", "task", "run")

    with pytest.raises(ValueError, match="fields"):
        LogEvent(timestamp, LogLevel.INFO, context, "message", fields=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="fields.*二元元组"):
        LogEvent(timestamp, LogLevel.INFO, context, "message", fields=(("key",),))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="重复键"):
        LogEvent(
            timestamp,
            LogLevel.INFO,
            context,
            "message",
            fields=(("key", "one"), ("key", "two")),
        )
    with pytest.raises(ValueError, match="diagnostic_paths"):
        LogEvent(
            timestamp,
            LogLevel.INFO,
            context,
            "message",
            diagnostic_paths=[Path("a.log")],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="diagnostic_paths.*元素"):
        LogEvent(
            timestamp,
            LogLevel.INFO,
            context,
            "message",
            diagnostic_paths=("a.log",),  # type: ignore[arg-type]
        )


def test_logger_public_boundaries_reject_invalid_parse_format_and_handlers() -> None:
    """验证级别解析、格式化和组合构造拒绝错误公开输入。

    无参数和返回值；错误类型应抛出 ``ValueError``。合法组合的 handlers 属性
    返回原不可变元组，不触发发送或关闭。
    """
    with pytest.raises(ValueError, match="级别"):
        LogLevel.parse(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="event"):
        format_log_event(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="handlers"):
        CompositeLogger([])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="handlers.*元素"):
        CompositeLogger((object(),))  # type: ignore[arg-type]

    calls: list[str] = []
    handler = _RecordingHandler("one", calls)
    logger = CompositeLogger((handler,))
    assert logger.handlers == (handler,)
