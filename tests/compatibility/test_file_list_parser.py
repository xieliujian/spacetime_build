"""验证六字段文件列表 Parser 的 round-trip 与严格拒绝规则。"""

from pathlib import Path

import pytest

from compatibility.file_list_parser import LegacyFileListParser
from compatibility.line_endings import LineEnding
from core.errors import CompatibilityError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "compatibility" / "synthetic" / "file_list"


def test_parser_round_trips_all_file_list_goldens() -> None:
    """验证四个独立 Golden 均能被对应换行策略解析。"""
    cases = (
        ("file_list_main_lf.txt", LineEnding.LF),
        ("file_list_main_crlf.txt", LineEnding.CRLF),
        ("file_list_historical_url_lf.txt", LineEnding.LF),
        ("file_list_low_lf.txt", LineEnding.LF),
    )
    for name, ending in cases:
        rows = LegacyFileListParser(ending).parse(
            (FIXTURES / name).read_bytes(), expected_list_version=123
        )
        assert rows
        assert all(row.file_version == 123 for row in rows)


def test_parser_rejects_bom_mixed_newlines_bad_fields_and_invalid_values() -> None:
    """验证 BOM、混合换行、错误字段、非法整数、MD5、路径和未终止行均失败。"""
    parser = LegacyFileListParser(LineEnding.LF)
    valid = b"a/file\t1\t0\t" + b"a" * 32 + b"\t1/a\t0\n"
    invalid_values = (
        b"\xef\xbb\xbfa/file\t1\t0\t" + b"a" * 32 + b"\t1/a\t0\n",
        valid.replace(b"\n", b"\r\n"),
        valid.replace(b"\t0\n", b"\n"),
        valid.replace(b"\t1\t0\t", b"\t01\t0\t"),
        valid.replace(b"\t1\t0\t", b"\t+1\t0\t"),
        valid.replace(b"\t1\t0\t", b"\t1\t-1\t"),
        valid.replace(b"a/file", b"a/../file"),
        valid.replace(b"a/file", b"a/file\tother"),
        valid[:-1],
        b"a/file\t1\t0\t" + b"A" * 32 + b"\t1/a\t0\n",
    )
    for data in invalid_values:
        with pytest.raises(CompatibilityError):
            parser.parse(data)
    with pytest.raises(CompatibilityError):
        parser.parse(b"a/file\t1\t0\t" + b"a" * 32 + b"\t1/a\t0\n" + valid)


def test_parser_requires_every_row_to_use_current_list_version() -> None:
    """验证 expected_list_version 会拒绝版本不一致但保留历史 URL。"""
    data = b"script/hotfix.lua\t99\t20\t" + b"b" * 32 + b"\t99/script/hotfix.lua\t3\n"
    with pytest.raises(CompatibilityError):
        LegacyFileListParser(LineEnding.LF).parse(data, expected_list_version=123)
    row = LegacyFileListParser(LineEnding.LF).parse(data)[0]
    assert row.file_url == "99/script/hotfix.lua"
