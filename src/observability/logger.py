"""规范日志事件、确定性单行格式和最小日志协议。

本模块定义日志级别、不可变日志事件以及 handler/logger 的结构化协议。事件构造
只执行内存校验、字段脱敏和稳定排序；文本格式化会先分别脱敏可变文本，再转义
所有可破坏单行结构的字符。模块不打开文件、不配置标准库全局 logging，也不产
生任何外部副作用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from threading import RLock
from typing import Protocol, cast, runtime_checkable

from observability.context import LogContext
from observability.failures import ErrorCode
from observability.redaction import redact_text


class LogLevel(IntEnum):
    """统一日志接口支持的四个严重级别。

    职责：
        提供可直接比较的稳定整数级别，并把配置中的严格大写文本转换为枚举。

    参数与返回：
        枚举成员使用固定整数值；``parse`` 接受一个级别名称并返回对应成员。

    异常、约束与副作用：
        只接受 ``DEBUG``、``INFO``、``WARNING`` 和 ``ERROR`` 四个字符串；其他
        类型、大小写或内容抛出 ``ValueError``。枚举和解析均无外部副作用。
    """

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40

    @classmethod
    def parse(cls, value: str) -> LogLevel:
        """把严格大写级别名称解析为 ``LogLevel``。

        参数：
            value: 配置中的日志级别名称。

        返回：
            与名称对应的四级 ``LogLevel`` 成员。

        异常、约束与副作用：
            非字符串或不是四个精确名称之一时抛出 ``ValueError``。函数不修剪、
            不改变大小写，也不读取配置或全局日志状态。
        """
        value_object = cast(object, value)
        if not isinstance(value_object, str):
            raise ValueError("日志级别必须是 str")
        try:
            return cls[value_object]
        except KeyError as exc:
            raise ValueError(f"不支持的日志级别: {value_object!r}") from exc


def _validate_aware_timestamp(timestamp: datetime) -> None:
    """校验日志时间是含有效时区的 ``datetime``。

    参数：
        timestamp: 待校验时间。

    返回：
        校验成功时返回 ``None``。

    异常、约束与副作用：
        类型非法、没有时区或时区无法计算偏移时抛出 ``ValueError``。函数不读取
        当前时间，不转换时区且无外部副作用。
    """
    timestamp_object = cast(object, timestamp)
    if not isinstance(timestamp_object, datetime):
        raise ValueError("timestamp 必须是 datetime")
    try:
        utc_offset = timestamp_object.utcoffset()
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp 必须包含有效时区") from exc
    if timestamp_object.tzinfo is None or utc_offset is None:
        raise ValueError("timestamp 必须是 timezone-aware datetime")


def _normalize_fields(fields: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    """校验、脱敏并按键 UTF-8 字节序规范化字段。

    参数：
        fields: 调用方提供的字符串键值二元元组。

    返回：
        值经过 ``redact_text`` 且按键 UTF-8 字节序排序的新元组。

    异常、约束与副作用：
        容器或二元组结构非法、键为空、成员不是字符串或键重复时抛出
        ``ValueError``。函数不修改输入且无 I/O。
    """
    fields_object = cast(object, fields)
    if not isinstance(fields_object, tuple):
        raise ValueError("fields 必须是字符串键值元组")

    normalized: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for entry_object in cast(tuple[object, ...], fields_object):
        if not isinstance(entry_object, tuple):
            raise ValueError("fields 的元素必须是二元元组")
        entry = cast(tuple[object, ...], entry_object)
        if len(entry) != 2:
            raise ValueError("fields 的元素必须是二元元组")
        key_object, value_object = entry
        if not isinstance(key_object, str) or not key_object:
            raise ValueError("fields 键必须是非空 str")
        if not isinstance(value_object, str):
            raise ValueError("fields 值必须是 str")
        if key_object in seen_keys:
            raise ValueError(f"fields 存在重复键: {key_object!r}")
        seen_keys.add(key_object)
        normalized.append((key_object, redact_text(value_object)))

    normalized.sort(key=lambda item: item[0].encode("utf-8"))
    return tuple(normalized)


def _normalize_diagnostic_paths(diagnostic_paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """校验诊断路径并按文本的 UTF-8 字节序稳定排序。

    参数：
        diagnostic_paths: 需要随错误事件输出的诊断文件路径元组。

    返回：
        保留重复项、按 ``str(path)`` UTF-8 字节序排序的新元组。

    异常、约束与副作用：
        容器不是元组或成员不是 ``Path`` 时抛出 ``ValueError``。函数不解析、不
        访问路径，也不修改输入。
    """
    paths_object = cast(object, diagnostic_paths)
    if not isinstance(paths_object, tuple):
        raise ValueError("diagnostic_paths 必须是 Path 元组")
    paths: list[Path] = []
    for path_object in cast(tuple[object, ...], paths_object):
        if not isinstance(path_object, Path):
            raise ValueError("diagnostic_paths 的元素必须是 Path")
        paths.append(path_object)
    paths.sort(key=lambda path: str(path).encode("utf-8"))
    return tuple(paths)


@dataclass(frozen=True, slots=True)
class LogEvent:
    """一条可确定性格式化的不可变日志事件。

    职责：
        绑定含时区时间、级别、运行上下文、消息、扩展字段、稳定错误码和诊断
        路径，并在构造时规范化无序输入。消息允许包含换行，格式化时会转义为
        单行文本。

    参数：
        timestamp: 含有效时区的事件时间。
        level: 四级 ``LogLevel``。
        context: 已校验的 ``LogContext``。
        message: 非空消息；控制字符由格式化器转义。
        fields: 非空键与字符串值组成的元组；键不得重复。
        error_code: ERROR 事件必需的稳定 ``ErrorCode``。
        diagnostic_paths: 无需访问即可记录的诊断路径元组。

    返回：
        无；构造成功后得到冻结、使用 slots 且输入顺序已规范化的值对象。

    异常、约束与副作用：
        字段类型或结构非法、时间无时区、消息为空、错误码与级别不匹配时抛出
        ``ValueError``。构造会对字段值脱敏，但不执行 I/O 或记录日志。
    """

    timestamp: datetime
    level: LogLevel
    context: LogContext
    message: str
    fields: tuple[tuple[str, str], ...] = ()
    error_code: ErrorCode | None = None
    diagnostic_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        """校验事件不变量并写入确定性字段与路径顺序。

        本方法没有参数和返回值；非法字段抛出 ``ValueError``。仅通过冻结数据类
        允许的初始化钩子替换规范化元组，不访问文件系统或全局日志状态。
        """
        _validate_aware_timestamp(self.timestamp)
        if not isinstance(cast(object, self.level), LogLevel):
            raise ValueError("level 必须是 LogLevel")
        if not isinstance(cast(object, self.context), LogContext):
            raise ValueError("context 必须是 LogContext")
        message_object = cast(object, self.message)
        if not isinstance(message_object, str) or not message_object:
            raise ValueError("message 必须是非空 str")

        error_code_object = cast(object, self.error_code)
        if self.level is LogLevel.ERROR:
            if not isinstance(error_code_object, ErrorCode):
                raise ValueError("ERROR 事件必须提供 ErrorCode")
        elif error_code_object is not None:
            raise ValueError("非 ERROR 事件不得提供 error_code")

        object.__setattr__(self, "fields", _normalize_fields(self.fields))
        object.__setattr__(
            self,
            "diagnostic_paths",
            _normalize_diagnostic_paths(self.diagnostic_paths),
        )


def _escape_log_text(value: str) -> str:
    """把任意字符串转义为日志行中的安全单行片段。

    参数：
        value: 已确认类型为字符串的字段值、字段名或消息。

    返回：
        依次转义反斜杠、双引号、回车、换行和制表符后的字符串。

    异常、约束与副作用：
        本内部函数要求调用方提供字符串；不执行脱敏、I/O 或原位修改。
    """
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def _quoted(value: str) -> str:
    """为已经确认的字符串添加日志双引号和必要转义。

    参数：
        value: 待编码的单个日志值。

    返回：
        双引号包围且内部已转义的单行表示。

    异常、约束与副作用：
        调用方负责类型校验；函数只分配字符串，无 I/O 和全局副作用。
    """
    return f'"{_escape_log_text(value)}"'


def format_log_event(event: LogEvent) -> str:
    """把日志事件格式化为确定、脱敏且不带末尾换行的单行文本。

    参数：
        event: 已校验并规范化的 ``LogEvent``。

    返回：
        时间精确到毫秒，随后依次包含级别、build_id、task、run_id、可选 step、
        可选 error_code、稳定字段、稳定诊断路径和 message 的单行文本。全部值使用
        双引号；多个诊断路径用重复的 ``diagnostic_path`` 项表达。

    异常、约束与副作用：
        参数不是 ``LogEvent`` 时抛出 ``ValueError``。格式化不读取当前时间；返回
        消息与诊断路径在引用前分别执行 ``redact_text``，避免秘密替换吞掉规范
        字段边界；函数不写流或配置全局 logging。
    """
    event_object = cast(object, event)
    if not isinstance(event_object, LogEvent):
        raise ValueError("event 必须是 LogEvent")

    timestamp = (
        event_object.timestamp.strftime("%Y-%m-%d %H:%M:%S.")
        + f"{event_object.timestamp.microsecond // 1000:03d}"
    )
    context = event_object.context
    parts = [
        timestamp,
        f"[{event_object.level.name}]",
        f"build_id={_quoted(context.build_id)}",
        f"task={_quoted(context.task_name)}",
        f"run_id={_quoted(context.run_id)}",
    ]
    if context.step_name:
        parts.append(f"step={_quoted(context.step_name)}")
    if event_object.error_code is not None:
        parts.append(f"error_code={_quoted(event_object.error_code.value)}")
    parts.extend(f"{_escape_log_text(key)}={_quoted(value)}" for key, value in event_object.fields)
    parts.extend(
        f"diagnostic_path={_quoted(redact_text(str(path)))}"
        for path in event_object.diagnostic_paths
    )
    parts.append(f"message={_quoted(redact_text(event_object.message))}")
    return " ".join(parts)


@runtime_checkable
class LogHandler(Protocol):
    """接收规范日志事件并管理自身生命周期的最小协议。

    实现必须提供 ``emit`` 和幂等或明确约束的 ``close``。协议本身不规定资源
    所有权；具体 handler 的文档负责说明是否关闭底层流以及失败传播方式。
    """

    def emit(self, event: LogEvent) -> None:
        """处理一条日志事件。

        参数：
            event: 待处理的规范日志事件。

        返回：
            成功时返回 ``None``。

        异常、约束与副作用：
            具体实现可以因关闭状态或 I/O 失败抛出异常；协议不允许调用方假定
            失败会被吞掉。副作用由实现声明。
        """
        ...

    def close(self) -> None:
        """结束 handler 生命周期并释放其拥有的资源。

        没有参数；成功时返回 ``None``。具体异常、幂等性和外部副作用由实现声明。
        """
        ...


@runtime_checkable
class Logger(Protocol):
    """供业务层注入的最小日志端口。

    端口只暴露事件发送和生命周期关闭，不绑定标准库 logging、全局 handler 或
    具体文本流。实现异常必须直接传播给调用方。
    """

    def emit(self, event: LogEvent) -> None:
        """按实现策略发送一条日志事件。

        参数：
            event: 待发送的规范日志事件。

        返回：
            全部目标处理成功时返回 ``None``。

        异常、约束与副作用：
            任一目标失败时实现应立即抛出原异常；具体 I/O 副作用由实现声明。
        """
        ...

    def close(self) -> None:
        """结束 logger 生命周期并释放其拥有的 handler。

        没有参数；成功时返回 ``None``。具体异常和副作用由实现声明。
        """
        ...


class CompositeLogger:
    """按声明顺序发送、按逆序关闭的一组日志 handler。

    参数：
        handlers: 不可变 handler 元组；发送顺序与元组一致。

    返回：
        无；构造成功后得到拥有独立关闭状态的 logger。

    异常、约束与副作用：
        容器不是元组或成员不符合 ``LogHandler`` 时抛出 ``ValueError``。``emit``
        与 ``close`` 在实例锁内串行执行；handler 异常立即原样传播，不继续调用
        后续 handler。成功关闭后重复关闭无操作，关闭后发送抛出 ``RuntimeError``。
    """

    __slots__ = ("_closed", "_handlers", "_lock")

    def __init__(self, handlers: tuple[LogHandler, ...]) -> None:
        """校验并保存 handler 元组，初始化可并发使用的生命周期状态。

        参数：
            handlers: 按发送顺序排列的 handler 元组，可以为空。

        返回：
            无。

        异常、约束与副作用：
            参数结构非法时抛出 ``ValueError``。构造仅创建线程锁，不调用 handler
            或产生 I/O。
        """
        handlers_object = cast(object, handlers)
        if not isinstance(handlers_object, tuple):
            raise ValueError("handlers 必须是 LogHandler 元组")
        validated_handlers: list[LogHandler] = []
        for handler_object in cast(tuple[object, ...], handlers_object):
            if not isinstance(handler_object, LogHandler):
                raise ValueError("handlers 的元素必须实现 LogHandler")
            validated_handlers.append(handler_object)
        self._handlers = tuple(validated_handlers)
        self._closed = False
        self._lock = RLock()

    @property
    def handlers(self) -> tuple[LogHandler, ...]:
        """返回按发送顺序保存的不可变 handler 元组。

        没有参数；返回构造时校验后的元组。不允许通过属性替换内部元组，读取不
        触发 handler、不执行 I/O，也不改变关闭状态。
        """
        return self._handlers

    def emit(self, event: LogEvent) -> None:
        """按顺序向每个 handler 发送同一事件。

        参数：
            event: 待发送的 ``LogEvent``；具体校验由 handler/格式化器执行。

        返回：
            全部 handler 成功后返回 ``None``。

        异常、约束与副作用：
            logger 已关闭时抛出 ``RuntimeError``。handler 失败时立即传播原异常，
            本次发送不调用其后的 handler；已调用 handler 的副作用不会回滚。
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("CompositeLogger 已关闭")
            for handler in self._handlers:
                handler.emit(event)

    def close(self) -> None:
        """按 handler 逆序关闭 logger，成功后进入永久关闭状态。

        没有参数；首次成功和后续重复调用均返回 ``None``。某个 handler 关闭失败
        时立即传播原异常且不继续关闭更早的 handler；logger 保持未完全关闭，
        调用方可重试。方法可能关闭部分底层资源。
        """
        with self._lock:
            if self._closed:
                return
            for handler in reversed(self._handlers):
                handler.close()
            self._closed = True
