"""CLI 默认日志会话 factory。

本模块只负责把已经解析的命令参数转换为日志上下文，并把日志根目录、控制台流和
可选秘密值显式传给 ``RunLogSession``。它不读取 TOML、不解析 SecretRef、不创建
SVN/Unity/Jenkins 适配器；真正的外部秘密值必须由 composition root 在拿到租约后
显式提供。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from threading import RLock
from typing import TextIO
from uuid import uuid4

from observability.aggregation import BuildLogCollector
from observability.context import LogContext
from observability.failures import ErrorCode
from observability.runtime import CommandLogRunner, RunLogResult, RunLogSession, RunOutcome


class DefaultCommandLogFactory:
    """为每次 CLI 命令创建独立日志会话的默认 factory。"""

    def __init__(
        self,
        root: Path,
        *,
        console_stream: TextIO | None = None,
        secret_values: tuple[str, ...] = (),
    ) -> None:
        """保存日志根目录、控制台流和外部秘密脱敏值。

        参数：
            root: 日志根目录。
            console_stream: 可选控制台流；缺省使用 stderr。
            secret_values: 只用于外部流 writer 的秘密值元组。

        返回：
            无；构造不创建目录和文件。

        异常、约束与副作用：
            路径或秘密值类型非法时抛 ``ValueError``；不会读取环境或配置。
        """
        if not isinstance(root, Path):
            raise ValueError("root 必须是 Path")
        if console_stream is not None and not callable(getattr(console_stream, "write", None)):
            raise ValueError("console_stream 必须提供 write")
        if not isinstance(secret_values, tuple) or any(
            not isinstance(value, str) for value in secret_values
        ):
            raise ValueError("secret_values 必须是 tuple[str, ...]")
        self._root = root
        self._console_stream = console_stream if console_stream is not None else sys.stderr
        self._secret_values = secret_values

    def __call__(self, command: str, args: object) -> RunLogSession:
        """根据命令和 Namespace 创建一次真实日志会话。

        参数：
            command: parser 固定的命令字符串。
            args: 命令参数对象；读取 build_id/run_id，其他字段只进入主日志的安全字段。

        返回：
            尚未写入开始事件、但已经打开四个独占日志文件的 ``RunLogSession``。

        异常、约束与副作用：
            命令身份非法或日志文件碰撞时抛异常；创建日志文件是唯一外部副作用。
        """
        if not isinstance(command, str) or not command:
            raise ValueError("command 必须是非空字符串")
        build_id = _argument_text(args, "build_id") or _argument_text(args, "run_id") or "cli"
        run_id = _argument_text(args, "run_id") or uuid4().hex
        task_name = command.replace(" ", "_")
        context = LogContext(build_id, task_name, run_id)
        return RunLogSession.open(
            self._root,
            context,
            console_stream=self._console_stream,
            secret_values=self._secret_values,
        )


def build_default_command_log_runner(
    root: Path,
    *,
    console_stream: TextIO | None = None,
    secret_values: tuple[str, ...] = (),
) -> CommandLogRunner:
    """构造 CLI 默认命令日志 runner。

    参数：
        root、console_stream、secret_values: 传给 ``DefaultCommandLogFactory`` 的显式依赖。

    返回：
        包含默认会话 factory 的 ``CommandLogRunner``；构造不创建日志资源。

    异常、约束与副作用：
        依赖非法时沿 factory 校验抛异常；不会读取配置或执行命令。
    """
    factory = DefaultCommandLogFactory(
        root,
        console_stream=console_stream,
        secret_values=secret_values,
    )
    return CommandLogRunner(factory, collector=_BuildLogCollectorRegistry(root))


def _argument_text(args: object, name: str) -> str | None:
    """读取 Namespace 中安全的非空文本参数。"""
    value = getattr(args, name, None)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"args.{name} 必须是非空字符串")
    return value


class _BuildLogCollectorRegistry:
    """为默认 CLI runner 按 build_id 保存增量汇总 collector。"""

    def __init__(self, root: Path) -> None:
        """绑定日志根目录并初始化并发保护。"""
        self._root = root
        self._collectors: dict[str, BuildLogCollector] = {}
        self._active: BuildLogCollector | None = None
        self._lock = RLock()

    def record(
        self,
        result: RunLogResult,
        *,
        message: str,
        error_code: ErrorCode | None = None,
    ) -> None:
        """把一次命令回执登记到对应 build collector。"""
        with self._lock:
            collector = self._collectors.get(result.context.build_id)
            if collector is None:
                collector = BuildLogCollector(
                    self._root,
                    result.context.build_id,
                    started_at=result.started_at,
                )
                self._collectors[result.context.build_id] = collector
            collector.record(result, message=message, error_code=error_code)
            self._active = collector

    def write(self, *, finished_at: object, outcome: object) -> Path:
        """刷新最近一次命令对应的 build.log。"""
        with self._lock:
            if self._active is None:
                raise RuntimeError("没有可汇总的命令日志")
            if not isinstance(finished_at, datetime):
                raise TypeError("finished_at 必须是 datetime")
            if not isinstance(outcome, RunOutcome):
                raise TypeError("outcome 必须是 RunOutcome")
            return self._active.write(finished_at=finished_at, outcome=outcome)


__all__ = ["DefaultCommandLogFactory", "build_default_command_log_runner"]
