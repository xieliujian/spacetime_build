"""验证控制台、排他文件、外部流和运行级 handler 装配。

本模块主要使用 pytest 临时目录与真实文件对象验证级别、并发整行、排他碰撞、
关闭、脱敏字节计数、运行隔离和失败回滚。仅 reparse point 分支使用最小 OS seam。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pytest

import observability.handlers as handlers_module
from observability import (
    ConsoleHandler,
    ExclusiveTextFileHandler,
    ExternalStreamWriter,
    LogContext,
    LogEvent,
    LogLevel,
    LogPaths,
    build_log_paths,
    format_log_event,
    open_run_handlers,
    redact_text,
)


class _WriteOnlyStream:
    """仅实现 write 的测试文本流，用于验证 flush 接口边界。"""

    def write(self, text: str) -> int:
        """接受文本并报告字符数，不保存内容。

        ``text`` 是待写字符串；返回字符数。方法不执行 I/O。
        """
        return len(text)


def _event(index: int, *, level: LogLevel = LogLevel.INFO) -> LogEvent:
    """创建带稳定上下文和唯一消息的真实日志事件。

    ``index`` 进入消息，``level`` 指定级别；返回值不执行 I/O。
    """
    return LogEvent(
        timestamp=datetime(2026, 1, 2, 3, 4, 5, index * 1000, tzinfo=timezone.utc),
        level=level,
        context=LogContext("build", "task", "run"),
        message=f"event-{index}",
    )


def _paths(root: Path, run_id: str) -> LogPaths:
    """为 handler 测试创建确定性日志路径值。

    ``root`` 是日志根，``run_id`` 区分运行；返回公开 ``LogPaths`` 值且不创建文件。
    """
    return build_log_paths(
        root,
        LogContext("build", "task", run_id),
        datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    )


def test_console_handler_filters_levels_flushes_and_never_closes_caller_stream() -> None:
    """验证控制台阈值、成功写入 flush 及非自有流关闭语义。

    无参数和返回值；``StringIO`` 是真实内存文本流。handler 关闭后调用方仍可
    继续写流，而再次 emit 必须失败。
    """
    stream = StringIO()
    handler = ConsoleHandler(stream, LogLevel.WARNING)

    handler.emit(_event(1, level=LogLevel.INFO))
    handler.emit(_event(2, level=LogLevel.WARNING))
    assert stream.getvalue() == f"{format_log_event(_event(2, level=LogLevel.WARNING))}\n"

    handler.close()
    handler.close()
    assert stream.closed is False
    stream.write("caller-owned")
    with pytest.raises(RuntimeError, match="关闭"):
        handler.emit(_event(3, level=LogLevel.WARNING))


def test_exclusive_file_handler_rejects_collision_without_overwriting(tmp_path: Path) -> None:
    """验证主日志排他创建碰撞不会截断或覆盖既有内容。

    ``tmp_path`` 提供真实临时目录。既有文件内容在构造失败后必须原样保留。
    """
    path = tmp_path / "main.log"
    path.write_text("historical", encoding="utf-8")

    with pytest.raises(FileExistsError):
        ExclusiveTextFileHandler(path)

    assert path.read_text(encoding="utf-8") == "historical"


def test_exclusive_file_handler_serializes_concurrent_whole_lines_and_closes(
    tmp_path: Path,
) -> None:
    """验证并发 emit 每次形成完整独立行，关闭幂等且关闭后拒绝写入。

    ``tmp_path`` 提供真实文件。线程池不使用 sleep，完成条件由 future 同步保证。
    """
    path = tmp_path / "main.log"
    handler = ExclusiveTextFileHandler(path)
    events = tuple(_event(index) for index in range(50))

    with ThreadPoolExecutor(max_workers=8) as executor:
        tuple(executor.map(handler.emit, events))
    handler.close()
    handler.close()

    assert set(path.read_text(encoding="utf-8").splitlines()) == {
        format_log_event(event) for event in events
    }
    with pytest.raises(RuntimeError, match="关闭"):
        handler.emit(_event(99))


def test_external_stream_writer_redacts_and_counts_written_utf8_bytes(tmp_path: Path) -> None:
    """验证外部流先脱敏再写入，并以实际 UTF-8 字节累计数量。

    ``tmp_path`` 提供真实二进制文件。关闭后仍可读取计数，重复关闭幂等，继续写入
    必须失败。
    """
    path = tmp_path / "stdout.log"
    writer = ExternalStreamWriter(path)
    text = "中文 password=stream-secret\nnext"
    expected = redact_text(text).encode("utf-8")

    written = writer.write_text(text)
    assert written == redact_text("中文 password=stream-secret\n")
    assert writer.byte_count == len(written.encode("utf-8"))
    tail = writer.close()
    assert written + tail == expected.decode("utf-8")
    assert writer.byte_count == len(expected)
    assert writer.close() == ""

    assert path.read_bytes() == expected
    assert b"stream-secret" not in path.read_bytes()
    with pytest.raises(RuntimeError, match="关闭"):
        writer.write_text("later")


def test_external_stream_writer_preserves_crlf_returns_chunks_and_counts_tail(
    tmp_path: Path,
) -> None:
    """验证 writer 返回每次实际落盘文本并在 close 写入无换行尾部。

    ``tmp_path`` 提供真实文件。CRLF 跨调用必须在第二次才写入，close 返回尾部，
    ``byte_count`` 等于最终文件的 UTF-8 字节数且重复 close 返回空。
    """
    path = tmp_path / "crlf-stream.log"
    writer = ExternalStreamWriter(path)

    first = writer.write_text("password=CRLF_SECRET\r")
    second = writer.write_text("\n中文尾部")
    tail = writer.close()
    combined = first + second + tail
    expected = redact_text("password=CRLF_SECRET\r\n中文尾部")

    assert first == ""
    assert second == "password=<redacted>\r\n"
    assert tail == "中文尾部"
    assert combined == expected
    assert path.read_bytes().decode("utf-8") == expected
    assert writer.byte_count == len(expected.encode("utf-8"))
    assert writer.close() == ""
    with pytest.raises(RuntimeError, match="关闭"):
        writer.write_text("later")


def test_external_stream_writer_never_leaks_credential_split_across_chunks(
    tmp_path: Path,
) -> None:
    """复现凭据键和值跨 chunk 时旧实现会把第二块秘密原样写入的问题。

    ``tmp_path`` 提供真实外部日志文件。两次写入分别只有键和值，完整逻辑行在
    close 时才结束；文件和返回片段都不得出现秘密，最终内容须等于整段脱敏结果。
    """
    path = tmp_path / "split-secret.log"
    writer = ExternalStreamWriter(path)

    first = writer.write_text("password=")
    second = writer.write_text("LEAKED_SECRET")

    assert path.read_bytes() == b""
    assert first == ""
    assert second == ""
    tail = writer.close()
    expected = redact_text("password=LEAKED_SECRET")
    assert tail == expected
    assert path.read_text(encoding="utf-8") == expected
    assert "LEAKED_SECRET" not in path.read_text(encoding="utf-8")


def test_open_run_handlers_keeps_parallel_runs_isolated(tmp_path: Path) -> None:
    """验证同一构建任务的不同 run_id 创建互不混用的独占日志资源。

    ``tmp_path`` 是共享日志根。两个运行分别写主日志与 stdout，关闭后内容只能
    出现在所属路径中，控制台流保持可用。
    """
    first_paths = _paths(tmp_path / "logs", "run-1")
    second_paths = _paths(tmp_path / "logs", "run-2")
    first_console = StringIO()
    second_console = StringIO()
    first = open_run_handlers(
        first_paths,
        first_console,
        LogLevel.INFO,
        LogLevel.DEBUG,
    )
    second = open_run_handlers(
        second_paths,
        second_console,
        LogLevel.INFO,
        LogLevel.DEBUG,
    )

    try:
        first[0].emit(_event(1))
        second[0].emit(_event(2))
        first[1].write_text("first-only")
        second[1].write_text("second-only")
    finally:
        for resources in (first, second):
            resources[0].close()
            resources[1].close()
            resources[2].close()
            resources[3].close()

    assert "event-1" in first_paths.main.read_text(encoding="utf-8")
    assert "event-2" not in first_paths.main.read_text(encoding="utf-8")
    assert first_paths.stdout.read_text(encoding="utf-8") == "first-only"
    assert second_paths.stdout.read_text(encoding="utf-8") == "second-only"
    assert first_console.closed is False
    assert second_console.closed is False


def test_open_run_handlers_rejects_symlink_directory_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证日志目录链被 OS 报告为符号链接时拒绝创建日志文件。

    ``tmp_path`` 提供真实目录，``monkeypatch`` 仅在 Windows 权限禁止创建测试
    链接时替代 ``Path.is_symlink`` 元数据结果；资源创建与公开失败仍走真实代码。
    """
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-logs"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        link = tmp_path / "reported-link"
        original_is_symlink = Path.is_symlink

        def is_symlink(path: Path) -> bool:
            """仅把测试日志根报告为符号链接，其他路径调用真实元数据方法。"""
            if path == link:
                return True
            return original_is_symlink(path)

        monkeypatch.setattr(Path, "is_symlink", is_symlink)
    paths = _paths(link, "run-link")

    with pytest.raises(ValueError, match="普通目录"):
        open_run_handlers(
            paths,
            StringIO(),
            LogLevel.INFO,
            LogLevel.DEBUG,
        )

    assert not paths.main.exists()


