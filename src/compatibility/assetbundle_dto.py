"""从 ReleaseSnapshot 生成旧 AssetBundle 数据库的不可变 DTO。

本模块只转换含 ``ASSET_BUNDLE_DATABASE`` membership 的快照条目；普通文件不会进入
数据库。Redirect slice 保留依赖和容器切片，容器自身作为普通 AssetBundle 记录出现。
三类 DTO 都由模块私有工厂创建，Writer 和路由只能消费已经验证的数据库对象。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import CompatibilityError
from release.snapshots import ReleaseMembership, ReleaseSnapshot

from compatibility.file_list_dto import _validate_int, _validate_path, _validate_text

_RECORD_TOKEN = object()
_REDIRECT_TOKEN = object()
_DATABASE_TOKEN = object()


def _bind_fields(instance: object, values: dict[str, object]) -> None:
    """向隐藏工厂创建的冻结 DTO 写入字段。

    参数：
        instance: 通过 ``object.__new__`` 创建的 DTO 实例。
        values: 字段名到值的映射。

    返回：
        ``None``。

    异常：
        字段绑定失败时由 Python 抛出异常。

    约束与副作用：
        仅在内存中绑定不可变 DTO，不提供公开构造入口。
    """
    for name, value in values.items():
        object.__setattr__(instance, name, value)


@dataclass(frozen=True, slots=True, init=False)
class AssetBundleRedirectRecord:
    """记录一个 AssetBundle 到 Redirect 容器的字节切片。

    参数：
        container_name: 容器逻辑路径。
        offset: 非负 Int32 字节偏移。
        length: 正 Int32 字节长度。

    返回：
        无；由 ``AssetBundleRecord`` 隐藏工厂绑定。

    异常：
        直接构造或替换时抛出 ``TypeError``；内部字段非法时抛出
        ``CompatibilityError``。

    约束与副作用：
        不读 Blob 字节，不访问文件系统。
    """

    container_name: str
    offset: int
    length: int

    @staticmethod
    def _create(*, container_name: str, offset: int, length: int) -> AssetBundleRedirectRecord:
        """创建并校验 Redirect DTO。

        参数：
            container_name: 合法客户端容器路径。
            offset: 非负 Int32 偏移。
            length: 正 Int32 长度。

        返回：
            不可变 Redirect 记录。

        异常：
            字段非法时抛出 ``CompatibilityError``。

        约束与副作用：
            纯内存构造。
        """
        name = _validate_path(container_name, field_name="redirect.container_name")
        offset_value = _validate_int(offset, field_name="redirect.offset")
        length_value = _validate_int(length, field_name="redirect.length", positive=True)
        record = object.__new__(AssetBundleRedirectRecord)
        _bind_fields(
            record,
            {"container_name": name, "offset": offset_value, "length": length_value},
        )
        return record


@dataclass(frozen=True, slots=True, init=False)
class AssetBundleRecord:
    """旧 AssetBundle 数据库中的一条记录。

    参数：
        name: AssetBundle 逻辑路径。
        dependencies: 有序、可重复的依赖逻辑路径元组。
        redirect: 可选容器切片。

    返回：
        无；只能由快照转换工厂创建。

    异常：
        直接构造或替换时抛出 ``TypeError``；字段非法时抛出
        ``CompatibilityError``。

    约束与副作用：
        依赖顺序和重复必须原样保留，不读写外部系统。
    """

    name: str
    dependencies: tuple[str, ...]
    redirect: AssetBundleRedirectRecord | None

    @staticmethod
    def _create(
        *,
        name: str,
        dependencies: tuple[str, ...],
        redirect: AssetBundleRedirectRecord | None,
    ) -> AssetBundleRecord:
        """创建并校验单条 AssetBundle 记录。

        参数：
            name: 合法逻辑路径。
            dependencies: 有序依赖。
            redirect: 可选 Redirect 切片。

        返回：
            不可变记录。

        异常：
            路径、依赖容器或类型非法时抛出 ``CompatibilityError``。

        约束与副作用：
            只做结构校验，不验证跨记录引用。
        """
        record_name = _validate_path(name, field_name="assetbundle.name")
        if not isinstance(dependencies, tuple):
            raise CompatibilityError("assetbundle.dependencies 必须是 tuple")
        dependency_names = tuple(
            _validate_path(item, field_name="assetbundle.dependency") for item in dependencies
        )
        if redirect is not None and not isinstance(redirect, AssetBundleRedirectRecord):
            raise CompatibilityError("assetbundle.redirect 必须是 Redirect DTO 或 None")
        record = object.__new__(AssetBundleRecord)
        _bind_fields(
            record,
            {
                "name": record_name,
                "dependencies": dependency_names,
                "redirect": redirect,
            },
        )
        return record


@dataclass(frozen=True, slots=True, init=False)
class AssetBundleDatabase:
    """一个客户端 AssetBundle 数据库索引空间。

    参数：
        database_name: 旧数据库文件名，例如 ``assetbundledb_scene.txt``。
        records: 该索引空间内的不可变 AssetBundle 记录元组。

    返回：
        无；由五库聚合工厂创建。

    异常：
        直接构造或替换时抛出 ``TypeError``；重复名称、缺失依赖或缺失 Redirect
        容器时抛出 ``CompatibilityError``。

    约束与副作用：
        一个数据库是独立解析空间；跨库依赖必须由路由把共享记录复制到各库。
    """

    database_name: str
    records: tuple[AssetBundleRecord, ...]

    @staticmethod
    def _create(
        *,
        database_name: str,
        records: tuple[AssetBundleRecord, ...],
    ) -> AssetBundleDatabase:
        """创建并校验一个数据库索引空间。

        参数：
            database_name: 非空数据库文件名。
            records: 已由快照转换工厂产生的记录元组。

        返回：
            通过本库引用完整性校验的数据库。

        异常：
            类型、重复名称、缺失依赖或 Redirect 容器时抛出 ``CompatibilityError``。

        约束与副作用：
            只做内存校验，不排序、不编码文本。
        """
        name = _validate_text(database_name, field_name="database_name")
        if not isinstance(records, tuple):
            raise CompatibilityError("database.records 必须是 tuple")
        if not all(isinstance(item, AssetBundleRecord) for item in records):
            raise CompatibilityError("database.records 的每一项必须是 AssetBundleRecord")
        by_name = {record.name: record for record in records}
        if len(by_name) != len(records):
            raise CompatibilityError("AssetBundle 数据库内逻辑路径重复")
        for record in records:
            for dependency in record.dependencies:
                if dependency not in by_name:
                    raise CompatibilityError(
                        f"AssetBundle 依赖不在数据库内: {record.name!r} -> {dependency!r}"
                    )
            if record.redirect is not None and record.redirect.container_name not in by_name:
                raise CompatibilityError(
                    f"Redirect 容器不在数据库内: {record.name!r} -> "
                    f"{record.redirect.container_name!r}"
                )
        database = object.__new__(AssetBundleDatabase)
        _bind_fields(database, {"database_name": name, "records": records})
        return database


def assetbundle_records_from_release_snapshot(
    snapshot: ReleaseSnapshot,
) -> tuple[AssetBundleRecord, ...]:
    """从已校验快照单向转换 AssetBundle 记录。

    参数：
        snapshot: 已由 ``ReleaseSnapshot.create`` 创建的单变体快照。

    返回：
        按快照原顺序排列的不可变 AssetBundle 记录元组。

    异常：
        输入类型或协议路径、依赖、Redirect 字段非法时抛出 ``CompatibilityError``。

    约束与副作用：
        仅转换带 ``ASSET_BUNDLE_DATABASE`` membership 的条目，不复制领域校验，
        不访问 Blob 字节和外部系统。
    """
    if not isinstance(snapshot, ReleaseSnapshot):
        raise CompatibilityError("assetbundle_records_from_release_snapshot 只接受 ReleaseSnapshot")
    records: list[AssetBundleRecord] = []
    for snapshot_entry in snapshot.entries:
        if ReleaseMembership.ASSET_BUNDLE_DATABASE not in snapshot_entry.memberships:
            continue
        redirect = None
        if snapshot_entry.redirect_slice is not None:
            slice_info = snapshot_entry.redirect_slice
            redirect = AssetBundleRedirectRecord._create(
                container_name=slice_info.container_logical_path,
                offset=slice_info.offset,
                length=slice_info.length,
            )
        records.append(
            AssetBundleRecord._create(
                name=snapshot_entry.release_entry.logical_path,
                dependencies=snapshot_entry.assetbundle_dependencies,
                redirect=redirect,
            )
        )
    return tuple(records)
