"""验证五类 AssetBundle 数据库路径路由和独立引用完整性。"""

import pytest

from compatibility.assetbundle_routing import (
    DATABASE_CHARACTER,
    DATABASE_ORDER,
    DATABASE_OTHER,
    DATABASE_PARTICLE,
    DATABASE_SCENE,
    DATABASE_UI,
    client_databases_from_release_snapshot,
    database_names_for_path,
)
from core.errors import CompatibilityError
from release.entries import ResourceVariant
from release.snapshots import ReleaseSnapshot

from .conftest import release_entry


def test_database_names_for_path_matches_legacy_ownership_and_shared_dependencies() -> None:
    """验证五类前缀、global 和 shader shared depend 的固定归属。"""
    assert database_names_for_path("scene/a.assetbundle") == (DATABASE_SCENE,)
    assert database_names_for_path("story/a.assetbundle") == (DATABASE_SCENE,)
    assert database_names_for_path("lingren/resources/global/a.assetbundle") == (DATABASE_SCENE,)
    assert database_names_for_path("ui/a.assetbundle") == (DATABASE_UI,)
    assert database_names_for_path("texture/a.assetbundle") == (DATABASE_OTHER,)
    assert database_names_for_path("skill/a.assetbundle") == (DATABASE_OTHER,)
    assert database_names_for_path("character/a.assetbundle") == (DATABASE_CHARACTER,)
    assert database_names_for_path("particle/a.assetbundle") == (DATABASE_PARTICLE,)
    assert database_names_for_path("depend/shader_scene.assetbundle") == DATABASE_ORDER
    with pytest.raises(CompatibilityError):
        database_names_for_path("depend/other.assetbundle")
    with pytest.raises(CompatibilityError):
        database_names_for_path("unknown/a.assetbundle")


def test_aggregate_client_databases_builds_five_local_index_spaces() -> None:
    """验证聚合结果固定五库，空库存在且共享 shader 可解析。"""
    snapshot = ReleaseSnapshot.create(
        ResourceVariant.MAIN,
        (
            release_entry("depend/shader_scene.assetbundle"),
            release_entry("scene/a.assetbundle", dependencies=("depend/shader_scene.assetbundle",)),
            release_entry("character/a.assetbundle"),
        ),
    )
    databases = client_databases_from_release_snapshot(snapshot)
    assert tuple(database.database_name for database in databases) == DATABASE_ORDER
    by_name = {database.database_name: database for database in databases}
    assert [record.name for record in by_name[DATABASE_SCENE].records] == [
        "depend/shader_scene.assetbundle",
        "scene/a.assetbundle",
    ]
    assert [record.name for record in by_name[DATABASE_CHARACTER].records] == [
        "depend/shader_scene.assetbundle",
        "character/a.assetbundle",
    ]
    assert [record.name for record in by_name[DATABASE_UI].records] == [
        "depend/shader_scene.assetbundle",
    ]
