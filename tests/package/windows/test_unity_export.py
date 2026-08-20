"""Windows Unity Player 导出请求测试。"""

from pathlib import Path

from package.unity_export import UnityPlayerExporter, WindowsPlayerExportOptions
from package.platforms.windows.model import WindowsArchitecture


def test_windows_player_exporter_generates_typed_architecture_and_release_settings(
    tmp_path: Path,
) -> None:
    """验证 Windows 导出请求包含架构、development 和 ReleaseBundle 入口。"""
    options = WindowsPlayerExportOptions(
        architecture=WindowsArchitecture.X86_64,
        development=False,
        release_bundle_id="a" * 64,
    )
    request = UnityPlayerExporter.plan_windows(
        project_path=tmp_path / "project",
        output_path=tmp_path / "Game.exe",
        unity_executable=tmp_path / "Unity.exe",
        log_path=tmp_path / "unity.log",
        unity_version="2022.3.62f2",
        options=options,
        timeout_seconds=120.0,
    )

    assert request.arguments == (
        "--platform",
        "windows",
        "--unity-version",
        "2022.3.62f2",
        "--output",
        (tmp_path / "Game.exe").as_posix(),
        "--architecture",
        "x86_64",
        "--development",
        "false",
        "--release-bundle-id",
        "a" * 64,
    )
    assert request.timeout_seconds == 120.0
