"""验证 AssetBundle 数据库 Parser 的 Golden round-trip 与严格拒绝规则。"""

from pathlib import Path

import pytest

from compatibility.assetbundle_parser import LegacyAssetBundleDbParser
from compatibility.line_endings import LineEnding
from core.errors import CompatibilityError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "compatibility" / "synthetic" / "assetbundle_db"


def test_ab_parser_reads_all_synthetic_goldens_without_reconstructing_writer_dto() -> None:
    """验证四个 Golden 可解析并保留依赖重复与 Redirect 字段。"""
    cases = (
        ("assetbundledb_scene_lf.txt", LineEnding.LF),
        ("assetbundledb_scene_crlf.txt", LineEnding.CRLF),
        ("assetbundledb_character_ordered_duplicates_lf.txt", LineEnding.LF),
        ("assetbundledb_scene_redirect_lf.txt", LineEnding.LF),
    )
    for name, ending in cases:
        database = LegacyAssetBundleDbParser(ending).parse((FIXTURES / name).read_bytes())
        assert database.records
    duplicate = LegacyAssetBundleDbParser(LineEnding.LF).parse(
        (FIXTURES / "assetbundledb_character_ordered_duplicates_lf.txt").read_bytes()
    )
    assert duplicate.records[0].dependencies == ("character/b.assetbundle",) * 2
    redirected = LegacyAssetBundleDbParser(LineEnding.LF).parse(
        (FIXTURES / "assetbundledb_scene_redirect_lf.txt").read_bytes()
    )
    assert redirected.records[0].redirect is not None
    assert redirected.records[0].redirect.container_index == 1


def test_ab_parser_rejects_bad_indexes_targets_lines_encoding_and_cycles() -> None:
    """验证主索引、子行顺序、引用、换行、UTF-8 和依赖环错误均失败。"""
    parser = LegacyAssetBundleDbParser(LineEnding.LF)
    invalid_values = (
        b"\tDepend:0\n",
        b"scene/a.assetbundle\t1\n",
        b"scene/a.assetbundle\t0\n\tDepend:\n",
        b"scene/a.assetbundle\t0\n\tDepend:01\n",
        b"scene/a.assetbundle\t0\n\tDepend:1\n",
        b"scene/a.assetbundle\t0\n\tRedirect:1\t0\t0\n",
        b"scene/a.assetbundle\t0\n\tRedirect:1\t0\t1\n",
        b"scene/a.assetbundle\t0\n\tRedirect:1\t0\t1\n\tDepend:0\n",
        b"scene/a.assetbundle\t0\n\tDepend:0\n\n",
        b"scene/a.assetbundle\t0\n"[:-1],
        b"\xef\xbb\xbfscene/a.assetbundle\t0\n",
        b"scene/a.assetbundle\t0\r\n",
        b"scene/a.assetbundle\t0\n\xff\n",
        b"scene/a.assetbundle\t0\nscene/b.assetbundle\t1\n\tDepend:0\n\tDepend:0\n",
    )
    for data in invalid_values:
        with pytest.raises(CompatibilityError):
            parser.parse(data)
    cycle = b"scene/a.assetbundle\t0\n\tDepend:1\nscene/b.assetbundle\t1\n\tDepend:0\n"
    with pytest.raises(CompatibilityError):
        parser.parse(cycle)
