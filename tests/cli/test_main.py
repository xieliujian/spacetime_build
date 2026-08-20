"""验证 CLI main 的整数返回和导入无副作用。"""

from cli.main import main


def test_main_help_returns_zero_without_loading_configuration() -> None:
    """Given help 参数，When 调用 main，Then 返回 0。"""
    assert main(["--help"]) == 0
