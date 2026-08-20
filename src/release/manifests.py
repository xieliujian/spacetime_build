"""协议无关 ReleaseManifest payload 与不可变清单领域模型。

本模块提供 ``ReleaseManifestPayload`` 与仅能经工厂创建的 ``ReleaseManifest``。
payload 锁定单一 ``ResourceVariant``（从 ``entries`` 导入）、当前 FileListNo、
完整 ``ReleaseSnapshot`` 与来源 BuildManifest ID；``CURRENT_UPLOAD`` 条目的
``object_version`` 必须已展开为具体 FileListNo / ``{n}_low``。本模块不生成旧
客户端协议文本。导入本模块不执行构建或发布。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from core.errors import PublishError
from release.entries import ReleaseObjectOrigin, ResourceVariant
from release.snapshots import ReleaseSnapshot

# 当前受支持的 ReleaseManifest schema；未知版本在构造与读取时一律拒绝。
RELEASE_MANIFEST_SCHEMA_VERSION = 1

# 仅 bind_release_manifest 持有；公开调用方无法合法传入该 token。
_RELEASE_MANIFEST_FACTORY_TOKEN = object()

_INT32_MAX = 2**31 - 1


def _expected_current_object_version(variant: ResourceVariant, file_list_no: int) -> str:
    """返回给定 FileListNo 下 CURRENT_UPLOAD 必须使用的具体 object_version。

    参数：
        variant: 主清或低清变体。
        file_list_no: 当前正整数 FileListNo。

    返回：
        ``MAIN`` 返回 ``str(file_list_no)``；``LOW`` 返回 ``f"{file_list_no}_low"``。

    异常：
        未知变体时抛出 ``PublishError``。

    约束与副作用：
        纯函数；与 entries 层哨兵不同，manifest 层要求已展开具体值。
    """
    if variant is ResourceVariant.MAIN:
        return str(file_list_no)
    if variant is ResourceVariant.LOW:
        return f"{file_list_no}_low"
    raise PublishError(f"未知 ResourceVariant: {variant!r}")


@dataclass(frozen=True, slots=True)
class ReleaseManifestPayload:
    """仅含可复现内容的发布清单 payload。

    职责：
        记录 schema、单一变体、当前 FileListNo、完整快照与来源 BuildManifest
        ID 元组；供工厂计算 ``manifest_id``。字段名刻意排除 ID。

    参数：
        schema_version: payload schema 整数版本；未知版本在严格读取时拒绝。
        variant: 与 ``snapshot.variant`` 一致的 ``ResourceVariant``。
        file_list_no: 正 Int32 当前文件列表号。
        snapshot: 已校验的 ``ReleaseSnapshot``。
        source_manifest_ids: 来源 BuildManifest ID 字符串元组（无序集合语义）。

    返回：
        无；本类为不可变数据载体。

    异常：
        变体不一致、FileListNo 非法，或 ``CURRENT_UPLOAD`` 未使用具体 FileListNo
        时抛出 ``PublishError``。

    约束与副作用：
        ``frozen=True, slots=True``；不计算 ID；无 I/O。未知 schema 由读写边界拒绝。
    """

    schema_version: int
    variant: ResourceVariant
    file_list_no: int
    snapshot: ReleaseSnapshot
    source_manifest_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        """构造后校验变体锁定与 CURRENT_UPLOAD 具体版本。

        参数：
            无；读取实例字段。

        返回：
            ``None``。

        异常：
            任一不变量被违反时抛出 ``PublishError``。

        约束与副作用：
            仅内存校验；不持久化。schema 合法性留给严格读取路径。
        """
        schema_version = cast(object, self.schema_version)
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise PublishError("schema_version 必须是 int")
        variant = cast(object, self.variant)
        if not isinstance(variant, ResourceVariant):
            raise PublishError("variant 必须是 ResourceVariant")
        snapshot = cast(object, self.snapshot)
        if not isinstance(snapshot, ReleaseSnapshot):
            raise PublishError("snapshot 必须是 ReleaseSnapshot")
        file_list_no = cast(object, self.file_list_no)
        if not isinstance(file_list_no, int) or isinstance(file_list_no, bool):
            raise PublishError("file_list_no 必须是 int")
        if file_list_no <= 0 or file_list_no > _INT32_MAX:
            raise PublishError(f"file_list_no 必须是正 Int32，实际为 {file_list_no!r}")

        if self.snapshot.variant is not self.variant:
            raise PublishError(
                "snapshot.variant 必须等于 payload.variant: "
                f"payload={self.variant.value}, "
                f"snapshot={self.snapshot.variant.value}"
            )

        expected_current = _expected_current_object_version(self.variant, self.file_list_no)
        for item in self.snapshot.entries:
            entry = item.release_entry
            if entry.variant is not self.variant:
                raise PublishError(
                    f"快照条目 variant 必须与 payload.variant 一致: {entry.logical_path!r}"
                )
            if entry.list_version != self.file_list_no:
                raise PublishError(
                    "ReleaseEntry.list_version 必须等于 payload.file_list_no: "
                    f"条目 {entry.logical_path!r} 的 list_version={entry.list_version}, "
                    f"file_list_no={self.file_list_no}"
                )
            # manifest 层要求 CURRENT_UPLOAD 已展开为具体 FileListNo，禁止残留哨兵。
            if entry.object_origin is ReleaseObjectOrigin.CURRENT_UPLOAD:
                if entry.object_version != expected_current:
                    raise PublishError(
                        "CURRENT_UPLOAD 的 object_version 必须等于 "
                        f"{expected_current!r}（file_list_no={self.file_list_no}），"
                        f"条目 {entry.logical_path!r} 实际为 {entry.object_version!r}"
                    )

        ids = cast(object, self.source_manifest_ids)
        if not isinstance(ids, tuple):
            raise PublishError("source_manifest_ids 必须是 tuple[str, ...]")
        for identity in cast(tuple[object, ...], ids):
            if not isinstance(identity, str) or identity == "":
                raise PublishError("source_manifest_ids 的每一项必须是非空 str")


def bind_release_manifest(*, manifest_id: str, payload: ReleaseManifestPayload) -> ReleaseManifest:
    """将已计算的 ``manifest_id`` 与 payload 绑定为不可变 ``ReleaseManifest``。

    参数：
        manifest_id: 由工厂根据 payload 规范字节计算出的 64 位 SHA256。
        payload: 已校验的可复现 ``ReleaseManifestPayload``。

    返回：
        绑定 ID 与 payload 的不可变 ``ReleaseManifest``。

    异常：
        无；调用方保证 ID 已正确计算。

    约束与副作用：
        仅供 ``ReleaseManifestFactory`` 与编解码器使用；公开调用方应通过工厂创建，
        不得自行传入任意 ID。纯内存构造，无 I/O。
    """
    return ReleaseManifest(
        manifest_id=manifest_id,
        payload=payload,
        _factory_token=_RELEASE_MANIFEST_FACTORY_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """绑定可复现 payload 与内容寻址 ``manifest_id`` 的不可变发布清单。

    职责：
        作为发布身份载体：``manifest_id`` 必须等于 payload 规范 JSON 字节的
        SHA256。公开调用方不得直接构造。

    参数：
        manifest_id: 64 位小写十六进制 SHA256；仅由工厂写入。
        payload: 可复现 ``ReleaseManifestPayload``。
        _factory_token: 模块私有工厂令牌。

    返回：
        无；本类为不可变数据载体。

    异常：
        直接公开构造时抛出 ``TypeError``。

    约束与副作用：
        ``frozen=True, slots=True``；创建只允许经工厂；无 I/O。
    """

    manifest_id: str
    payload: ReleaseManifestPayload
    _factory_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """拒绝非工厂构造，保证 ID 只能由工厂写入。

        参数：
            无；读取 ``self._factory_token``。

        返回：
            ``None``。

        异常：
            ``_factory_token`` 不是模块私有令牌时抛出 ``TypeError``。

        约束与副作用：
            仅内存门禁。
        """
        if self._factory_token is not _RELEASE_MANIFEST_FACTORY_TOKEN:
            raise TypeError(
                "ReleaseManifest 只能通过 ReleaseManifestFactory.create(payload) "
                "创建，禁止直接构造或传入自备 manifest_id"
            )
