"""Windows 便携包确定性归档测试。

本模块只验证 payload 到 ZIP 字节和 Blob 摘要的纯 Python 行为，不执行签名工具、
安装器工具或任何真实 Windows 平台命令。
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from package.platforms.windows.model import WindowsProfile
from package.platforms.windows.portable import (
    WindowsPortableBuilder,
    WindowsPortablePayload,
)


def _payload(root: Path, *, signed: bool, profile: WindowsProfile) -> WindowsPortablePayload:
    """构造测试用便携 payload，并显式表达签名边界。"""
    return WindowsPortablePayload(root, signed=signed, profile=profile)


def test_portable_builder_returns_deterministic_zip_with_stable_metadata(
    tmp_path: Path,
) -> None:
    """验证文件顺序、空目录、时间戳、权限和 Blob 摘要均稳定。"""
    root = tmp_path / "payload"
    (root / "Game_Data" / "empty").mkdir(parents=True)
    (root / "Game_Data" / "globalgamemanagers").write_bytes(b"data")
    (root / "Game.exe").write_bytes(b"player")

    first = WindowsPortableBuilder.build(
        _payload(root, signed=True, profile=WindowsProfile.PRODUCTION)
    )
    second = WindowsPortableBuilder.build(
        _payload(root, signed=True, profile=WindowsProfile.PRODUCTION)
    )

    assert first.content == second.content
    assert first.files == (
        "Game.exe",
        "Game_Data/",
        "Game_Data/empty/",
        "Game_Data/globalgamemanagers",
    )
    assert first.blob.sha256 == hashlib.sha256(first.content).hexdigest()
    assert first.blob.locator == f"blobs/{first.blob.sha256}"
    assert first.blob.size == len(first.content)
    assert first.signed is True
    assert first.profile is WindowsProfile.PRODUCTION

    archive_path = tmp_path / "portable.zip"
    archive_path.write_bytes(first.content)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == list(first.files)
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
        assert all(
            (item.external_attr >> 16) & 0o777 == (0o755 if item.is_dir() else 0o644)
            for item in archive.infolist()
        )


def test_portable_builder_accepts_explicit_unsigned_test_payload(tmp_path: Path) -> None:
    """验证 unsigned 只作为显式测试 profile 输入，并在摘要中保留边界。"""
    root = tmp_path / "payload"
    root.mkdir()
    (root / "Game.exe").write_bytes(b"test-player")

    result = WindowsPortableBuilder.build(_payload(root, signed=False, profile=WindowsProfile.TEST))

    assert result.signed is False
    assert result.profile is WindowsProfile.TEST


def test_portable_builder_rejects_unsigned_production_payload(tmp_path: Path) -> None:
    """验证生产 payload 在输入模型和构建器边界都不能 unsigned。"""
    root = tmp_path / "payload"
    root.mkdir()

    with pytest.raises(ValueError, match="unsigned"):
        _payload(root, signed=False, profile=WindowsProfile.PRODUCTION)


def test_portable_builder_rejects_symlink(tmp_path: Path) -> None:
    """验证归档器不跟随 payload 中的符号链接。"""
    root = tmp_path / "payload"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = root / "escape.bin"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 环境不允许创建符号链接")

    with pytest.raises(ValueError, match="符号链接"):
        WindowsPortableBuilder.build(_payload(root, signed=True, profile=WindowsProfile.TEST))

    link.unlink()


def test_portable_builder_rejects_casefold_duplicate_paths(tmp_path: Path) -> None:
    """验证 Windows 大小写折叠后的重复路径被拒绝。"""
    root = tmp_path / "payload"
    root.mkdir()
    try:
        (root / "Game.exe").write_bytes(b"one")
        if (root / "game.EXE").exists():
            pytest.skip("当前文件系统不允许创建大小写不同的同目录文件")
        (root / "game.EXE").write_bytes(b"two")
    except FileExistsError:
        pytest.skip("当前文件系统不允许创建大小写不同的同目录文件")

    with pytest.raises(ValueError, match="重复路径"):
        WindowsPortableBuilder.build(_payload(root, signed=True, profile=WindowsProfile.TEST))


def test_portable_builder_rejects_zip_slip_path_components(tmp_path: Path) -> None:
    """验证 payload 中的路径组件不能产生绝对路径或 dot-dot ZIP 条目。"""
    root = tmp_path / "payload"
    (root / "safe").mkdir(parents=True)
    (root / "safe" / "file.bin").write_bytes(b"safe")

    result = WindowsPortableBuilder.build(_payload(root, signed=True, profile=WindowsProfile.TEST))

    assert all(
        name != ""
        and not name.startswith("/")
        and "\\" not in name
        and all(part not in {"", ".", ".."} for part in name.rstrip("/").split("/"))
        for name in result.files
    )

    for malicious_path in (
        "../escape.bin",
        "/absolute/escape.bin",
        r"nested\escape.bin",
        "foo:bar",
        "foo.",
        "foo ",
    ):
        with pytest.raises(ValueError, match="ZIP 路径"):
            WindowsPortableBuilder.validate_archive_path(malicious_path)