def test_open_run_handlers_rejects_reparse_point_reported_by_os(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 OS 报告任一日志目录层级为 reparse point 时公开装配失败。

    ``tmp_path`` 提供普通真实目录，``monkeypatch`` 仅替代 Windows 元数据判定
    seam，模拟 CI 无法创建 junction 的分支；不模拟资源创建或回滚行为。
    """
    root = tmp_path / "logs"
    paths = _paths(root, "run-reparse")

    def is_reparse(path: Path) -> bool:
        """仅把指定日志根报告为 reparse point，其余路径使用普通结果。"""
        return path == root

    monkeypatch.setattr(handlers_module, "_is_link_or_reparse", is_reparse)

    with pytest.raises(ValueError, match="普通目录"):
        open_run_handlers(
            paths,
            StringIO(),
            LogLevel.INFO,
            LogLevel.DEBUG,
        )

    assert not paths.main.exists()


def test_open_run_handlers_rolls_back_only_new_files_after_partial_collision(
    tmp_path: Path,
) -> None:
    """验证部分初始化失败只删除本次新文件，绝不删除或覆盖碰撞历史文件。

    ``tmp_path`` 提供真实日志根。预置 stderr 触发第三个文件碰撞；先创建的 main
    和 stdout 必须回滚，既有 stderr 内容和目录必须保留，unity 不应创建。
    """
    paths = _paths(tmp_path / "logs", "run-collision")
    paths.directory.mkdir(parents=True)
    paths.stderr.write_text("historical-stderr", encoding="utf-8")

    with pytest.raises(FileExistsError):
        open_run_handlers(
            paths,
            StringIO(),
            LogLevel.INFO,
            LogLevel.DEBUG,
        )

    assert paths.directory.is_dir()
    assert not paths.main.exists()
    assert not paths.stdout.exists()
    assert paths.stderr.read_text(encoding="utf-8") == "historical-stderr"
    assert not paths.unity.exists()


def test_handlers_reject_invalid_paths_levels_streams_and_events(tmp_path: Path) -> None:
    """验证 handler 公开构造和 emit 边界拒绝错误运行时类型。

    ``tmp_path`` 提供真实临时目录。路径、级别、流接口和事件非法时应抛出
    ``ValueError``；已创建资源在断言后显式关闭。
    """
    with pytest.raises(ValueError, match="stream.*write"):
        ConsoleHandler(object(), LogLevel.INFO)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="stream.*flush"):
        ConsoleHandler(_WriteOnlyStream(), LogLevel.INFO)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="min_level"):
        ConsoleHandler(StringIO(), 20)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="path"):
        ExclusiveTextFileHandler("main.log")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="已存在"):
        ExclusiveTextFileHandler(tmp_path / "missing" / "main.log")

    console = ConsoleHandler(StringIO(), LogLevel.INFO)
    with pytest.raises(ValueError, match="event"):
        console.emit(object())  # type: ignore[arg-type]
    console.close()

    file_handler = ExclusiveTextFileHandler(tmp_path / "main.log", LogLevel.WARNING)
    file_handler.emit(_event(1, level=LogLevel.INFO))
    with pytest.raises(ValueError, match="event"):
        file_handler.emit(object())  # type: ignore[arg-type]
    file_handler.close()
    assert (tmp_path / "main.log").read_text(encoding="utf-8") == ""

    writer = ExternalStreamWriter(tmp_path / "stdout.log")
    with pytest.raises(ValueError, match="text"):
        writer.write_text(object())  # type: ignore[arg-type]
    writer.close()

    with pytest.raises(ValueError, match="paths"):
        open_run_handlers(
            object(),  # type: ignore[arg-type]
            StringIO(),
            LogLevel.INFO,
            LogLevel.DEBUG,
        )


def test_open_run_handlers_reports_close_error_but_continues_precise_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证部分创建失败时 close 异常被报告且精确文件回滚仍继续。

    ``tmp_path`` 预置 stdout 碰撞，``monkeypatch`` 让真实主文件 handler 完成关闭
    后报告关闭错误。装配应抛出回滚错误、删除新 main 并保留既有 stdout。
    """
    paths = _paths(tmp_path / "logs", "run-close-error")
    paths.directory.mkdir(parents=True)
    paths.stdout.write_text("historical-stdout", encoding="utf-8")
    original_close = ExclusiveTextFileHandler.close

    def close_then_fail(handler: ExclusiveTextFileHandler) -> None:
        """先执行真实资源关闭，再模拟底层 close 报告失败。"""
        original_close(handler)
        raise OSError("simulated close failure")

    monkeypatch.setattr(ExclusiveTextFileHandler, "close", close_then_fail)

    with pytest.raises(RuntimeError, match="回滚未完整完成"):
        open_run_handlers(
            paths,
            StringIO(),
            LogLevel.INFO,
            LogLevel.DEBUG,
        )

    assert not paths.main.exists()
    assert paths.stdout.read_text(encoding="utf-8") == "historical-stdout"
