"""验证 ReleaseManifest 工厂规范化、身份哈希与严格读写。

本模块覆盖第二阶段 Task 13 Step 2：``ReleaseManifestFactory`` 对无序 snapshot
entries / source IDs 稳定化，保留有序重复依赖，拒绝直接构造；读写时重算 ID 并
拒绝空/陈旧 ID、未知 schema 与 variant 不一致。测试不访问外部系统。
"""

from __future__ import annotations

from typing import Any, cast

import hashlib
import json
from pathlib import Path

import pytest

from st.build.core.artifacts import BlobRef
from st.build.core.errors import PublishError
from st.build.core.manifest_codec import canonical_json_bytes
from st.build.release.entries import (
    ReleaseEntry,
    ReleaseObjectOrigin,
    ResourceVariant,
)
from st.build.release.manifest_codec import (
    ReleaseManifestFactory,
    read_release_manifest,
    release_manifest_payload_dict,
    write_release_manifest,
)
from st.build.release.manifests import (
    RELEASE_MANIFEST_SCHEMA_VERSION,
    ReleaseManifest,
    ReleaseManifestPayload,
)
from st.build.release.snapshots import (
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_MD5_A = "1" * 32
_MD5_B = "2" * 32
_BOTH = frozenset({ReleaseMembership.FILE_LIST, ReleaseMembership.ASSET_BUNDLE_DATABASE})


def _blob(sha256: str, *, size: int = 100) -> BlobRef:
    """构造测试用 ``BlobRef``。"""
    return BlobRef(locator=f"sha256:{sha256}", sha256=sha256, size=size)


def _entry(
    *,
    logical_path: str,
    object_version: str = "123",
    object_origin: ReleaseObjectOrigin = ReleaseObjectOrigin.CURRENT_UPLOAD,
    variant: ResourceVariant = ResourceVariant.MAIN,
    source_sha: str = _SHA_A,
    transfer_sha: str = _SHA_B,
    source_md5: str = _MD5_A,
) -> ReleaseEntry:
    """构造测试用 ``ReleaseEntry``。"""
    return ReleaseEntry(
        logical_path=logical_path,
        variant=variant,
        source_blob=_blob(source_sha),
        source_md5=source_md5,
        original_size=100,
        transfer_blob=_blob(transfer_sha, size=80),
        transfer_size=80,
        list_version=1,
        object_version=object_version,
        file_url=f"https://cdn.example/{logical_path}",
        subpackage_flag=0,
        object_origin=object_origin,
    )


def _ab(
    release_entry: ReleaseEntry,
    *,
    deps: tuple[str, ...] = (),
) -> ReleaseSnapshotEntry:
    """包装为 AssetBundle 快照条目。"""
    return ReleaseSnapshotEntry(
        release_entry=release_entry,
        artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
        memberships=_BOTH,
        assetbundle_dependencies=deps,
        redirect_slice=None,
    )


def _payload(
    *,
    schema_version: int = RELEASE_MANIFEST_SCHEMA_VERSION,
    variant: ResourceVariant = ResourceVariant.MAIN,
    file_list_no: int = 123,
    entries: tuple[ReleaseSnapshotEntry, ...],
    source_manifest_ids: tuple[str, ...] = ("src-a", "src-b"),
) -> ReleaseManifestPayload:
    """组装已校验的 ``ReleaseManifestPayload``。"""
    snapshot = ReleaseSnapshot.create(variant, entries)
    return ReleaseManifestPayload(
        schema_version=schema_version,
        variant=variant,
        file_list_no=file_list_no,
        snapshot=snapshot,
        source_manifest_ids=source_manifest_ids,
    )


def test_release_manifest_factory_stabilizes_unordered_inputs_and_hashes_all_identity_fields() -> (
    None
):
    """验证无序输入稳定化，且身份字段任一变化都会改变 ID。

    测试无参数和返回值。断言：

    - 交换无序 snapshot entries / source IDs 不改变 ID；
    - schema、variant、FileListNo、snapshot 内容或 source ID 任一变化改变 ID；
    - 依赖 tuple 顺序与重复保留；
    - 直接构造 ``ReleaseManifest`` 失败。

    当 ``st.build.release.manifests`` / ``manifest_codec`` 尚未创建时，测试收集
    阶段应以 ``ModuleNotFoundError`` 失败。除导入外不产生外部副作用。
    """
    entry_z = _ab(
        _entry(logical_path="z/last.ab", object_version="123"),
        deps=("b", "a", "b"),
    )
    entry_a = _ab(
        _entry(
            logical_path="a/first.ab",
            object_version="100",
            object_origin=ReleaseObjectOrigin.HISTORICAL,
            source_sha=_SHA_B,
            transfer_sha=_SHA_A,
            source_md5=_MD5_B,
        )
    )
    # 依赖目标。
    dep_a = _ab(
        _entry(
            logical_path="a",
            object_version="90",
            object_origin=ReleaseObjectOrigin.HISTORICAL,
            source_sha=_SHA_B,
            transfer_sha=_SHA_B,
            source_md5=_MD5_B,
        )
    )
    dep_b = _ab(
        _entry(
            logical_path="b",
            object_version="91",
            object_origin=ReleaseObjectOrigin.HISTORICAL,
            source_sha=_SHA_A,
            transfer_sha=_SHA_A,
            source_md5=_MD5_A,
        )
    )

    payload1 = _payload(
        entries=(entry_z, entry_a, dep_a, dep_b),
        source_manifest_ids=("src-b", "src-a"),
    )
    payload2 = _payload(
        entries=(dep_b, entry_a, dep_a, entry_z),
        source_manifest_ids=("src-a", "src-b"),
    )

    manifest1 = ReleaseManifestFactory.create(payload1)
    manifest2 = ReleaseManifestFactory.create(payload2)
    assert manifest1.manifest_id == manifest2.manifest_id
    expected_id = hashlib.sha256(
        canonical_json_bytes(release_manifest_payload_dict(payload1))
    ).hexdigest()
    assert manifest1.manifest_id == expected_id
    assert len(manifest1.manifest_id) == 64

    encoded = release_manifest_payload_dict(payload1)
    snapshot_doc = cast(dict[str, Any], encoded["snapshot"])
    entries_doc = cast(list[dict[str, Any]], snapshot_doc["entries"])
    z_item = next(
        item
        for item in entries_doc
        if cast(dict[str, Any], item["release_entry"])["logical_path"] == "z/last.ab"
    )
    assert z_item["assetbundle_dependencies"] == ["b", "a", "b"]

    with pytest.raises(TypeError):
        ReleaseManifest(manifest_id="", payload=payload1)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        ReleaseManifest(
            manifest_id=expected_id,
            payload=payload1,
        )  # type: ignore[call-arg]

    mutations = [
        _payload(
            schema_version=RELEASE_MANIFEST_SCHEMA_VERSION + 1,
            entries=(entry_z, entry_a, dep_a, dep_b),
        ),
        _payload(
            file_list_no=124,
            entries=(
                _ab(_entry(logical_path="z/last.ab", object_version="124"), deps=("b", "a", "b")),
                entry_a,
                dep_a,
                dep_b,
            ),
        ),
        _payload(
            entries=(
                _ab(
                    _entry(logical_path="z/last.ab", object_version="123"),
                    deps=("a", "b", "b"),
                ),
                entry_a,
                dep_a,
                dep_b,
            ),
        ),
        _payload(
            entries=(entry_z, entry_a, dep_a, dep_b),
            source_manifest_ids=("src-a", "src-CHANGED"),
        ),
    ]
    for mutated in mutations:
        other = ReleaseManifestFactory.create(mutated)
        assert other.manifest_id != manifest1.manifest_id

    # variant 变化：构造独立 low payload。
    low_entry = _ab(
        _entry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.LOW,
            object_version="123_low",
        )
    )
    low_payload = _payload(
        variant=ResourceVariant.LOW,
        entries=(low_entry,),
        source_manifest_ids=("src-a",),
    )
    low_manifest = ReleaseManifestFactory.create(low_payload)
    assert low_manifest.manifest_id != manifest1.manifest_id


