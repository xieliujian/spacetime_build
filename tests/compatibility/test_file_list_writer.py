"""验证六字段文件列表 Writer 的排序、换行和 Golden 字节契约。"""

from pathlib import Path

import pytest

from compatibility.file_list_dto import file_list_rows_from_manifest
from compatibility.file_list_writer import LegacyFileListWriter
from compatibility.line_endings import LineEnding
from core.errors import CompatibilityError

from .conftest import manifest, release_entry

FIXTURES = Path(__file__).parents[1] / "fixtures" / "compatibility" / "synthetic" / "file_list"


def test_writer_emits_six_fields_sorted_by_utf8_filename_bytes() -> None:
    """验证每行六列并按逻辑文件名 UTF-8 bytes 排序。"""
    payload = manifest(
        (
            release_entry(
                "z/file",
                artifact_class=__import__(
                    "release.snapshots", fromlist=["ReleaseArtifactClass"]
                ).ReleaseArtifactClass.REGULAR_FILE,
            ),
            release_entry(
                "a/file",
                artifact_class=__import__(
                    "release.snapshots", fromlist=["ReleaseArtifactClass"]
                ).ReleaseArtifactClass.REGULAR_FILE,
            ),
        )
    )
    rows = file_list_rows_from_manifest(payload)
    output = LegacyFileListWriter(LineEnding.LF).write(rows)
    assert output.decode().splitlines()[0].startswith("a/file\t")
    assert all(line.count("\t") == 5 for line in output.decode().splitlines())


def test_writer_uses_selected_line_ending_and_terminal_newline() -> None:
    """验证空输入为空 bytes，非空输入只使用指定换行并以换行结束。"""
    payload = manifest((release_entry("scene/a.assetbundle"),))
    rows = file_list_rows_from_manifest(payload)
    assert LegacyFileListWriter(LineEnding.LF).write(rows).endswith(b"\n")
    assert b"\r\n" in LegacyFileListWriter(LineEnding.CRLF).write(rows)
    assert LegacyFileListWriter(LineEnding.LF).write(()) == b""


def test_writer_matches_file_list_golden_bytes() -> None:
    """验证主清 LF/CRLF、历史 URL 和低清 Golden 的完整 bytes。"""
    main_payload = manifest(
        (
            release_entry(
                "config/空 格.txt",
                artifact_class=__import__(
                    "release.snapshots", fromlist=["ReleaseArtifactClass"]
                ).ReleaseArtifactClass.REGULAR_FILE,
                source_size=12,
                transfer_size=12,
                source_sha="a" * 64,
                transfer_sha="b" * 64,
            ),
        )
    )
    historical_payload = manifest(
        (
            release_entry(
                "script/hotfix.lua",
                artifact_class=__import__(
                    "release.snapshots", fromlist=["ReleaseArtifactClass"]
                ).ReleaseArtifactClass.REGULAR_FILE,
                object_origin=__import__(
                    "release.entries", fromlist=["ReleaseObjectOrigin"]
                ).ReleaseObjectOrigin.HISTORICAL,
                object_version="99",
                file_url="99/script/hotfix.lua",
                source_size=20,
                transfer_size=20,
                source_sha="b" * 64,
                transfer_sha="b" * 64,
                source_md5="2" * 32,
                subpackage_flag=3,
            ),
        )
    )
    low_payload = manifest(
        (
            release_entry(
                "scene/a.assetbundle",
                variant=__import__(
                    "release.entries", fromlist=["ResourceVariant"]
                ).ResourceVariant.LOW,
                artifact_class=__import__(
                    "release.snapshots", fromlist=["ReleaseArtifactClass"]
                ).ReleaseArtifactClass.REGULAR_FILE,
                object_version="123_low",
                source_size=10,
                transfer_size=10,
                source_sha="b" * 64,
                transfer_sha="b" * 64,
                source_md5="3" * 32,
                subpackage_flag=1,
            ),
        ),
        variant=__import__("release.entries", fromlist=["ResourceVariant"]).ResourceVariant.LOW,
    )
    cases = (
        ("file_list_main_lf.txt", LegacyFileListWriter(LineEnding.LF), main_payload),
        ("file_list_main_crlf.txt", LegacyFileListWriter(LineEnding.CRLF), main_payload),
        (
            "file_list_historical_url_lf.txt",
            LegacyFileListWriter(LineEnding.LF),
            historical_payload,
        ),
        ("file_list_low_lf.txt", LegacyFileListWriter(LineEnding.LF), low_payload),
    )
    for name, writer, payload in cases:
        assert writer.write(file_list_rows_from_manifest(payload)) == (FIXTURES / name).read_bytes()


def test_writer_rejects_non_dto_rows() -> None:
    """验证 Writer 不接受散装对象，保持协议入口类型边界。"""
    with pytest.raises(CompatibilityError):
        LegacyFileListWriter(LineEnding.LF).write((object(),))  # type: ignore[arg-type]
