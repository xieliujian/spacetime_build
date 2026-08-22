"""把 parser 命令路由到注入的 application handler。

命令处理器不实现领域判断、不直接调用 Planner/Executor 或平台工具。每个 handler
接收 argparse Namespace，具体 application 请求和服务组合由 composition root 提供。
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Callable
from dataclasses import dataclass

from core.errors import ConfigurationError

CommandHandler = Callable[[Namespace], object]


@dataclass(frozen=True, slots=True)
class CommandServices:
    """各 CLI 命令的可选 application handler 注册表。"""

    plan: CommandHandler | None = None
    resource_build: CommandHandler | None = None
    release_publish: CommandHandler | None = None
    release_build: CommandHandler | None = None
    release_version_preview: CommandHandler | None = None
    release_version_allocate: CommandHandler | None = None
    release_upload: CommandHandler | None = None
    release_activate: CommandHandler | None = None
    release_rollback: CommandHandler | None = None
    external_probe: CommandHandler | None = None
    compatibility_dual_run: CommandHandler | None = None
    package_build: CommandHandler | None = None
    run_status: CommandHandler | None = None
    run_cancel: CommandHandler | None = None
    run_resume: CommandHandler | None = None


class CommandDispatcher:
    """按固定 command 名调用唯一注入 handler。"""

    def __init__(self, services: CommandServices) -> None:
        """保存不可变 handler 注册表。"""
        if not isinstance(services, CommandServices):
            raise TypeError("services 必须是 CommandServices")
        self._services = services

    def dispatch(self, args: Namespace) -> object:
        """路由一次解析结果并返回 application 摘要。

        参数：
            args: ``cli.parser`` 产生的 Namespace。

        返回：
            对应 handler 的原始摘要。

        异常：
            command 缺失或 handler 未注入时抛 ``ConfigurationError``。

        约束与副作用：
            不在 dispatcher 中解析业务字段、不构造外部适配器；仅调用一次 handler。
        """
        command = getattr(args, "command", None)
        handlers: dict[str, CommandHandler | None] = {
            "plan": self._services.plan,
            "resource build": self._services.resource_build,
            "release publish": self._services.release_publish,
            "release build": self._services.release_build,
            "release version preview": self._services.release_version_preview,
            "release version allocate": self._services.release_version_allocate,
            "release upload": self._services.release_upload,
            "release activate": self._services.release_activate,
            "release rollback": self._services.release_rollback,
            "external probe": self._services.external_probe,
            "compatibility dual-run": self._services.compatibility_dual_run,
            "package build": self._services.package_build,
            "run status": self._services.run_status,
            "run cancel": self._services.run_cancel,
            "run resume": self._services.run_resume,
        }
        if not isinstance(command, str):
            raise ConfigurationError("命令缺失或不是字符串")
        handler = handlers.get(command)
        if handler is None:
            raise ConfigurationError(f"命令未装配 handler: {command!r}")
        return handler(args)


__all__ = ["CommandDispatcher", "CommandHandler", "CommandServices"]
