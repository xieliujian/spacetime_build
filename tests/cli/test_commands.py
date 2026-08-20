"""验证命令处理器只路由到注入的 application handler。"""

from cli.commands import CommandDispatcher, CommandServices
from cli.parser import build_parser


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
