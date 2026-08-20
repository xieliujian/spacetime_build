"""Windows Player 布局计划与安全应用测试。"""

from pathlib import Path

import pytest

from package.platforms.windows.layout import (
    LayoutCopy,
    LayoutWrite,
    WindowsLayoutApplier,
    WindowsLayoutPlanner,
)


def test_windows_layout_copies_player_data_and_writes_are_deterministic(tmp_path: Path) -> None:
    """验证 exe、Data 和运行库布局成功应用且不改变源树。"""
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    source.mkdir()
    workspace.mkdir()
    (source / "Game.exe").write_bytes(b"player")
    (source / "Game_Data").mkdir()
    (source / "Game_Data" / "globalgamemanagers").write_bytes(b"data")

    plan = WindowsLayoutPlanner.plan(
        workspace,
        copies=(
            LayoutCopy(source / "Game.exe", "Game.exe"),
            LayoutCopy(source / "Game_Data" / "globalgamemanagers", "Game_Data/globalgamemanagers"),
        ),
        writes=(LayoutWrite("appconfig.json", b'{"version":1}'),),
    )
    WindowsLayoutApplier.apply(plan)

    assert (workspace / "Game.exe").read_bytes() == b"player"
    assert (workspace / "Game_Data" / "globalgamemanagers").read_bytes() == b"data"
    assert (workspace / "appconfig.json").read_bytes() == b'{"version":1}'
    assert (source / "Game.exe").read_bytes() == b"player"


def test_windows_layout_rejects_duplicate_casefold_paths_and_escape(tmp_path: Path) -> None:
    """验证重复目标、路径逃逸和 Windows 保留设备名均失败。"""
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    source.mkdir()
    workspace.mkdir()
    file_path = source / "Game.exe"
    file_path.write_bytes(b"player")

    with pytest.raises(ValueError, match="冲突"):
        WindowsLayoutPlanner.plan(
            workspace,
            copies=(LayoutCopy(file_path, "Game.exe"), LayoutCopy(file_path, "game.EXE")),
        )
    with pytest.raises(ValueError):
        WindowsLayoutPlanner.plan(workspace, writes=(LayoutWrite("../outside.txt", b"x"),))
    with pytest.raises(ValueError):
        WindowsLayoutPlanner.plan(workspace, writes=(LayoutWrite("CON.txt", b"x"),))
    for invalid_destination in ("foo:bar", "foo.", "foo "):
        with pytest.raises(ValueError):
            WindowsLayoutPlanner.plan(
                workspace,
                writes=(LayoutWrite(invalid_destination, b"x"),),
            )


def test_windows_layout_rejects_source_symlinks(tmp_path: Path) -> None:
    """验证布局不会跟随源目录中的符号链接逃逸。"""
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    source.mkdir()
    workspace.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = source / "Game.exe"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 环境不允许创建符号链接")

    with pytest.raises(ValueError, match="符号链接"):
        WindowsLayoutPlanner.plan(workspace, copies=(LayoutCopy(link, "Game.exe"),))
