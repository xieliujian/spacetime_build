"""spacetime-build 的唯一可执行入口。

``main`` 只在函数调用后解析参数、装配显式服务、分发命令和渲染结果。导入模块不
加载 TOML、不读取环境、不创建适配器；未知业务异常映射为 10 并只输出脱敏摘要。
"""

from __future__ import annotations

from collections.abc import Sequence
import sys

from cli.bootstrap import build_composition_root
from cli.commands import CommandDispatcher, CommandServices
from cli.exit_codes import exit_code_for
from cli.output import render_error, render_success
from cli.parser import build_parser


def main(
    argv: Sequence[str] | None = None,
    *,
    services: CommandServices | None = None,
) -> int:
    """解析并执行一次 CLI 调用，返回稳定整数退出码。

    参数：
        argv: 不含程序名的参数序列；为 ``None`` 时使用当前进程参数。
        services: 可选显式服务集合；生产装配必须由调用方提供 factory。

    返回：
        成功 0；argparse/配置/规划/工具/发布等错误返回计划中的 2..10。

    异常与副作用：
        argparse 的 help/version SystemExit 被转换为整数；业务异常不泄漏 traceback，
        结果输出到 stdout，错误输出到 stderr。
    """
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    json_mode = bool(getattr(args, "json", False))
    try:
        active_services = services if services is not None else build_composition_root(None)
        value = CommandDispatcher(active_services).dispatch(args)
        print(render_success(value, json_mode=json_mode))
        return 0
    except KeyboardInterrupt as exc:
        print(
            render_error(exc, code=9, json_mode=json_mode, run_id=getattr(args, "run_id", None)),
            file=sys.stderr,
        )
        return 9
    except BaseException as exc:
        code = exit_code_for(exc)
        print(
            render_error(exc, code=code, json_mode=json_mode, run_id=getattr(args, "run_id", None)),
            file=sys.stderr,
        )
        return code


if __name__ == "__main__":  # pragma: no cover - 仅进程入口路径
    raise SystemExit(main())


__all__ = ["main"]