def test_read_release_manifest_rejects_empty_stale_or_unknown_schema(
    tmp_path: Path,
) -> None:
    """验证原子写读 round-trip，并拒绝空/陈旧 ID、未知 schema 与 variant 不一致。

    参数：
        tmp_path: pytest 临时目录。

    返回：
        无。断言读写成功后 ID 一致；空 ID、陈旧 ID、未知 schema、variant 不一致
        均抛 ``PublishError``，不返回半合法对象。

    当编解码模块尚未创建时，收集阶段应以 ``ModuleNotFoundError`` 失败。
    仅向临时目录写文件。
    """
    entry = _ab(_entry(logical_path="scene/a.ab", object_version="123"))
    payload = _payload(entries=(entry,), source_manifest_ids=("src-1",))
    manifest = ReleaseManifestFactory.create(payload)
    path = tmp_path / "release_manifest.json"

    write_release_manifest(manifest, path)
    assert path.is_file()
    leftovers = list(tmp_path.glob("*.tmp*")) + list(tmp_path.glob(".*tmp*"))
    assert leftovers == []

    loaded = read_release_manifest(path)
    assert loaded.manifest_id == manifest.manifest_id
    assert loaded.payload == manifest.payload

    empty_id_doc = {
        "manifest_id": "",
        "payload": release_manifest_payload_dict(payload),
    }
    path.write_text(json.dumps(empty_id_doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_manifest(path)

    stale_doc = {
        "manifest_id": "0" * 64,
        "payload": release_manifest_payload_dict(payload),
    }
    path.write_text(json.dumps(stale_doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_manifest(path)

    unknown_schema = {
        "manifest_id": manifest.manifest_id,
        "payload": {
            **release_manifest_payload_dict(payload),
            "schema_version": 999,
        },
    }
    path.write_text(json.dumps(unknown_schema, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_manifest(path)

    # variant 字段与 snapshot.variant 不一致。
    mismatched = release_manifest_payload_dict(payload)
    mismatched["variant"] = ResourceVariant.LOW.value
    bad_variant_doc = {
        "manifest_id": manifest.manifest_id,
        "payload": mismatched,
    }
    path.write_text(json.dumps(bad_variant_doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_manifest(path)
