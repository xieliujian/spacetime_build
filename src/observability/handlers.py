"""文本日志 handler、外部流写入器和运行级资源装配。

本模块只管理调用方显式传入的控制台流与单次运行独占日志文件，不配置标准库
logging 或 root handler。文件全部排他创建；写入与关闭由实例锁保护；批量装配失
败时仅回滚本次调用刚创建的精确文件，绝不删除日志目录或覆盖历史日志。
"""

from __future__ import annotations

import stat
from pathlib import Path
from threading import RLock
from typing import BinaryIO, TextIO, cast

from observability.context import LogPaths
from observability.logger import (
    CompositeLogger,
    LogEvent,
    LogHandler,
    LogLevel,
    format_log_event,
)
from observability.redaction import StreamingRedactor

_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _require_path(value: Path, *, field_name: str) -> Path:
    """校验公开文件路径参数确实是 ``Path``。

    参数：
        value: 待校验路径。
        field_name: 用于异常消息的字段名。

    返回：
        未解析、未转换的原始 ``Path``。

    异常、约束与副作用：
        类型非法时抛出 ``ValueError``。函数不访问文件系统或解析链接。
    """
    value_object = cast(object, value)
    if not isinstance(value_object, Path):
        raise ValueError(f"{field_name} 必须是 Path")
    return value_object


def _require_level(value: LogLevel, *, field_name: str) -> LogLevel:
    """校验级别参数是 ``LogLevel``，避免普通整数绕过四级约束。

    参数：
        value: 待校验级别。
        field_name: 用于异常消息的字段名。

    返回：
        原始 ``LogLevel`` 成员。

    异常、约束与副作用：
        参数不是 ``LogLevel`` 时抛出 ``ValueError``。函数无 I/O 和状态修改。
    """
    value_object = cast(object, value)
    if not isinstance(value_object, LogLevel):
        raise ValueError(f"{field_name} 必须是 LogLevel")
    return value_object


def _is_link_or_reparse(path: Path) -> bool:
    """判断现有路径实体是否为符号链接、junction 或其他 reparse point。

    参数：
        path: 待检查的单个路径实体。

    返回：
        符号链接或具有 Windows reparse point 属性时返回 ``True``，普通实体或路径
        不存在时返回 ``False``。

    异常、约束与副作用：
        除路径不存在外的 ``lstat`` 错误直接传播。函数只读取文件元数据，不跟随
        链接、不创建或修改文件。
    """
    if path.is_symlink():
        return True
    try:
        path_status = path.lstat()
    except FileNotFoundError:
        return False
    file_attributes = cast(int, getattr(path_status, "st_file_attributes", 0))
    return bool(file_attributes & _REPARSE_POINT_ATTRIBUTE)


def _validate_regular_directory(path: Path, *, field_name: str) -> None:
    """确认路径存在且是非链接、非 reparse point 的普通目录。

    参数：
        path: 待检查目录。
        field_name: 用于异常消息的字段名。

    返回：
        校验成功时返回 ``None``。

    异常、约束与副作用：
        目录不存在、不是目录或属于链接/reparse point 时抛出 ``ValueError``；其
        他元数据错误直接传播。函数只读文件系统元数据。
    """
    try:
        path_status = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{field_name} 必须已存在") from exc
    if _is_link_or_reparse(path) or not stat.S_ISDIR(path_status.st_mode):
        raise ValueError(f"{field_name} 必须是普通目录")


def _validate_directory_chain(directory: Path) -> None:
    """逐级拒绝日志目录路径中的链接和 Windows reparse point。

    参数：
        directory: 已由调用方创建完成的日志目录。

    返回：
        每一级都存在且是普通目录时返回 ``None``。

    异常、约束与副作用：
        任一级不存在、不是目录、是符号链接、junction 或其他 reparse point 时抛
        出 ``ValueError``。``absolute`` 仅做词法绝对化，不调用 ``resolve``；函数
        只读取元数据，不修改目录。
    """
    absolute_directory = directory.absolute()
    chain = (*reversed(absolute_directory.parents), absolute_directory)
    for component in chain:
        _validate_regular_directory(component, field_name=f"日志目录层级 {component}")


