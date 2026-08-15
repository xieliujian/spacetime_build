"""ReleaseBundle payload 规范编解码、工厂与严格本地读写。

本模块负责将 ``ReleaseBundlePayload`` 编码为确定性 JSON 字节，通过
``ReleaseBundleFactory`` 计算 ``bundle_id``，并以临时文件加 ``Path.replace()``
原子写入；读取时重算 ID 做完整性校验。无序 manifest 集合在编码边界按
variant 值与 ``manifest_id`` 稳定排序。复用 ``release_manifest_payload_dict``
与 ``core.manifest_codec.canonical_json_bytes``。禁止用文件中的 ID 直接构造
``ReleaseBundle``。本模块不实现发布器或 CDN。导入本模块不执行构建或发布。
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
from release.bundles import (
    RELEASE_BUNDLE_SCHEMA_VERSION,
    ReleaseBundle,
    ReleaseBundlePayload,
    bind_release_bundle,
)
from release.entries import (
    ReleaseEntry,
    ReleaseObjectOrigin,
    ResourceVariant,
)
from release.manifest_codec import (
    ReleaseManifestFactory,
    release_manifest_payload_dict,
)
from release.manifests import (
    RELEASE_MANIFEST_SCHEMA_VERSION,
    ReleaseManifest,
    ReleaseManifestPayload,
)
from release.snapshots import (
    RedirectSlice,
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)


def _sorted_manifests(
    manifests: tuple[ReleaseManifest, ...],
) -> tuple[ReleaseManifest, ...]:
    """按 variant 值与 manifest_id 对无序 manifest 集合做稳定排序。

    参数：
        manifests: 待排序的 ``ReleaseManifest`` 元组。

    返回：
        稳定排序后的新元组；不修改入参。

    异常：
        无。

    约束与副作用：
        纯函数；排序键为 ``(variant.value, manifest_id)``，保证交换输入顺序
        不影响规范字节。
    """
    return tuple(
        sorted(
            manifests,
            key=lambda item: (item.payload.variant.value, item.manifest_id),
        )
    )


def _manifest_document(manifest: ReleaseManifest) -> dict[str, object]:
    """将单个 ``ReleaseManifest`` 转为可嵌套的规范字典。

    参数：
        manifest: 工厂创建的不可变清单。

    返回：
        含 ``manifest_id`` 与规范 ``payload`` 的字典。

    异常：
        无。

    约束与副作用：
        纯函数；嵌套 payload 复用 ``release_manifest_payload_dict``。
    """
    return {
        "manifest_id": manifest.manifest_id,
        "payload": release_manifest_payload_dict(manifest.payload),
    }


def release_bundle_payload_dict(
    payload: ReleaseBundlePayload,
) -> dict[str, object]:
    """将 ``ReleaseBundlePayload`` 转为规范可编码字典。

    参数：
        payload: 可复现发布包 payload；不得含 ``bundle_id``。

    返回：
        仅含可复现字段的字典；``manifests`` 按 variant 值与 ``manifest_id``
        稳定排序。

    异常：
        无；调用方保证 payload 已通过领域校验。

    约束与副作用：
        只在无序集合边界规范化；不计算 ID；无 I/O。
    """
    return {
        "schema_version": payload.schema_version,
        "manifests": [_manifest_document(item) for item in _sorted_manifests(payload.manifests)],
        "baseline_bundle_id": payload.baseline_bundle_id,
    }


class ReleaseBundleFactory:
    """由可复现 payload 创建不可变 ``ReleaseBundle`` 的工厂。

    职责：
        完整规范编码 payload 后计算 SHA256 作为 ``bundle_id``，再以私有 token
        构造 ``ReleaseBundle``；调用方不能传入或覆盖 ID。

    参数：
        类方法接收 ``ReleaseBundlePayload``；无实例状态。

    返回：
        ``create`` 返回绑定 payload 与 64 位 ID 的 ``ReleaseBundle``。

    异常：
        payload 非法时由领域模型抛出 ``PublishError``。

    约束与副作用：
        纯内存计算；无 I/O；ID 输入刻意排除 ``bundle_id`` 字段自身。
    """

    @staticmethod
    def create(payload: ReleaseBundlePayload) -> ReleaseBundle:
        """根据 payload 规范字节创建带内容寻址 ID 的 ``ReleaseBundle``。

        参数：
            payload: 仅含可复现内容的 ``ReleaseBundlePayload``。

        返回：
            ``bundle_id`` 等于规范 JSON UTF-8 字节 SHA256 的不可变发布包。

        异常：
            无额外异常；依赖 payload / 编解码路径上的既有校验。

        约束与副作用：
            调用方不能传入 ID；不读写磁盘。
        """
        digest = hashlib.sha256(
            canonical_json_bytes(release_bundle_payload_dict(payload))
        ).hexdigest()
        return bind_release_bundle(bundle_id=digest, payload=payload)


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


def _parse_manifest_payload(raw: object) -> ReleaseManifestPayload:
    """从字典解析嵌套的 ``ReleaseManifestPayload``。"""
    data = _require_mapping(raw, field_name="manifest.payload")
    schema_version = _require_int(
        data.get("schema_version"), field_name="manifest.payload.schema_version"
    )
    if schema_version != RELEASE_MANIFEST_SCHEMA_VERSION:
        raise PublishError(
            "嵌套 ReleaseManifest schema_version 不受支持: "
            f"{schema_version!r}，当前仅支持 {RELEASE_MANIFEST_SCHEMA_VERSION}"
        )

    try:
        variant = ResourceVariant(
            _require_str(data.get("variant"), field_name="manifest.payload.variant")
        )
    except ValueError as exc:
        raise PublishError(f"manifest.payload.variant 非法: {exc}") from exc

    ids_raw = _require_list(
        data.get("source_manifest_ids"),
        field_name="manifest.payload.source_manifest_ids",
    )
    source_ids = tuple(
        _require_str(item, field_name="manifest.payload.source_manifest_ids[]") for item in ids_raw
    )

    snapshot = _parse_snapshot(data.get("snapshot"))
    if snapshot.variant is not variant:
        raise PublishError(
            "manifest.payload.variant 与 snapshot.variant 不一致: "
            f"payload={variant.value}, snapshot={snapshot.variant.value}"
        )

    return ReleaseManifestPayload(
        schema_version=schema_version,
        variant=variant,
        file_list_no=_require_int(
            data.get("file_list_no"), field_name="manifest.payload.file_list_no"
        ),
        snapshot=snapshot,
        source_manifest_ids=source_ids,
    )


def _parse_nested_manifest(raw: object) -> ReleaseManifest:
    """解析嵌套 manifest 并经工厂重算 ID 做完整性校验。"""
    data = _require_mapping(raw, field_name="manifests[]")
    file_id = data.get("manifest_id")
    if not isinstance(file_id, str) or file_id == "":
        raise PublishError("嵌套 ReleaseManifest.manifest_id 不得为空且必须是 str")
    if "payload" not in data:
        raise PublishError("嵌套 ReleaseManifest 缺少 payload 字段")

    payload = _parse_manifest_payload(data["payload"])
    recomputed = ReleaseManifestFactory.create(payload)
    if file_id != recomputed.manifest_id:
        raise PublishError("嵌套 ReleaseManifest.manifest_id 与 payload 规范字节 SHA256 不一致")
    return recomputed


def _parse_bundle_payload(raw: object) -> ReleaseBundlePayload:
    """从字典解析 ``ReleaseBundlePayload``。"""
    data = _require_mapping(raw, field_name="payload")
    schema_version = _require_int(data.get("schema_version"), field_name="payload.schema_version")
    # 未知 schema 必须在进入工厂前硬失败。
    if schema_version != RELEASE_BUNDLE_SCHEMA_VERSION:
        raise PublishError(
            "ReleaseBundle schema_version 不受支持: "
            f"{schema_version!r}，当前仅支持 {RELEASE_BUNDLE_SCHEMA_VERSION}"
        )

    manifests_raw = _require_list(data.get("manifests"), field_name="payload.manifests")
    manifests = tuple(_parse_nested_manifest(item) for item in manifests_raw)

    baseline_raw = data.get("baseline_bundle_id")
    if baseline_raw is not None and not isinstance(baseline_raw, str):
        raise PublishError("payload.baseline_bundle_id 必须是 str 或 null")

    return ReleaseBundlePayload(
        schema_version=schema_version,
        manifests=manifests,
        baseline_bundle_id=baseline_raw,
    )


def write_release_bundle(bundle: ReleaseBundle, path: Path) -> None:
    """将 ``ReleaseBundle`` 以规范 JSON 原子写入本地路径。

    参数：
        bundle: 工厂创建的不可变发布包。
        path: 目标文件路径。

    返回：
        ``None``；成功时 ``path`` 指向完整文件。

    异常：
        磁盘写入失败时抛出底层 ``OSError``。

    约束与副作用：
        先写入同目录临时文件，再 ``Path.replace()`` 替换目标。仅本地文件系统；
        不触达 CDN 或发布器。
    """
    document = {
        "bundle_id": bundle.bundle_id,
        "payload": release_bundle_payload_dict(bundle.payload),
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


def read_release_bundle(path: Path) -> ReleaseBundle:
    """从本地 JSON 读取并严格校验 ``ReleaseBundle``。

    参数：
        path: 发布包文件路径。

    返回：
        经工厂重算 ID 且与文件 ID 严格相等后的 ``ReleaseBundle``。

    异常：
        JSON/结构/schema 非法，文件中 ID 为空或与重算值不等，嵌套 manifest ID
        陈旧，重复 variant，或 FileListNo 不一致时抛出 ``PublishError``；
        不返回半合法对象。

    约束与副作用：
        先解析 payload（含嵌套 manifest 重算），再用 ``ReleaseBundleFactory.create``
        重算 bundle ID；禁止用文件中的 ID 直接构造。只读本地文件。
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PublishError(f"ReleaseBundle JSON 无法解析: {path}") from exc

    root = _require_mapping(document, field_name="ReleaseBundle 根对象")
    if "payload" not in root:
        raise PublishError("ReleaseBundle 缺少 payload 字段")

    file_id = root.get("bundle_id")
    if not isinstance(file_id, str) or file_id == "":
        raise PublishError("ReleaseBundle.bundle_id 不得为空且必须是 str")

    payload = _parse_bundle_payload(root["payload"])
    recomputed = ReleaseBundleFactory.create(payload)
    if file_id != recomputed.bundle_id:
        raise PublishError("ReleaseBundle.bundle_id 与 payload 规范字节 SHA256 不一致")
    return recomputed
