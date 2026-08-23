"""验证命令级日志 middleware 覆盖成功、失败和取消结果。"""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from observability.aggregation import BuildLogCollector
from observability.context import LogContext
from observability.runtime import CommandLogRunner, RunLogSession


def _factory(root: Path):
    """创建使用固定上下文和日志根目录的测试会话 factory。"""
    started_at = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)

    def create(command: str, _args: object) -> RunLogSession:
        """为每个命令创建独立真实日志会话。"""
        task_name = command.replace(" ", "_")
        return RunLogSession.open(
            root,
            LogContext("build-1", task_name, f"run-{task_name}"),
            started_at=started_at,
            console_stream=StringIO(),
        )

    return create


def test_command_log_runner_records_successful_handler_result(tmp_path: Path) -> None:
    """Given handler 成功返回状态，When middleware 执行，Then 主日志含命令生命周期。"""
    runner = CommandLogRunner(_factory(tmp_path / "logs"))
    result = runner.run(
        "resource build",
        object(),
        lambda _args: SimpleNamespace(state="SUCCEEDED"),
    )

    assert getattr(result, "state") == "SUCCEEDED"
    main = next((tmp_path / "logs").rglob("*.log"))
    text = main.read_text(encoding="utf-8")
    assert "运行开始" in text
    assert "命令完成" in text
    assert 'command="resource build"' in text


def test_command_log_runner_updates_build_log_collector(tmp_path: Path) -> None:
    """Given 注入汇总 collector，When 命令完成，Then build.log 可直接定位任务日志。"""
    started_at = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)
    collector = BuildLogCollector(
        tmp_path / "logs",
        "build-1",
        started_at=started_at,
        fields=(("profile", "release"),),
    )
    runner = CommandLogRunner(_factory(tmp_path / "logs"), collector=collector)

    runner.run("resource build", object(), lambda _args: SimpleNamespace(state="SUCCEEDED"))

    build_log = tmp_path / "logs" / "build-1" / "build.log"
    assert build_log.is_file()
    text = build_log.read_text(encoding="utf-8")
    assert 'task="resource_build"' in text
    assert "resource build" in text


def test_command_log_runner_logs_exception_without_swallowing_or_leaking_secret(
    tmp_path: Path,
) -> None:
    """Given handler 抛出带秘密异常，When middleware 执行，Then 失败落盘且原异常继续抛出。"""
    runner = CommandLogRunner(_factory(tmp_path / "logs"))

    def fail(_args: object) -> object:
        """制造一个需要转换为稳定失败事件的异常。"""
        raise ValueError("Unity failed password=secret-value")

    with pytest.raises(ValueError, match="password"):
        runner.run("external probe", object(), fail)

    main = next((tmp_path / "logs").rglob("external_probe_*.log"))
    text = main.read_text(encoding="utf-8")
    assert "[ERROR]" in text
    assert "INTERNAL_ERROR" in text
    assert "secret-value" not in text