def _validate_text_stream(stream: TextIO) -> TextIO:
    """校验控制台流至少提供可调用的 ``write`` 和 ``flush``。

    参数：
        stream: 调用方拥有的文本流。

    返回：
        原始流对象。

    异常、约束与副作用：
        缺少可调用 ``write`` 或 ``flush`` 时抛出 ``ValueError``。函数不会试写、
        flush 或关闭流。
    """
    stream_object = cast(object, stream)
    if not callable(getattr(stream_object, "write", None)):
        raise ValueError("stream 必须提供 write")
    if not callable(getattr(stream_object, "flush", None)):
        raise ValueError("stream 必须提供 flush")
    return stream


class ConsoleHandler:
    """向调用方拥有的文本流输出规范日志行。

    参数：
        stream: 提供 ``write`` 和 ``flush`` 的文本流；handler 不拥有该流。
        min_level: 允许输出的最低 ``LogLevel``。

    返回：
        无；构造成功后得到具有独立关闭状态和写锁的 handler。

    异常、约束与副作用：
        参数非法时抛出 ``ValueError``。达到阈值的事件会执行一次整行写入并立即
        flush；I/O 失败原样传播。``close`` 幂等且绝不关闭或 flush 底层流，关闭
        后 ``emit`` 抛出 ``RuntimeError``。
    """

    __slots__ = ("_closed", "_lock", "min_level", "stream")

    def __init__(self, stream: TextIO, min_level: LogLevel) -> None:
        """保存非自有流、最低级别和实例生命周期锁。

        参数：
            stream: 调用方拥有的控制台文本流。
            min_level: 最低输出级别。

        返回：
            无。

        异常、约束与副作用：
            流接口或级别非法时抛出 ``ValueError``。构造不写、不 flush、不关闭流。
        """
        self.stream = _validate_text_stream(stream)
        self.min_level = _require_level(min_level, field_name="min_level")
        self._closed = False
        self._lock = RLock()

    def emit(self, event: LogEvent) -> None:
        """按最低级别过滤并写入一条规范日志行。

        参数：
            event: 待格式化和输出的 ``LogEvent``。

        返回：
            事件低于阈值或写入并 flush 成功时返回 ``None``。

        异常、约束与副作用：
            handler 已关闭时抛出 ``RuntimeError``；事件非法或流写入失败时直接传
            播异常。达到阈值时在锁内写入规范行加 ``\n`` 并 flush。
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("ConsoleHandler 已关闭")
            if not isinstance(cast(object, event), LogEvent):
                raise ValueError("event 必须是 LogEvent")
            if event.level < self.min_level:
                return
            self.stream.write(f"{format_log_event(event)}\n")
            self.stream.flush()

    def close(self) -> None:
        """幂等关闭 handler 自身但保留调用方文本流。

        没有参数；首次及重复调用均返回 ``None``。方法只改变实例关闭状态，不调用
        底层流的 ``flush`` 或 ``close``，因此流仍可由调用方继续使用。
        """
        with self._lock:
            self._closed = True


class ExclusiveTextFileHandler:
    """排他拥有一个 UTF-8 主日志文件的线程安全 handler。

    参数：
        path: 待排他创建的日志文件路径；父目录必须已存在且是普通目录。
        min_level: 最低输出级别，默认 ``DEBUG``。

    返回：
        无；构造成功后立即拥有以 ``x`` 模式创建的文本流。

    异常、约束与副作用：
        参数或父目录非法时抛出 ``ValueError``；目标碰撞由排他创建抛出
        ``FileExistsError``，其他打开失败原样传播。达到阈值的事件在锁内整行写入
        并 flush。关闭幂等，关闭后发送抛出 ``RuntimeError``，从不覆盖旧文件。
    """

    __slots__ = ("_closed", "_lock", "_stream", "min_level", "path")

    def __init__(self, path: Path, min_level: LogLevel = LogLevel.DEBUG) -> None:
        """校验父目录并排他创建 UTF-8、LF 文本文件。

        参数：
            path: 要创建的精确日志文件。
            min_level: 最低输出级别。

        返回：
            无。

        异常、约束与副作用：
            路径、级别或父目录非法时抛出 ``ValueError``；文件已存在时抛出
            ``FileExistsError``。成功时创建一个空文件并保持打开，不写任何内容。
        """
        self.path = _require_path(path, field_name="path")
        self.min_level = _require_level(min_level, field_name="min_level")
        _validate_regular_directory(self.path.parent, field_name="path.parent")
        self._stream: TextIO = self.path.open(mode="x", encoding="utf-8", newline="\n")
        self._closed = False
        self._lock = RLock()

    def emit(self, event: LogEvent) -> None:
        """按最低级别过滤并同步落盘一条规范日志行。

        参数：
            event: 待格式化和写入的 ``LogEvent``。

        返回：
            事件低于阈值或写入与 flush 成功时返回 ``None``。

        异常、约束与副作用：
            handler 已关闭时抛出 ``RuntimeError``；事件或文件 I/O 错误直接传播。
            达到阈值时锁覆盖整行 ``write`` 与 ``flush``，防止线程间行内容交错。
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("ExclusiveTextFileHandler 已关闭")
            if not isinstance(cast(object, event), LogEvent):
                raise ValueError("event 必须是 LogEvent")
            if event.level < self.min_level:
                return
            self._stream.write(f"{format_log_event(event)}\n")
            self._stream.flush()

    def close(self) -> None:
        """幂等关闭 handler 拥有的文本文件。

        没有参数；首次关闭底层流，重复调用无操作并返回 ``None``。底层 close 错误
        原样传播；只有成功后才标记关闭，便于调用方在失败后重试。
        """
        with self._lock:
            if self._closed:
                return
            self._stream.close()
            self._closed = True


