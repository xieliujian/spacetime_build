"""验证运行级日志会话能够输出完整生命周期和外部流日志。"""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from observability import ErrorCode, LogContext, LogLevel
from observability.runtime import RunLogSession, RunOutcome


def test_run_log_session_writes_lifecycle_external_streams_and_duration(
    tmp_path: Path,
) -> None:
    """Given 一次带秘密的运行，When 完成会话，Then 全部日志脱敏且包含耗时。"""
    started_at = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)
    finished_at = datetime(2026, 8, 22, 10, 0, 1, 234000, tzinfo=timezone.utc)
    context = LogContext("build-1", "resource", "run-1")
    console = StringIO()

    session = RunLogSession.open(
        tmp_path / "logs",
        context,
        started_at=started_at,
        console_stream=console,
        console_level=LogLevel.INFO,
        file_level=LogLevel.DEBUG,
        secret_values=("token-secret",),
    )
    session.start(fields=(("source_revision", "r123"),))
    with session.phase("unity", fields=(("asset_count", "2"),)):
        session.emit(
            LogLevel.DEBUG,
            "Unity 处理中 token=token-secret",
        )
        session.write_stdout("stdout token-secret\n")
        session.write_stderr("stderr warning\n")
        session.write_unity("unity output\n")
    result = session.finish(
        RunOutcome.SUCCEEDED,
        finished_at=finished_at,
        fields=(("artifact_count", "2"),),
    )
    session.close()

    assert result.outcome is RunOutcome.SUCCEEDED
    assert result.duration_ms == 1234
    assert "运行开始" in result.paths.main.read_text(encoding="utf-8")
    main_text = result.paths.main.read_text(encoding="utf-8")
    assert "运行完成" in main_text
    assert "阶段开始" in main_text
    assert "阶段完成" in main_text
    assert 'duration_ms="1234"' in main_text
    assert "token-secret" not in main_text
    assert "token-secret" not in result.paths.stdout.read_text(encoding="utf-8")
    assert result.paths.stderr.read_text(encoding="utf-8") == "stderr warning\n"
    assert result.paths.unity.read_text(encoding="utf-8") == "unity output\n"
    assert "运行完成" in console.getvalue()


def test_run_log_session_records_failure_with_error_code_and_diagnostics(tmp_path: Path) -> None:
    """Given 失败阶段，When finish，Then 主日志包含稳定错误码和诊断路径。"""
    started_at = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)
    context = LogContext("build-1", "release", "run-2")
    session = RunLogSession.open(
        tmp_path / "logs",
        context,
        started_at=started_at,
        console_stream=StringIO(),
        console_level=LogLevel.ERROR,
        file_level=LogLevel.DEBUG,
    )

    session.start()
    result = session.finish(
        RunOutcome.FAILED,
        finished_at=datetime(2026, 8, 22, 10, 0, 0, 500000, tzinfo=timezone.utc),
        message="Unity failed password=hidden",
        error_code=ErrorCode.UNITY_PROCESS_EXIT_NONZERO,
        diagnostic_paths=(tmp_path / "unity.log",),
    )
    session.close()

    text = result.paths.main.read_text(encoding="utf-8")
    assert "[ERROR]" in text
    assert "UNITY_PROCESS_EXIT_NONZERO" in text
    assert "diagnostic_path=" in text
    assert "hidden" not in text
