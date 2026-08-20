"""Windows 内部 inventory 模型、收集和确定性 codec 测试。"""

from pathlib import Path

import pytest

from package.platforms.windows.inventory import (
    WindowsInventoryCodec,
    WindowsInventoryCollector,
    WindowsInventoryEntry,
)


def test_windows_inventory_collects_hash_size_version_and_excludes_directories(
    tmp_path: Path,
) -> None:
    """验证 inventory 只收集普通文件，按逻辑路径排序并记录 SHA256/大小。"""
    root = tmp_path / "payload"
    root.mkdir()
    (root / "Game.exe").write_bytes(b"player")
    (root / "Data").mkdir()
    (root / "Data" / "a.bin").write_bytes(b"a")
    (root / "cache").mkdir()
    (root / "cache" / "ignored.bin").write_bytes(b"ignored")

    inventory = WindowsInventoryCollector.collect(root, "1.2.3", excluded_directories=("cache",))

    assert [entry.logical_path for entry in inventory.entries] == ["Data/a.bin", "Game.exe"]
    assert inventory.package_version == "1.2.3"
    assert inventory.entries[0].size == 1
    assert (
        inventory.entries[0].sha256
        == "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    )


def test_windows_inventory_codec_is_deterministic_and_rejects_duplicates(tmp_path: Path) -> None:
    """验证 inventory JSON 可稳定 round-trip，并拒绝大小写折叠重复路径。"""
    first = WindowsInventoryCollector.collect(tmp_path, "1.0.0")
    path = tmp_path / "inventory.json"
    WindowsInventoryCodec.write(first, path)
    raw = path.read_bytes()
    second = WindowsInventoryCodec.read(path)
    assert second == first
    assert WindowsInventoryCodec.encode(first) == raw

    with pytest.raises(ValueError, match="重复"):
        WindowsInventoryEntry("Game.exe", 1, "a" * 64, "1.0.0")
        WindowsInventoryCodec.from_entries(
            "1.0.0",
            (
                WindowsInventoryEntry("Game.exe", 1, "a" * 64, "1.0.0"),
                WindowsInventoryEntry("game.EXE", 1, "a" * 64, "1.0.0"),
            ),
        )


@pytest.mark.parametrize("logical_path", ("foo:bar", "foo.", "foo "))
def test_windows_inventory_rejects_ntfs_special_path_semantics(logical_path: str) -> None:
    """验证 inventory 路径不接受 NTFS alternate stream 或尾随特殊字符。"""
    with pytest.raises(ValueError):
        WindowsInventoryEntry(logical_path, 1, "a" * 64, "1.0.0")
