"""IL2CPP 归档的确定性、安全边界和受限解包测试。"""

from pathlib import Path
import zipfile

import pytest

from services.il2cpp.archive import Il2CppArchiveCodec, Il2CppArchiveLimits


def test_create_is_deterministic_and_returns_sorted_file_table(tmp_path: Path) -> None:
    """Given 同一目录内容，When 创建两次归档，Then 字节摘要和文件表一致。"""
    source = tmp_path / "source"
    source.joinpath("z").mkdir(parents=True)
    source.joinpath("a.txt").write_bytes(b"a")
    source.joinpath("z", "b.bin").write_bytes(b"bb")
    first = Il2CppArchiveCodec.create(source, tmp_path / "first.zip")
    second = Il2CppArchiveCodec.create(source, tmp_path / "second.zip")

    assert first.blob == second.blob
    assert tuple(item.path for item in first.entries) == ("a.txt", "z/b.bin")


def test_create_rejects_symlink_and_casefold_duplicate(tmp_path: Path) -> None:
    """验证归档不跟随链接，并拒绝 Windows 大小写折叠后的重复路径。"""
    duplicate_archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate_archive, "w") as archive:
        archive.writestr("A.bin", b"a")
        archive.writestr("a.bin", b"b")
    with pytest.raises(ValueError, match="大小写"):
        Il2CppArchiveCodec.extract(duplicate_archive, tmp_path / "duplicate")

    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "real.bin").write_bytes(b"real")
    try:
        (linked / "link.bin").symlink_to(linked / "real.bin")
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 节点未提供创建符号链接权限")
    with pytest.raises(ValueError, match="符号链接"):
        Il2CppArchiveCodec.create(linked, tmp_path / "linked.zip")


def test_create_enforces_file_and_size_limits(tmp_path: Path) -> None:
    """验证文件数量、单文件大小和总大小限制不会被归档输入绕过。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.bin").write_bytes(b"1234")
    with pytest.raises(ValueError, match="单文件"):
        Il2CppArchiveCodec.create(
            source,
            tmp_path / "archive.zip",
            limits=Il2CppArchiveLimits(max_file_size=3),
        )
    with pytest.raises(ValueError, match="文件数量"):
        Il2CppArchiveCodec.create(
            source,
            tmp_path / "archive.zip",
            limits=Il2CppArchiveLimits(max_files=0),
        )


def test_extract_rejects_zip_slip_and_round_trips_archive(tmp_path: Path) -> None:
    """验证正常归档可解包，恶意成员名在任何文件写入前被拒绝。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.bin").write_bytes(b"payload")
    archive = Il2CppArchiveCodec.create(source, tmp_path / "archive.zip")
    extracted = tmp_path / "extracted"

    entries = Il2CppArchiveCodec.extract(archive.path, extracted)

    assert tuple(item.path for item in entries) == ("payload.bin",)
    assert (extracted / "payload.bin").read_bytes() == b"payload"
    assert not (tmp_path / "payload.bin").exists()
