"""验证 CLI 命令树、参数互斥和帮助文本稳定性。"""

import pytest

from cli.parser import build_parser


def test_parser_accepts_planned_command_tree_and_common_flags() -> None:
    """Given 资源构建命令，When 解析，Then 得到不含适配器的纯参数 Namespace。"""
    args = build_parser().parse_args(
        ["resource", "build", "--platform", "android", "--revision", "123", "--dry-run"]
    )
    assert args.command == "resource build"
    assert args.platform == "android"
    assert args.dry_run is True


def test_parser_rejects_conflicting_output_modes() -> None:
    """Given JSON 和 human 同时指定，When 解析，Then argparse 以 2 失败。"""
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--json", "--human", "plan"])
    assert exc_info.value.code == 2


def test_parser_help_mentions_only_planned_command_names() -> None:
    """Given 根 parser，Then 帮助文本包含稳定命令树。"""
    text = build_parser().format_help()
    assert "resource" in text
    assert "release" in text
    assert "package" in text
    assert "run" in text
