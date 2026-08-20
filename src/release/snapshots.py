"""协议无关发布快照：分类、membership、AB 依赖与 Redirect 切片。

本模块提供 ``ReleaseArtifactClass``、``ReleaseMembership``、``RedirectSlice``、
``ReleaseSnapshotEntry`` 与 ``ReleaseSnapshot``，用于在 compatibility 之前表达
完整发布快照。快照锁定单一 ``ResourceVariant``（从 ``entries`` 导入），保留有序
重复 AB 依赖，并校验 Redirect 容器交叉引用与分类-membership 组合。本模块不生成
AB 数据库索引、``Depend:``/``Redirect:`` 文本或换行规则。导入本模块不执行构建
或发布。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import cast

from core.artifacts import BlobRef
from core.errors import PublishError
from release.entries import ReleaseEntry, ResourceVariant

_INT32_MAX = 2**31 - 1
_RELEASE_SNAPSHOT_FACTORY_TOKEN = object()


class ReleaseArtifactClass(Enum):
    """发布快照中的协议无关产物分类。

    职责：
        区分普通文件、未 Redirect 的 AssetBundle、被 Redirect 替代的切片，以及
        Redirect 容器，驱动 membership 与交叉引用校验。

    参数：
        枚举成员无额外构造参数；值为稳定字符串标签。

    返回：
        无；通过 ``ReleaseArtifactClass.<NAME>`` 取值。

    异常：
        无；非法名称访问由 ``Enum`` 标准机制报错。

    约束与副作用：
        不编码旧客户端协议字段；无外部副作用。
    """

    REGULAR_FILE = "regular_file"
    ASSET_BUNDLE = "asset_bundle"
    REDIRECT_SLICE = "redirect_slice"
    REDIRECT_CONTAINER = "redirect_container"


class ReleaseMembership(Enum):
    """发布快照条目的发布成员资格。

    职责：
        显式声明条目是否进入文件列表与/或 AssetBundle 数据库；compatibility
        只能按本集合从已验证快照单向生成 DTO。

    参数：
        枚举成员无额外构造参数；值为稳定字符串标签。

    返回：
        无；通过 ``ReleaseMembership.<NAME>`` 取值。

    异常：
        无；非法名称访问由 ``Enum`` 标准机制报错。

    约束与副作用：
        不直接生成协议文本；无外部副作用。
    """

    FILE_LIST = "file_list"
    ASSET_BUNDLE_DATABASE = "asset_bundle_database"


@dataclass(frozen=True, slots=True)
class RedirectSlice:
    """被 Redirect 替代的原 AB 在容器中的字节切片。

    职责：
        记录容器逻辑路径、容器 Blob、偏移与长度，供快照交叉引用校验；不生成
        ``Redirect:`` 文本行。

    参数：
        container_logical_path: 容器在快照中的逻辑路径。
        container: 容器内容的持久 ``BlobRef``。
        offset: 非负 Int32 起始偏移。
        length: 正 Int32 切片长度。

    返回：
        无；本类为不可变数据载体。

    异常：
        路径为空、Blob 非法类型，或 offset/length 越界类型时抛出 ``PublishError``。
        ``BlobRef`` 自身非法时抛出 ``ArtifactValidationError``。

    约束与副作用：
        ``frozen=True, slots=True``；完整越界与容器存在性在 ``ReleaseSnapshot.create``
        中校验。无 I/O。
    """

    container_logical_path: str
    container: BlobRef
    offset: int
    length: int

    def __post_init__(self) -> None:
        """构造后校验切片字段基础类型与非负边界。

        参数：
            无；读取实例字段。

        返回：
            ``None``。

        异常：
            字段类型或基础边界非法时抛出 ``PublishError``。

        约束与副作用：
            仅内存校验；相对容器大小的越界在快照组装时再验。
        """
        container_path = cast(object, self.container_logical_path)
        if not isinstance(container_path, str) or container_path == "":
            raise PublishError("container_logical_path 不得为空")
        container = cast(object, self.container)
        if not isinstance(container, BlobRef):
            raise PublishError("container 必须是 BlobRef")
        offset = cast(object, self.offset)
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise PublishError("offset 必须是 int")
        if offset < 0 or offset > _INT32_MAX:
            raise PublishError(f"offset 必须是非负 Int32，实际为 {offset!r}")
        length = cast(object, self.length)
        if not isinstance(length, int) or isinstance(length, bool):
            raise PublishError("length 必须是 int")
        if length <= 0 or length > _INT32_MAX:
            raise PublishError(f"length 必须是正 Int32，实际为 {length!r}")


@dataclass(frozen=True, slots=True)
class ReleaseSnapshotEntry:
    """快照中的单条协议无关发布记录。

    职责：
        将 ``ReleaseEntry`` 与产物分类、membership、有序 AB 依赖及可选 Redirect
        切片绑定；供 ``ReleaseSnapshot.create`` 做集合级校验。

    参数：
        release_entry: 底层发布条目。
        artifact_class: ``ReleaseArtifactClass`` 分类。
        memberships: ``frozenset[ReleaseMembership]`` 成员资格。
        assetbundle_dependencies: 有序依赖逻辑路径元组；可含重复。
        redirect_slice: ``REDIRECT_SLICE`` 时必须提供；其他分类应为 ``None``。

    返回：
        无；本类为不可变数据载体。

    异常：
        字段类型非法时抛出 ``PublishError``。分类-membership 组合在快照创建时校验。

    约束与副作用：
        ``frozen=True, slots=True``；依赖保留顺序与重复。无 I/O。
    """

    release_entry: ReleaseEntry
    artifact_class: ReleaseArtifactClass
    memberships: frozenset[ReleaseMembership]
    assetbundle_dependencies: tuple[str, ...]
    redirect_slice: RedirectSlice | None

    def __post_init__(self) -> None:
        """构造后校验条目字段类型与依赖元组结构。

        参数：
            无；读取实例字段。

        返回：
            ``None``。

        异常：
            任一字段类型不合法时抛出 ``PublishError``。

        约束与副作用：
            仅单条结构校验；跨条目不变量由 ``ReleaseSnapshot.create`` 负责。
        """
        release_entry = cast(object, self.release_entry)
        if not isinstance(release_entry, ReleaseEntry):
            raise PublishError("release_entry 必须是 ReleaseEntry")
        artifact_class = cast(object, self.artifact_class)
        if not isinstance(artifact_class, ReleaseArtifactClass):
            raise PublishError("artifact_class 必须是 ReleaseArtifactClass")
        memberships = cast(object, self.memberships)
        if not isinstance(memberships, frozenset):
            raise PublishError("memberships 必须是 frozenset[ReleaseMembership]")
        for item in cast(frozenset[object], memberships):
            if not isinstance(item, ReleaseMembership):
                raise PublishError("memberships 的每一项必须是 ReleaseMembership")
        deps = cast(object, self.assetbundle_dependencies)
        if not isinstance(deps, tuple):
            raise PublishError("assetbundle_dependencies 必须是 tuple[str, ...]")
        for dep in cast(tuple[object, ...], deps):
            if not isinstance(dep, str) or dep == "":
                raise PublishError("assetbundle_dependencies 的每一项必须是非空 str")
        redirect_slice = cast(object, self.redirect_slice)
        if redirect_slice is not None and not isinstance(redirect_slice, RedirectSlice):
            raise PublishError("redirect_slice 必须是 RedirectSlice 或 None")


def _expected_memberships(
    artifact_class: ReleaseArtifactClass,
) -> frozenset[ReleaseMembership]:
    """返回给定分类允许且必须精确匹配的 membership 集合。

    参数：
        artifact_class: 产物分类。

    返回：
        该分类唯一合法的 ``frozenset[ReleaseMembership]``。

    异常：
        未知分类时抛出 ``PublishError``。

    约束与副作用：
        纯函数；与设计文档中的四类 membership 规则对齐。
    """
    if artifact_class is ReleaseArtifactClass.REGULAR_FILE:
        return frozenset({ReleaseMembership.FILE_LIST})
    if artifact_class is ReleaseArtifactClass.ASSET_BUNDLE:
        return frozenset(
            {
                ReleaseMembership.FILE_LIST,
                ReleaseMembership.ASSET_BUNDLE_DATABASE,
            }
        )
    if artifact_class is ReleaseArtifactClass.REDIRECT_SLICE:
        return frozenset({ReleaseMembership.ASSET_BUNDLE_DATABASE})
    if artifact_class is ReleaseArtifactClass.REDIRECT_CONTAINER:
        return frozenset(
            {
                ReleaseMembership.FILE_LIST,
                ReleaseMembership.ASSET_BUNDLE_DATABASE,
            }
        )
    raise PublishError(f"未知 ReleaseArtifactClass: {artifact_class!r}")


def _validate_entry_classification(entry: ReleaseSnapshotEntry) -> None:
    """校验单条分类与 membership、Redirect 字段组合。

    参数：
        entry: 待校验快照条目。

    返回：
        ``None``。

    异常：
        分类-membership 不匹配，或 Redirect 字段与分类矛盾时抛出 ``PublishError``。

    约束与副作用：
        纯校验；不修改入参。
    """
    expected = _expected_memberships(entry.artifact_class)
    if entry.memberships != expected:
        raise PublishError(
            "非法分类-membership 组合: "
            f"class={entry.artifact_class.value}, "
            f"memberships={sorted(m.value for m in entry.memberships)}"
        )

    if entry.artifact_class is ReleaseArtifactClass.REDIRECT_SLICE:
        if entry.redirect_slice is None:
            raise PublishError("REDIRECT_SLICE 必须提供 redirect_slice")
    elif entry.redirect_slice is not None:
        raise PublishError(f"{entry.artifact_class.value} 不得携带 redirect_slice")


def _blob_identity(blob: BlobRef) -> tuple[str, str, int]:
    """提取 Blob 身份三元组，供相等比较。

    参数：
        blob: 持久 Blob 引用。

    返回：
        ``(locator, sha256, size)``。

    异常：
        无。

    约束与副作用：
        纯函数。
    """
    return (blob.locator, blob.sha256, blob.size)


def _bind_release_snapshot(
    *,
    variant: ResourceVariant,
    entries: tuple[ReleaseSnapshotEntry, ...],
) -> ReleaseSnapshot:
    """绑定已完成校验的快照字段。

    参数：
        variant: 已校验的快照变体。
        entries: 已校验且已规范为元组的快照条目。

    返回：
        仅由 ``ReleaseSnapshot.create`` 返回的不可变快照。

    异常：
        无；调用方必须先完成全部领域校验。

    约束与副作用：
        使用模块私有 token 和 ``object.__new__``，阻止公开 dataclass 构造器绕过
        ``create``；纯内存绑定。
    """
    snapshot = object.__new__(ReleaseSnapshot)
    object.__setattr__(snapshot, "variant", variant)
    object.__setattr__(snapshot, "entries", entries)
    object.__setattr__(snapshot, "_factory_token", _RELEASE_SNAPSHOT_FACTORY_TOKEN)
    return snapshot


@dataclass(frozen=True, slots=True, init=False)
class ReleaseSnapshot:
    """锁定单一变体的协议无关完整发布快照。

    职责：
        聚合已校验的 ``ReleaseSnapshotEntry`` 元组，保证变体一致、路径唯一、
        依赖目标存在、Redirect 容器存在且 Blob/切片边界合法。

    参数：
        variant: 快照锁定的 ``ResourceVariant``。
        entries: 已通过 ``create`` 校验的条目元组。

    返回：
        无；通过 ``create`` 工厂得到实例。

    异常：
        公开应使用 ``create``；直接构造不保证不变量。

    约束与副作用：
        ``frozen=True, slots=True``；不生成兼容协议文本；无 I/O。
    """

    variant: ResourceVariant
    entries: tuple[ReleaseSnapshotEntry, ...]
    _factory_token: object = field(default=None, repr=False, compare=False)

    @staticmethod
    def create(
        variant: ResourceVariant,
        entries: tuple[ReleaseSnapshotEntry, ...] | list[ReleaseSnapshotEntry],
    ) -> ReleaseSnapshot:
        """创建并校验锁定单一变体的发布快照。

        参数：
            variant: 快照必须锁定的主/低清变体。
            entries: 快照条目序列；内部规范为元组。

        返回：
            通过全部交叉引用与 membership 校验的 ``ReleaseSnapshot``。

        异常：
            变体混入、路径重复、依赖/容器缺失、Blob 不匹配、切片越界或非法
            分类-membership 时抛出 ``PublishError``。

        约束与副作用：
            纯内存校验；依赖顺序与重复原样保留；不读写磁盘。
        """
        variant_obj = cast(object, variant)
        if not isinstance(variant_obj, ResourceVariant):
            raise PublishError("variant 必须是 ResourceVariant")
        variant = variant_obj
        entries_obj = cast(object, entries)
        if isinstance(entries_obj, list):
            entry_tuple = tuple(cast(list[object], entries_obj))
        elif isinstance(entries_obj, tuple):
            entry_tuple = cast(tuple[object, ...], entries_obj)
        else:
            raise PublishError("entries 必须是 tuple[ReleaseSnapshotEntry, ...] 或 list")

        typed_entries: list[ReleaseSnapshotEntry] = []
        for entry in entry_tuple:
            if not isinstance(entry, ReleaseSnapshotEntry):
                raise PublishError("entries 的每一项必须是 ReleaseSnapshotEntry")
            # 单 variant：任一条目混入其他变体即拒绝整张快照。
            if entry.release_entry.variant is not variant:
                raise PublishError(
                    "ReleaseSnapshot 锁定单一 ResourceVariant，"
                    f"期望 {variant.value}，"
                    f"条目 {entry.release_entry.logical_path!r} 为 "
                    f"{entry.release_entry.variant.value}"
                )
            _validate_entry_classification(entry)
            typed_entries.append(entry)
        entry_tuple = tuple(typed_entries)

        by_path: dict[str, ReleaseSnapshotEntry] = {}
        for entry in entry_tuple:
            path = entry.release_entry.logical_path
            if path in by_path:
                raise PublishError(f"快照逻辑路径重复: {path!r}")
            by_path[path] = entry

        # Redirect 容器路径在快照中必须唯一（分类层面再确认无重复容器路径）。
        container_paths = [
            item.release_entry.logical_path
            for item in entry_tuple
            if item.artifact_class is ReleaseArtifactClass.REDIRECT_CONTAINER
        ]
        if len(container_paths) != len(set(container_paths)):
            raise PublishError("Redirect 容器逻辑路径必须唯一")

        for entry in entry_tuple:
            for dep in entry.assetbundle_dependencies:
                if dep not in by_path:
                    raise PublishError(
                        f"依赖目标缺失: {dep!r} (引用方 {entry.release_entry.logical_path!r})"
                    )

            slice_info = entry.redirect_slice
            if slice_info is None:
                continue

            container_path = slice_info.container_logical_path
            if container_path not in by_path:
                raise PublishError(
                    f"Redirect 容器缺失: {container_path!r} "
                    f"(引用方 {entry.release_entry.logical_path!r})"
                )
            container_entry = by_path[container_path]
            if container_entry.artifact_class is not ReleaseArtifactClass.REDIRECT_CONTAINER:
                raise PublishError(
                    f"Redirect 容器路径 {container_path!r} 的分类必须是 REDIRECT_CONTAINER"
                )

            # 切片声明的 Blob 必须与容器条目传输身份一致。
            container_blob = container_entry.release_entry.transfer_blob
            if _blob_identity(slice_info.container) != _blob_identity(container_blob):
                raise PublishError(
                    f"RedirectSlice.container 与容器条目 transfer_blob 不匹配: {container_path!r}"
                )

            end = slice_info.offset + slice_info.length
            if end > container_blob.size:
                raise PublishError(
                    "RedirectSlice 越界: "
                    f"offset={slice_info.offset}, length={slice_info.length}, "
                    f"container_size={container_blob.size}"
                )

        return _bind_release_snapshot(variant=variant, entries=entry_tuple)