class ExternalStreamWriter:
    """排他保存已经过文本脱敏的外部程序输出流。

    参数：
        path: 待排他创建的外部 stdout、stderr 或 Unity 日志路径。

    返回：
        无；构造成功后拥有二进制文件和从零开始的 ``byte_count``。

    异常、约束与副作用：
        父目录非法时抛出 ``ValueError``，文件碰撞或 I/O 失败原样传播。调用方只能
        通过 ``write_text`` 提交文本；实例使用 ``StreamingRedactor`` 暂存未结束
        逻辑行，只把完整脱敏文本编码为 UTF-8 并在锁内写入、flush。它不接受 raw
        bytes，也不尝试解码字节。若业务必须保存未脱敏的完整原始诊断流，必须使用
        另一个权限受控且生命周期明确的专用组件，不能绕过本 writer 的脱敏边界。
        关闭会 finalize 并写入安全尾部，重复关闭返回空字符串。
    """

    __slots__ = ("_byte_count", "_closed", "_lock", "_redactor", "_stream", "path")

    def __init__(self, path: Path) -> None:
        """校验父目录并排他创建二进制外部日志文件。

        参数：
            path: 要创建的精确日志路径。

        返回：
            无。

        异常、约束与副作用：
            路径或父目录非法时抛出 ``ValueError``；文件存在时抛出
            ``FileExistsError``。成功时创建空二进制文件，不写数据。
        """
        self.path = _require_path(path, field_name="path")
        _validate_regular_directory(self.path.parent, field_name="path.parent")
        self._stream: BinaryIO = self.path.open(mode="xb")
        self._redactor = StreamingRedactor()
        self._byte_count = 0
        self._closed = False
        self._lock = RLock()

    @property
    def byte_count(self) -> int:
        """返回已成功交给底层文件流的脱敏 UTF-8 字节数。

        没有参数；返回从构造开始累计的非负整数。读取在实例锁内完成，不 flush、
        不访问文件元数据，也不改变 writer 状态。
        """
        with self._lock:
            return self._byte_count

    def write_text(self, text: str) -> str:
        """流式脱敏并线程安全地追加本次完成的逻辑行。

        参数：
            text: 已由进程层解码的 stdout、stderr 或 Unity 文本片段。

        返回：
            本次实际写入文件的脱敏文本；chunk 尚未形成完整安全逻辑行时返回空
            字符串。返回文本的 UTF-8 长度等于本次 ``byte_count`` 增量。

        异常、约束与副作用：
            ``text`` 非字符串时抛出 ``ValueError``；writer 已关闭时抛出
            ``RuntimeError``；脱敏或 I/O 错误直接传播。流式状态与文件写入均在
            实例锁内串行；``byte_count`` 只累计底层实际报告写入的字节数。
        """
        text_object = cast(object, text)
        if not isinstance(text_object, str):
            raise ValueError("text 必须是 str")
        with self._lock:
            if self._closed:
                raise RuntimeError("ExternalStreamWriter 已关闭")
            redacted = self._redactor.feed(text_object)
            encoded = redacted.encode("utf-8")
            if not encoded:
                return redacted
            written = self._stream.write(encoded)
            self._byte_count += written
            if written != len(encoded):
                raise OSError("外部日志文件发生短写入")
            self._stream.flush()
            return redacted

    def close(self) -> str:
        """finalize 脱敏尾部、写入并幂等关闭自有二进制文件。

        没有参数；首次调用返回本次 finalize 后实际写入的脱敏尾部，重复调用返回
        空字符串。底层写入、flush 或 close 错误原样传播；只有成功后才标记关闭，
        已累计 ``byte_count`` 在关闭后仍可读取。
        """
        with self._lock:
            if self._closed:
                return ""
            redacted_tail = self._redactor.finalize()
            encoded = redacted_tail.encode("utf-8")
            if encoded:
                written = self._stream.write(encoded)
                self._byte_count += written
                if written != len(encoded):
                    raise OSError("外部日志文件发生短写入")
                self._stream.flush()
            self._stream.close()
            self._closed = True
            return redacted_tail


