"""ReleaseManifest payload 规范编解码、工厂与严格本地读写。

本模块负责将 ``ReleaseManifestPayload`` 编码为确定性 JSON 字节，通过
``ReleaseManifestFactory`` 计算 ``manifest_id``，并以临时文件加 ``Path.replace()``
原子写入；读取时重算 ID 做完整性校验。无序集合（snapshot entries、
source_manifest_ids）在编码边界按 UTF-8 字节键排序；有序 AB 依赖元组保留顺序
与重复。复用 ``core.manifest_codec.canonical_json_bytes``。禁止用文件中的 ID
直接构造 ``ReleaseManifest``。导入本模块不执行构建或发布。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import cast

from core.artifacts import BlobRef
from core.errors import PublishError
from core.manifest_codec import canonical_json_bytes
from release.entries import (
    ReleaseEntry,
    ReleaseObjectOrigin,
    ResourceVariant,
)
from release.manifests import (
    RELEASE_MANIFEST_SCHEMA_VERSION,
    ReleaseManifest,
    ReleaseManifestPayload,
    bind_release_manifest,
)
from release.snapshots import (
    RedirectSlice,
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)


def _blob_dict(blob: BlobRef) -> dict[str, object]:
    """将 ``BlobRef`` 转为可规范编码的字典。

    参数：
        blob: 已校验的持久 Blob 引用。

    返回：
        含 locator、sha256、size 的字典。

    异常：
        无。

    约束与副作用：
        纯函数。
    """
    return {
        "locator": blob.locator,
        "sha256": blob.sha256,
        "size": blob.size,
    }


def _release_entry_dict(entry: ReleaseEntry) -> dict[str, object]:
    """将 ``ReleaseEntry`` 转为可规范编码的字典。

    参数：
        entry: 已校验的发布条目。

    返回：
        含全部可复现字段的字典。

    异常：
        无。

    约束与副作用：
        纯函数；不计算 ID。
    """
    return {
        "logical_path": entry.logical_path,
        "variant": entry.variant.value,
        "source_blob": _blob_dict(entry.source_blob),
        "source_md5": entry.source_md5,
        "original_size": entry.original_size,
        "transfer_blob": _blob_dict(entry.transfer_blob),
        "transfer_size": entry.transfer_size,
        "list_version": entry.list_version,
        "object_version": entry.object_version,
        "file_url": entry.file_url,
        "subpackage_flag": entry.subpackage_flag,
        "object_origin": entry.object_origin.value,
    }


def _redirect_slice_dict(slice_info: RedirectSlice) -> dict[str, object]:
    """将 ``RedirectSlice`` 转为可规范编码的字典。

    参数：
        slice_info: Redirect 切片。

    返回：
        含容器路径、Blob、offset、length 的字典。

    异常：
        无。

    约束与副作用：
        纯函数。
    """
    return {
        "container_logical_path": slice_info.container_logical_path,
        "container": _blob_dict(slice_info.container),
        "offset": slice_info.offset,
        "length": slice_info.length,
    }


def _snapshot_entry_dict(entry: ReleaseSnapshotEntry) -> dict[str, object]:
    """将 ``ReleaseSnapshotEntry`` 转为可规范编码的字典。

    参数：
        entry: 快照条目。

    返回：
        含 release_entry、分类、已排序 membership、有序依赖与可选 Redirect 的字典。

    异常：
        无。

    约束与副作用：
        membership 按 value UTF-8 排序；依赖保留原序与重复。
    """
    return {
        "release_entry": _release_entry_dict(entry.release_entry),
        "artifact_class": entry.artifact_class.value,
        "memberships": sorted(m.value for m in entry.memberships),
        "assetbundle_dependencies": list(entry.assetbundle_dependencies),
        "redirect_slice": (
            None if entry.redirect_slice is None else _redirect_slice_dict(entry.redirect_slice)
        ),
    }


def release_manifest_payload_dict(
    payload: ReleaseManifestPayload,
) -> dict[str, object]:
    """将 ``ReleaseManifestPayload`` 转为规范可编码字典。

    参数：
        payload: 可复现发布清单 payload；不得含 ``manifest_id``。

    返回：
        仅含可复现字段的字典；snapshot entries 按逻辑路径 UTF-8 排序；
        ``source_manifest_ids`` 按 UTF-8 排序。

    异常：
        无；调用方保证 payload 已通过领域校验。

    约束与副作用：
        只在无序集合边界规范化；有序依赖不排序不去重；不计算 ID；无 I/O。
    """
    sorted_entries = sorted(
        payload.snapshot.entries,
        key=lambda item: item.release_entry.logical_path.encode("utf-8"),
    )
    sorted_source_ids = sorted(payload.source_manifest_ids, key=lambda item: item.encode("utf-8"))
    return {
        "schema_version": payload.schema_version,
        "variant": payload.variant.value,
        "file_list_no": payload.file_list_no,
        "snapshot": {
            "variant": payload.snapshot.variant.value,
            "entries": [_snapshot_entry_dict(item) for item in sorted_entries],
        },
        "source_manifest_ids": list(sorted_source_ids),
    }


class ReleaseManifestFactory:
    """由可复现 payload 创建不可变 ``ReleaseManifest`` 的工厂。

    职责：
        完整规范编码 payload 后计算 SHA256 作为 ``manifest_id``，再以私有 token
        构造 ``ReleaseManifest``；调用方不能传入或覆盖 ID。

    参数：
        类方法接收 ``ReleaseManifestPayload``；无实例状态。

    返回：
        ``create`` 返回绑定 payload 与 64 位 ID 的 ``ReleaseManifest``。

    异常：
        payload 非法时由领域模型抛出 ``PublishError``。

    约束与副作用：
        纯内存计算；无 I/O；ID 输入刻意排除 ``manifest_id`` 字段自身。
    """

    @staticmethod
    def create(payload: ReleaseManifestPayload) -> ReleaseManifest:
        """根据 payload 规范字节创建带内容寻址 ID 的 ``ReleaseManifest``。

        参数：
            payload: 仅含可复现内容的 ``ReleaseManifestPayload``。

        返回：
            ``manifest_id`` 等于规范 JSON UTF-8 字节 SHA256 的不可变清单。

        异常：
            无额外异常；依赖 payload / 编解码路径上的既有校验。

        约束与副作用：
            调用方不能传入 ID；不读写磁盘。
        """
        digest = hashlib.sha256(
            canonical_json_bytes(release_manifest_payload_dict(payload))
        ).hexdigest()
        return bind_release_manifest(manifest_id=digest, payload=payload)


def _require_mapping(value: object, *, field_name: str) -> dict[str, object]:
    """要求值为 JSON 对象映射。"""
    if not isinstance(value, dict):
        raise PublishError(f"{field_name} 必须是 JSON 对象")
    return cast(dict[str, object], value)


def _require_list(value: object, *, field_name: str) -> list[object]:
    """要求值为 JSON 数组。"""
    if not isinstance(value, list):
        raise PublishError(f"{field_name} 必须是列表")
    return cast(list[object], value)


def _require_str(value: object, *, field_name: str) -> str:
    """要求值为字符串。"""
    if not isinstance(value, str):
        raise PublishError(f"{field_name} 必须是 str")
    return value


def _require_int(value: object, *, field_name: str) -> int:
    """要求值为非布尔整数。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise PublishError(f"{field_name} 必须是 int")
    return value


