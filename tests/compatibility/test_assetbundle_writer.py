"""验证 AssetBundle 数据库 Writer 的索引、依赖、Redirect 和 Golden 字节。"""

from pathlib import Path

import pytest

from compatibility.assetbundle_dto import (
    AssetBundleDatabase,
    AssetBundleRecord,
    AssetBundleRedirectRecord,
)
from compatibility.assetbundle_writer import LegacyAssetBundleDbWriter
from compatibility.line_endings import LineEnding
from core.errors import CompatibilityError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "compatibility" / "synthetic" / "assetbundle_db"


def record(
    name: str,
    *,
    dependencies: tuple[str, ...] = (),
    redirect: AssetBundleRedirectRecord | None = None,
) -> AssetBundleRecord:
    """使用私有测试工厂构造已校验 AssetBundle 记录。"""
    return AssetBundleRecord._create(name=name, dependencies=dependencies, redirect=redirect)


def database(records: tuple[AssetBundleRecord, ...]) -> AssetBundleDatabase:
    """使用私有测试工厂构造已校验数据库。"""
    return AssetBundleDatabase._create(database_name="assetbundledb_scene.txt", records=records)


def test_ab_writer_assigns_indexes_by_utf8_name_bytes() -> None:
    """验证记录按 UTF-8 名称排序并从零连续编号。"""
    output = LegacyAssetBundleDbWriter(LineEnding.LF).write(
        database((record("scene/z.assetbundle"), record("scene/a.assetbundle")))
    )
    assert output.splitlines() == [b"scene/a.assetbundle\t0", b"scene/z.assetbundle\t1"]


def test_ab_writer_encodes_dependency_indexes_in_original_order_with_duplicates() -> None:
    """验证依赖索引保留输入顺序与重复项。"""
    output = LegacyAssetBundleDbWriter(LineEnding.LF).write(
        database(
            (
                record("character/a.assetbundle", dependencies=("character/b.assetbundle",) * 2),
                record("character/b.assetbundle"),
            )
        )
    )
    assert b"\tDepend:1\t1\n" in output


def test_ab_writer_encodes_redirect_container_offset_and_length() -> None:
    """验证 Redirect 精确编码容器索引、偏移和长度。"""
    output = LegacyAssetBundleDbWriter(LineEnding.LF).write(
        database(
            (
                record(
                    "scene/a.assetbundle",
                    redirect=AssetBundleRedirectRecord._create(
                        container_name="scene/redirect/redirect_0.assetbundle",
                        offset=0,
                        length=3,
                    ),
                ),
                record("scene/redirect/redirect_0.assetbundle"),
            )
        )
    )
    assert b"\tRedirect:1\t0\t3\n" in output


def test_ab_writer_rejects_dependency_cycles_before_emitting_bytes() -> None:
    """验证自环和多节点循环在输出前失败。"""
    self_cycle = database((record("scene/a.assetbundle", dependencies=("scene/a.assetbundle",)),))
    with pytest.raises(CompatibilityError):
        LegacyAssetBundleDbWriter(LineEnding.LF).write(self_cycle)
    cycle = database(
        (
            record("scene/a.assetbundle", dependencies=("scene/b.assetbundle",)),
            record("scene/b.assetbundle", dependencies=("scene/a.assetbundle",)),
        )
    )
    with pytest.raises(CompatibilityError):
        LegacyAssetBundleDbWriter(LineEnding.LF).write(cycle)


def test_ab_writer_matches_synthetic_database_golden_bytes() -> None:
    """验证四个合成 AssetBundle 数据库 Golden 的完整 bytes。"""
    cases = (
        (
            "assetbundledb_scene_lf.txt",
            LineEnding.LF,
            database(
                (
                    record("depend/shader_scene.assetbundle"),
                    record(
                        "scene/a.assetbundle", dependencies=("depend/shader_scene.assetbundle",)
                    ),
                )
            ),
        ),
        (
            "assetbundledb_scene_crlf.txt",
            LineEnding.CRLF,
            database(
                (
                    record("depend/shader_scene.assetbundle"),
                    record(
                        "scene/a.assetbundle", dependencies=("depend/shader_scene.assetbundle",)
                    ),
                )
            ),
        ),
        (
            "assetbundledb_character_ordered_duplicates_lf.txt",
            LineEnding.LF,
            database(
                (
                    record(
                        "character/a.assetbundle", dependencies=("character/b.assetbundle",) * 2
                    ),
                    record("character/b.assetbundle"),
                )
            ),
        ),
        (
            "assetbundledb_scene_redirect_lf.txt",
            LineEnding.LF,
            database(
                (
                    record(
                        "scene/a.assetbundle",
                        redirect=AssetBundleRedirectRecord._create(
                            container_name="scene/redirect/redirect_0.assetbundle",
                            offset=0,
                            length=3,
                        ),
                    ),
                    record("scene/redirect/redirect_0.assetbundle"),
                )
            ),
        ),
    )
    for name, ending, value in cases:
        assert LegacyAssetBundleDbWriter(ending).write(value) == (FIXTURES / name).read_bytes()
