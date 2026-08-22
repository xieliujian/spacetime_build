"""CLI argparse 命令树和参数边界。

parser 只把命令行文本转换为 ``argparse.Namespace``，不加载 TOML、不读取环境、
不构造外部适配器。命令名与参数在这里固定，实际业务路由由 ``cli.commands`` 完成。
"""

from __future__ import annotations

import argparse


def _add_common_arguments(parser: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    """为根和叶子 parser 添加一致的公共参数。"""
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument("--config", type=str, default=default, help="TOML 配置路径")
    parser.add_argument("--profile", type=str, default=default, help="显式 Profile")
    parser.add_argument("--platform", choices=("android", "ios", "windows"), default=default)
    parser.add_argument("--revision", type=str, default=default, help="固定源码 revision")
    parser.add_argument("--run-id", type=str, default=default, help="运行身份")
    parser.add_argument("--dry-run", action="store_true", default=default)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", default=default, help="JSON 输出")
    modes.add_argument("--human", action="store_true", default=default, help="人类可读输出")


def _leaf(
    parent: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    command: str,
    help_text: str,
) -> argparse.ArgumentParser:
    """创建一个命令叶子并固定内部 command 字符串。"""
    parser = parent.add_parser(name, help=help_text)
    parser.set_defaults(command=command)
    _add_common_arguments(parser, suppress_defaults=True)
    return parser


def build_parser() -> argparse.ArgumentParser:
    """构造完整命令树。

    返回：
        每次调用新建的 ``ArgumentParser``，便于测试和多次嵌入。

    异常：
        argparse 只在调用 ``parse_args`` 时对用户输入抛 ``SystemExit(2)``。

    约束与副作用：
        构造过程只分配 parser 对象，不加载配置、不访问文件、不创建适配器。
    """
    parser = argparse.ArgumentParser(prog="spacetime-build", description="SE 构建系统运行入口")
    _add_common_arguments(parser, suppress_defaults=False)
    groups = parser.add_subparsers(dest="command_group", required=True)

    _leaf(groups, "plan", "plan", "生成构建计划")

    resource = groups.add_parser("resource", help="资源构建命令")
    resource_sub = resource.add_subparsers(dest="resource_command", required=True)
    _leaf(resource_sub, "build", "resource build", "构建资源")

    release = groups.add_parser("release", help="发布命令")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    _leaf(release_sub, "build", "release build", "构建并准备正式版本")
    _leaf(release_sub, "publish", "release publish", "发布 ReleaseBundle")
    version = release_sub.add_parser("version", help="版本号命令")
    version_sub = version.add_subparsers(dest="version_command", required=True)
    _leaf(version_sub, "preview", "release version preview", "预览下一个 FileListNo")
    _leaf(version_sub, "allocate", "release version allocate", "预留正式 FileListNo")
    _leaf(release_sub, "upload", "release upload", "上传正式版本不可变对象")
    _leaf(release_sub, "activate", "release activate", "CAS 激活版本入口")
    _leaf(release_sub, "rollback", "release rollback", "回滚到历史 ReleaseBundle")

    package = groups.add_parser("package", help="客户端包体命令")
    package_sub = package.add_subparsers(dest="package_command", required=True)
    _leaf(package_sub, "build", "package build", "构建客户端包体")

    run = groups.add_parser("run", help="运行记录操作")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    _leaf(run_sub, "status", "run status", "查看运行状态")
    _leaf(run_sub, "cancel", "run cancel", "请求取消运行")
    _leaf(run_sub, "resume", "run resume", "恢复运行")

    external = groups.add_parser("external", help="外部系统探针")
    external_sub = external.add_subparsers(dest="external_command", required=True)
    _leaf(external_sub, "probe", "external probe", "执行 SVN/Unity/Jenkins/Secrets/CDN 探针")

    compatibility = groups.add_parser("compatibility", help="旧客户端兼容验收")
    compatibility_sub = compatibility.add_subparsers(dest="compatibility_command", required=True)
    _leaf(
        compatibility_sub,
        "dual-run",
        "compatibility dual-run",
        "执行隔离旧系统双跑和 Parser 验收",
    )
    return parser


__all__ = ["build_parser"]