def _parse_blob(raw: object, *, field_name: str) -> BlobRef:
    """从字典解析 ``BlobRef``。"""
    data = _require_mapping(raw, field_name=field_name)
    return BlobRef(
        locator=_require_str(data.get("locator"), field_name=f"{field_name}.locator"),
        sha256=_require_str(data.get("sha256"), field_name=f"{field_name}.sha256"),
        size=_require_int(data.get("size"), field_name=f"{field_name}.size"),
    )


def _parse_release_entry(raw: object) -> ReleaseEntry:
    """从字典解析 ``ReleaseEntry``。"""
    data = _require_mapping(raw, field_name="release_entry")
    try:
        variant = ResourceVariant(
            _require_str(data.get("variant"), field_name="release_entry.variant")
        )
        object_origin = ReleaseObjectOrigin(
            _require_str(data.get("object_origin"), field_name="release_entry.object_origin")
        )
    except ValueError as exc:
        raise PublishError(f"release_entry 枚举字段非法: {exc}") from exc

    try:
        return ReleaseEntry(
            logical_path=_require_str(
                data.get("logical_path"), field_name="release_entry.logical_path"
            ),
            variant=variant,
            source_blob=_parse_blob(
                data.get("source_blob"), field_name="release_entry.source_blob"
            ),
            source_md5=_require_str(data.get("source_md5"), field_name="release_entry.source_md5"),
            original_size=_require_int(
                data.get("original_size"), field_name="release_entry.original_size"
            ),
            transfer_blob=_parse_blob(
                data.get("transfer_blob"), field_name="release_entry.transfer_blob"
            ),
            transfer_size=_require_int(
                data.get("transfer_size"), field_name="release_entry.transfer_size"
            ),
            list_version=_require_int(
                data.get("list_version"), field_name="release_entry.list_version"
            ),
            object_version=_require_str(
                data.get("object_version"), field_name="release_entry.object_version"
            ),
            file_url=_require_str(data.get("file_url"), field_name="release_entry.file_url"),
            subpackage_flag=_require_int(
                data.get("subpackage_flag"),
                field_name="release_entry.subpackage_flag",
            ),
            object_origin=object_origin,
        )
    except PublishError:
        raise
    except (TypeError, ValueError) as exc:
        raise PublishError(f"解析 ReleaseEntry 失败: {exc}") from exc