def _rollback_created_files(
    opened_handlers: list[LogHandler | ExternalStreamWriter],
    created_paths: list[Path],
) -> None:
    """关闭已打开资源并删除本次装配刚创建的精确文件。

    参数：
        opened_handlers: 按创建顺序记录的自有资源对象。
        created_paths: 与成功创建资源对应的精确文件路径。

    返回：
        全部关闭与删除成功时返回 ``None``。

    异常、约束与副作用：
        清理过程中仍尽量处理全部资源，最后抛出遇到的第一个异常。函数仅关闭传入
        对象并删除传入的精确文件，不删除目录、不使用 glob，也不触碰碰撞文件。
    """
    first_error: Exception | None = None
    for handler in reversed(opened_handlers):
        try:
            handler.close()
        except Exception as exc:  # 清理必须继续，首个异常在完成其余回滚后报告。
            if first_error is None:
                first_error = exc
    for path in reversed(created_paths):
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:  # 同上；精确路径列表保证不会误删历史文件。
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise RuntimeError("运行日志资源初始化失败后的回滚未完整完成") from first_error


def open_run_handlers(
    paths: LogPaths,
    console_stream: TextIO,
    console_level: LogLevel,
    file_level: LogLevel,
) -> tuple[
    CompositeLogger,
    ExternalStreamWriter,
    ExternalStreamWriter,
    ExternalStreamWriter,
]:
    """安全创建一次运行所需的主日志与三类外部日志资源。

    参数：
        paths: 已完成词法 containment 与确定命名校验的 ``LogPaths``。
        console_stream: 调用方拥有且不会由本函数关闭的控制台文本流。
        console_level: 控制台最低输出级别。
        file_level: 主日志文件最低输出级别。

    返回：
        ``(CompositeLogger, stdout_writer, stderr_writer, unity_writer)``。组合 logger
        按控制台、主文件顺序发送，关闭时逆序释放；三个 writer 分别独占对应路径。

    异常、约束与副作用：
        参数非法、目录层级含符号链接/junction/reparse point、目标碰撞或 I/O 失败
        时直接抛出异常。函数会创建 ``paths.directory`` 及缺失父目录，但不删除目
        录；main、stdout、stderr、unity 按顺序排他创建。任一步失败时关闭已打开
        资源并只删除本次调用成功创建的精确文件，不配置全局 logging。
    """
    paths_object = cast(object, paths)
    if not isinstance(paths_object, LogPaths):
        raise ValueError("paths 必须是 LogPaths")
    _validate_text_stream(console_stream)
    _require_level(console_level, field_name="console_level")
    _require_level(file_level, field_name="file_level")

    paths_object.directory.mkdir(parents=True, exist_ok=True)
    _validate_directory_chain(paths_object.directory)

    console_handler = ConsoleHandler(console_stream, console_level)
    opened_handlers: list[LogHandler | ExternalStreamWriter] = []
    created_paths: list[Path] = []
    try:
        main_handler = ExclusiveTextFileHandler(paths_object.main, file_level)
        opened_handlers.append(main_handler)
        created_paths.append(paths_object.main)

        stdout_writer = ExternalStreamWriter(paths_object.stdout)
        opened_handlers.append(stdout_writer)
        created_paths.append(paths_object.stdout)

        stderr_writer = ExternalStreamWriter(paths_object.stderr)
        opened_handlers.append(stderr_writer)
        created_paths.append(paths_object.stderr)

        unity_writer = ExternalStreamWriter(paths_object.unity)
        opened_handlers.append(unity_writer)
        created_paths.append(paths_object.unity)

        logger = CompositeLogger((console_handler, main_handler))
    except Exception:
        _rollback_created_files(opened_handlers, created_paths)
        raise

    return logger, stdout_writer, stderr_writer, unity_writer
