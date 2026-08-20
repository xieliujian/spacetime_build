"""实现旧客户端五类 AssetBundle 数据库的确定性路径归属。

路由是纯路径规则：scene/story/global、ui、texture/skill、character、particle
分别归属固定数据库；shader depend 记录共享到五库。模块不读取历史数据库文件，
也不把未有证据的其他 depend 路径默认为共享依赖。
"""

from __future__ import annotations

from compatibility.assetbundle_dto import (
    AssetBundleDatabase,
    AssetBundleRecord,
    assetbundle_records_from_release_snapshot,
)
from compatibility.file_list_dto import _validate_path
from core.errors import CompatibilityError
from release.snapshots import ReleaseSnapshot

DATABASE_SCENE = "assetbundledb_scene.txt"
DATABASE_UI = "assetbundledb_ui.txt"
DATABASE_OTHER = "assetbundledb_other.txt"
DATABASE_CHARACTER = "assetbundledb_character.txt"
DATABASE_PARTICLE = "assetbundledb_particle.txt"
DATABASE_ORDER = (
    DATABASE_SCENE,
    DATABASE_UI,
    DATABASE_OTHER,
    DATABASE_CHARACTER,
    DATABASE_PARTICLE,
)


def database_names_for_path(logical_path: str) -> tuple[str, ...]:
    """返回一个 AssetBundle 路径对应的固定数据库集合。

    参数：
        logical_path: 客户端逻辑 AssetBundle 路径。

    返回：
        一个或多个固定数据库文件名，shader depend 返回五库元组。

    异常：
        未知前缀、未证实的 depend 路径或非法路径时抛出 ``CompatibilityError``。

    约束与副作用：
        纯函数，不访问真实历史数据库和文件系统。
    """
    path = _validate_path(logical_path, field_name="assetbundle.logical_path")
    if path.startswith("depend/shader_") and path.endswith(".assetbundle"):
        return DATABASE_ORDER
    if path.startswith("depend/"):
        raise CompatibilityError(f"尚无证据的 depend 路径归属: {path!r}")
    if path.startswith(("scene/", "story/", "lingren/resources/global/")):
        return (DATABASE_SCENE,)
    if path.startswith("ui/"):
        return (DATABASE_UI,)
    if path.startswith(("texture/", "skill/")):
        return (DATABASE_OTHER,)
    if path.startswith("character/"):
        return (DATABASE_CHARACTER,)
    if path.startswith("particle/"):
        return (DATABASE_PARTICLE,)
    raise CompatibilityError(f"未知 AssetBundle 路径前缀: {path!r}")


def client_databases_from_release_snapshot(
    snapshot: ReleaseSnapshot,
) -> tuple[AssetBundleDatabase, ...]:
    """从一个快照聚合固定顺序的五个客户端数据库。

    参数：
        snapshot: 已验证的单变体 ReleaseSnapshot。

    返回：
        按 scene、ui、other、character、particle 顺序排列的五个数据库，空库也保留。

    异常：
        快照类型、路径归属或某个独立数据库引用不完整时抛出 ``CompatibilityError``。

    约束与副作用：
        共享 shader 记录复制到每个数据库；只在内存中聚合，不写协议文件。
    """
    if not isinstance(snapshot, ReleaseSnapshot):
        raise CompatibilityError("client_databases_from_release_snapshot 只接受 ReleaseSnapshot")
    records = assetbundle_records_from_release_snapshot(snapshot)
    grouped: dict[str, list[AssetBundleRecord]] = {name: [] for name in DATABASE_ORDER}
    for record in records:
        for database_name in database_names_for_path(record.name):
            grouped[database_name].append(record)
    return tuple(
        AssetBundleDatabase._create(
            database_name=database_name,
            records=tuple(grouped[database_name]),
        )
        for database_name in DATABASE_ORDER
    )
