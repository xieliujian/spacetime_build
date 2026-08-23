"""验证命令处理器只路由到注入的 application handler。"""

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from cli.commands import CommandDispatcher, CommandServices
from cli.parser import build_parser
from observability import LogContext, RunLogSession
from observability.runtime import CommandLogRunner


def test_command_dispatcher_routes_plan_without_domain_logic() -> None:
    """Given 注入 plan handler，When 分发，Then 原样返回 handler 结果。"""
    calls: list[str] = []

    def handler(args: object) -> object:
        """记录调用并返回结果。"""
        calls.append("plan")
        return {"planned": True}

    args = build_parser().parse_args(["plan"])
    result = CommandDispatcher(CommandServices(plan=handler)).dispatch(args)
    assert result == {"planned": True}
    assert calls == ["plan"]


def test_command_dispatcher_routes_through_injected_log_runner(tmp_path: Path) -> None:
    """Given 注入日志 middleware，When 分发命令，Then 成功生命周期写入主日志。"""
    started_at = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)

    def factory(command: str, _args: object) -> RunLogSession:
        """为 dispatcher 测试创建真实日志会话。"""
        return RunLogSession.open(
            tmp_path / "logs",
            LogContext("build-1", command, "run-1"),
            started_at=started_at,
            console_stream=StringIO(),
        )

    args = build_parser().parse_args(["plan"])
    dispatcher = CommandDispatcher(
        CommandServices(
            plan=lambda _args: {"state": "SUCCEEDED"},
            log_runner=CommandLogRunner(factory),
        )
    )

    assert dispatcher.dispatch(args) == {"state": "SUCCEEDED"}
    main = next((tmp_path / "logs").rglob("plan_*.log"))
    assert "命令完成" in main.read_text(encoding="utf-8")
