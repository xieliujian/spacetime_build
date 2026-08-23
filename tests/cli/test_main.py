"""验证 CLI main 的整数返回和导入无副作用。"""

from pathlib import Path

from cli.commands import CommandServices
from cli.main import main


def test_main_help_returns_zero_without_loading_configuration() -> None:
    """Given help 参数，When 调用 main，Then 返回 0。"""
    assert main(["--help"]) == 0


def test_main_auto_wires_default_command_log_output(tmp_path: Path, capsys: object) -> None:
    """Given 显式服务和日志根目录，When 执行 CLI，Then 自动生成主日志和 build.log。"""
    services = CommandServices(plan=lambda _args: {"state": "SUCCEEDED"})

    assert (
        main(
            [
                "--log-root",
                str(tmp_path / "logs"),
                "--build-id",
                "build-1",
                "--run-id",
                "run-1",
                "plan",
            ],
            services=services,
        )
        == 0
    )

    assert (tmp_path / "logs" / "build-1" / "build.log").is_file()
    assert next((tmp_path / "logs").rglob("plan_*.log")).is_file()