def _parse_redirect_slice(raw: object) -> RedirectSlice | None:
    """从字典或 null 解析 ``RedirectSlice``。"""
    if raw is None:
        return None
    data = _require_mapping(raw, field_name="redirect_slice")
    return RedirectSlice(
        container_logical_path=_require_str(
            data.get("container_logical_path"),
            field_name="redirect_slice.container_logical_path",
        ),
        container=_parse_blob(data.get("container"), field_name="redirect_slice.container"),
        offset=_require_int(data.get("offset"), field_name="redirect_slice.offset"),
        length=_require_int(data.get("length"), field_name="redirect_slice.length"),
    )


def _parse_snapshot_entry(raw: object) -> ReleaseSnapshotEntry:
    """从字典解析 ``ReleaseSnapshotEntry``。"""
    data = _require_mapping(raw, field_name="snapshot.entries[]")
    try:
        artifact_class = ReleaseArtifactClass(
            _require_str(data.get("artifact_class"), field_name="artifact_class")
        )
    except ValueError as exc:
        raise PublishError(f"artifact_class 非法: {exc}") from exc

    memberships_raw = _require_list(data.get("memberships"), field_name="memberships")
    memberships: set[ReleaseMembership] = set()
    for item in memberships_raw:
        try:
            memberships.add(ReleaseMembership(_require_str(item, field_name="memberships[]")))
        except ValueError as exc:
            raise PublishError(f"membership 非法: {exc}") from exc

    deps_raw = _require_list(
        data.get("assetbundle_dependencies"), field_name="assetbundle_dependencies"
    )
    dependencies = tuple(
        _require_str(item, field_name="assetbundle_dependencies[]") for item in deps_raw
    )

    return ReleaseSnapshotEntry(
        release_entry=_parse_release_entry(data.get("release_entry")),
        artifact_class=artifact_class,
        memberships=frozenset(memberships),
        assetbundle_dependencies=dependencies,
        redirect_slice=_parse_redirect_slice(data.get("redirect_slice")),
    )


def _parse_snapshot(raw: object) -> ReleaseSnapshot:
    """从字典解析并经 ``ReleaseSnapshot.create`` 校验的快照。"""
    data = _require_mapping(raw, field_name="snapshot")
    try:
        variant = ResourceVariant(_require_str(data.get("variant"), field_name="snapshot.variant"))
    except ValueError as exc:
        raise PublishError(f"snapshot.variant 非法: {exc}") from exc

    entries_raw = _require_list(data.get("entries"), field_name="snapshot.entries")
    entries = tuple(_parse_snapshot_entry(item) for item in entries_raw)
    return ReleaseSnapshot.create(variant, entries)


def _parse_payload(raw: object) -> ReleaseManifestPayload:
    """从字典解析 ``ReleaseManifestPayload``。"""
    data = _require_mapping(raw, field_name="payload")
    schema_version = _require_int(data.get("schema_version"), field_name="payload.schema_version")
    # 未知 schema 必须在进入工厂前硬失败。
    if schema_version != RELEASE_MANIFEST_SCHEMA_VERSION:
        raise PublishError(
            "ReleaseManifest schema_version 不受支持: "
            f"{schema_version!r}，当前仅支持 {RELEASE_MANIFEST_SCHEMA_VERSION}"
        )

    try:
        variant = ResourceVariant(_require_str(data.get("variant"), field_name="payload.variant"))
    except ValueError as exc:
        raise PublishError(f"payload.variant 非法: {exc}") from exc

    ids_raw = _require_list(
        data.get("source_manifest_ids"), field_name="payload.source_manifest_ids"
    )
    source_ids = tuple(
        _require_str(item, field_name="payload.source_manifest_ids[]") for item in ids_raw
    )

    snapshot = _parse_snapshot(data.get("snapshot"))
    # 顶层 variant 与 snapshot.variant 必须一致，防止磁盘篡改半合法对象。
    if snapshot.variant is not variant:
        raise PublishError(
            "payload.variant 与 snapshot.variant 不一致: "
            f"payload={variant.value}, snapshot={snapshot.variant.value}"
        )

    return ReleaseManifestPayload(
        schema_version=schema_version,
        variant=variant,
        file_list_no=_require_int(data.get("file_list_no"), field_name="payload.file_list_no"),
        snapshot=snapshot,
        source_manifest_ids=source_ids,
    )


def write_release_manifest(manifest: ReleaseManifest, path: Path) -> None:
    """将 ``ReleaseManifest`` 以规范 JSON 原子写入本地路径。

    参数：
        manifest: 工厂创建的不可变清单。
        path: 目标文件路径。

    返回：
        ``None``；成功时 ``path`` 指向完整文件。

    异常：
        磁盘写入失败时抛出底层 ``OSError``。

    约束与副作用：
        先写入同目录临时文件，再 ``Path.replace()`` 替换目标。仅本地文件系统。
    """
    document = {
        "manifest_id": manifest.manifest_id,
        "payload": release_manifest_payload_dict(manifest.payload),
    }
    body = canonical_json_bytes(document)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_bytes(body)
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def read_release_manifest(path: Path) -> ReleaseManifest:
    """从本地 JSON 读取并严格校验 ``ReleaseManifest``。

    参数：
        path: 清单文件路径。

    返回：
        经工厂重算 ID 且与文件 ID 严格相等后的 ``ReleaseManifest``。

    异常：
        JSON/结构/schema 非法，或文件中 ID 为空、与重算值不等，或 variant 不一致
        时抛出 ``PublishError``；不返回半合法对象。

    约束与副作用：
        先解析 payload，再用 ``ReleaseManifestFactory.create`` 重算 ID；禁止用
        文件中的 ID 直接构造。只读本地文件。
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PublishError(f"ReleaseManifest JSON 无法解析: {path}") from exc

    root = _require_mapping(document, field_name="ReleaseManifest 根对象")
    if "payload" not in root:
        raise PublishError("ReleaseManifest 缺少 payload 字段")

    file_id = root.get("manifest_id")
    if not isinstance(file_id, str) or file_id == "":
        raise PublishError("ReleaseManifest.manifest_id 不得为空且必须是 str")

    payload = _parse_payload(root["payload"])
    recomputed = ReleaseManifestFactory.create(payload)
    if file_id != recomputed.manifest_id:
        raise PublishError("ReleaseManifest.manifest_id 与 payload 规范字节 SHA256 不一致")
    return recomputed
