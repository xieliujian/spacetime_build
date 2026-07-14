"""覆盖 release 领域校验失败与编解码错误分支。

本模块补充第二阶段 Task 16 覆盖率：针对 payload/条目/快照类型非法、路径边界、
codec 解析失败与读写错误路径编写聚焦用例。测试不访问 SVN、Unity、Jenkins、CDN，
也不导入 ``compatibility``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from st.build.core.artifacts import BlobRef
from st.build.core.errors import PublishError
from st.build.release.bundle_codec import (
    ReleaseBundleFactory,
    read_release_bundle,
    release_bundle_payload_dict,
    write_release_bundle,
)
from st.build.release.bundles import (
    RELEASE_BUNDLE_SCHEMA_VERSION,
    ReleaseBundlePayload,
)
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
    ReleaseManifestPayload,
)
from st.build.release.snapshots import (
    RedirectSlice,
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_MD5_A = "1" * 32
_BOTH = frozenset({ReleaseMembership.FILE_LIST, ReleaseMembership.ASSET_BUNDLE_DATABASE})


def _blob(sha256: str = _SHA_A, *, size: int = 100) -> BlobRef:
    """构造测试用 ``BlobRef``。"""
    return BlobRef(locator=f"sha256:{sha256}", sha256=sha256, size=size)


def _entry(
    *,
    logical_path: str = "scene/a.ab",
    variant: ResourceVariant = ResourceVariant.MAIN,
    object_version: str = "123",
    object_origin: ReleaseObjectOrigin = ReleaseObjectOrigin.CURRENT_UPLOAD,
) -> ReleaseEntry:
    """构造合法 ``ReleaseEntry``。"""
    return ReleaseEntry(
        logical_path=logical_path,
        variant=variant,
        source_blob=_blob(_SHA_A),
        source_md5=_MD5_A,
        original_size=100,
        transfer_blob=_blob(_SHA_B, size=80),
        transfer_size=80,
        list_version=1,
        object_version=object_version,
        file_url=f"https://cdn.example/{logical_path}",
        subpackage_flag=0,
        object_origin=object_origin,
    )


def _ab(entry: ReleaseEntry) -> ReleaseSnapshotEntry:
    """包装为 AssetBundle 快照项。"""
    return ReleaseSnapshotEntry(
        release_entry=entry,
        artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
        memberships=_BOTH,
        assetbundle_dependencies=(),
        redirect_slice=None,
    )


def _make_manifest(
    *,
    variant: ResourceVariant = ResourceVariant.MAIN,
    file_list_no: int = 123,
) -> Any:
    """经工厂创建的 ``ReleaseManifest``。"""
    object_version = str(file_list_no) if variant is ResourceVariant.MAIN else f"{file_list_no}_low"
    snapshot = ReleaseSnapshot.create(
        variant, (_ab(_entry(variant=variant, object_version=object_version)),)
    )
    payload = ReleaseManifestPayload(
        schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
        variant=variant,
        file_list_no=file_list_no,
        snapshot=snapshot,
        source_manifest_ids=("src",),
    )
    return ReleaseManifestFactory.create(payload)


def test_entries_validation_helpers_and_type_failures() -> None:
    """验证 entries 路径/Int32 与构造期类型失败分支。

    Given: 故意非法的路径、布尔伪整数与错误字段类型。
    When: 构造 ``ReleaseEntry``。
    Then: 均抛出 ``PublishError``。
    """
    with pytest.raises(PublishError):
        _entry(logical_path=cast(str, 1))
    with pytest.raises(PublishError):
        _entry(logical_path="scene\\bad.ab")
    with pytest.raises(PublishError):
        _entry(logical_path="scene/bad.ab/")
    with pytest.raises(PublishError):
        _entry(logical_path="scene/./bad.ab")

    with pytest.raises(PublishError):
        ReleaseEntry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.MAIN,
            source_blob=_blob(),
            source_md5=_MD5_A,
            original_size=cast(int, True),
            transfer_blob=_blob(_SHA_B),
            transfer_size=1,
            list_version=1,
            object_version="{current}",
            file_url="https://cdn.example/a",
            subpackage_flag=0,
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )
    with pytest.raises(PublishError):
        ReleaseEntry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.MAIN,
            source_blob=_blob(),
            source_md5=_MD5_A,
            original_size=1,
            transfer_blob=_blob(_SHA_B),
            transfer_size=1,
            list_version=cast(int, True),
            object_version="{current}",
            file_url="https://cdn.example/a",
            subpackage_flag=0,
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )
    with pytest.raises(PublishError):
        ReleaseEntry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.MAIN,
            source_blob=_blob(),
            source_md5=_MD5_A,
            original_size=1,
            transfer_blob=_blob(_SHA_B),
            transfer_size=1,
            list_version=0,
            object_version="{current}",
            file_url="https://cdn.example/a",
            subpackage_flag=0,
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )

    with pytest.raises(PublishError):
        ReleaseEntry(
            logical_path="scene/a.ab",
            variant=cast(ResourceVariant, "main"),
            source_blob=_blob(),
            source_md5=_MD5_A,
            original_size=1,
            transfer_blob=_blob(_SHA_B),
            transfer_size=1,
            list_version=1,
            object_version="{current}",
            file_url="https://cdn.example/a",
            subpackage_flag=0,
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )

    with pytest.raises(PublishError):
        ReleaseEntry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.MAIN,
            source_blob=_blob(),
            source_md5=_MD5_A,
            original_size=1,
            transfer_blob=_blob(_SHA_B),
            transfer_size=1,
            list_version=1,
            object_version="{current}",
            file_url="https://cdn.example/a",
            subpackage_flag=0,
            object_origin=cast(ReleaseObjectOrigin, "current_upload"),
        )

    with pytest.raises(PublishError):
        ReleaseEntry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.MAIN,
            source_blob=cast(BlobRef, "not-blob"),
            source_md5=_MD5_A,
            original_size=1,
            transfer_blob=_blob(_SHA_B),
            transfer_size=1,
            list_version=1,
            object_version="{current}",
            file_url="https://cdn.example/a",
            subpackage_flag=0,
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )

    with pytest.raises(PublishError):
        ReleaseEntry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.MAIN,
            source_blob=_blob(),
            source_md5=_MD5_A,
            original_size=1,
            transfer_blob=cast(BlobRef, "not-blob"),
            transfer_size=1,
            list_version=1,
            object_version="{current}",
            file_url="https://cdn.example/a",
            subpackage_flag=0,
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )

    with pytest.raises(PublishError):
        ReleaseEntry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.MAIN,
            source_blob=_blob(),
            source_md5=_MD5_A,
            original_size=1,
            transfer_blob=_blob(_SHA_B),
            transfer_size=1,
            list_version=1,
            object_version="{current}",
            file_url="   ",
            subpackage_flag=0,
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )


def test_redirect_slice_and_snapshot_entry_type_failures() -> None:
    """验证 RedirectSlice / SnapshotEntry / create 的类型与边界失败。

    Given: 空路径、非 Blob、布尔伪整数、错误 membership 与非法 entries 容器。
    When: 构造切片/条目或调用 ``ReleaseSnapshot.create``。
    Then: 抛出 ``PublishError``。
    """
    with pytest.raises(PublishError):
        RedirectSlice(
            container_logical_path="",
            container=_blob(size=10),
            offset=0,
            length=1,
        )
    with pytest.raises(PublishError):
        RedirectSlice(
            container_logical_path="c.ab",
            container=cast(BlobRef, "x"),
            offset=0,
            length=1,
        )
    with pytest.raises(PublishError):
        RedirectSlice(
            container_logical_path="c.ab",
            container=_blob(size=10),
            offset=cast(int, True),
            length=1,
        )
    with pytest.raises(PublishError):
        RedirectSlice(
            container_logical_path="c.ab",
            container=_blob(size=10),
            offset=0,
            length=cast(int, True),
        )
    with pytest.raises(PublishError):
        RedirectSlice(
            container_logical_path="c.ab",
            container=_blob(size=10),
            offset=0,
            length=0,
        )

    entry = _entry()
    with pytest.raises(PublishError):
        ReleaseSnapshotEntry(
            release_entry=cast(ReleaseEntry, "bad"),
            artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
            memberships=_BOTH,
            assetbundle_dependencies=(),
            redirect_slice=None,
        )
    with pytest.raises(PublishError):
        ReleaseSnapshotEntry(
            release_entry=entry,
            artifact_class=cast(ReleaseArtifactClass, "asset_bundle"),
            memberships=_BOTH,
            assetbundle_dependencies=(),
            redirect_slice=None,
        )
    with pytest.raises(PublishError):
        ReleaseSnapshotEntry(
            release_entry=entry,
            artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
            memberships=cast(frozenset[ReleaseMembership], {"file_list"}),
            assetbundle_dependencies=(),
            redirect_slice=None,
        )
    with pytest.raises(PublishError):
        ReleaseSnapshotEntry(
            release_entry=entry,
            artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
            memberships=frozenset({cast(ReleaseMembership, "file_list")}),
            assetbundle_dependencies=(),
            redirect_slice=None,
        )
    with pytest.raises(PublishError):
        ReleaseSnapshotEntry(
            release_entry=entry,
            artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
            memberships=_BOTH,
            assetbundle_dependencies=cast(tuple[str, ...], ["a"]),
            redirect_slice=None,
        )
    with pytest.raises(PublishError):
        ReleaseSnapshotEntry(
            release_entry=entry,
            artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
            memberships=_BOTH,
            assetbundle_dependencies=("",),
            redirect_slice=None,
        )
    with pytest.raises(PublishError):
        ReleaseSnapshotEntry(
            release_entry=entry,
            artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
            memberships=_BOTH,
            assetbundle_dependencies=(),
            redirect_slice=cast(RedirectSlice, "bad"),
        )

    with pytest.raises(PublishError):
        ReleaseSnapshot.create(cast(ResourceVariant, "main"), ())
    with pytest.raises(PublishError):
        ReleaseSnapshot.create(ResourceVariant.MAIN, cast(Any, "not-seq"))
    with pytest.raises(PublishError):
        ReleaseSnapshot.create(ResourceVariant.MAIN, cast(Any, ["not-entry"]))
    # list 输入合法路径：空 list 产生空快照
    empty = ReleaseSnapshot.create(ResourceVariant.MAIN, [])
    assert empty.entries == ()


def test_manifest_and_bundle_payload_type_failures() -> None:
    """验证 Manifest/Bundle payload 的类型与空集合失败分支。

    Given: 非法 schema/variant/snapshot/ids/manifests/baseline。
    When: 构造对应 payload。
    Then: 抛出 ``PublishError``。
    """
    snapshot = ReleaseSnapshot.create(ResourceVariant.MAIN, (_ab(_entry()),))
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=cast(int, True),
            variant=ResourceVariant.MAIN,
            file_list_no=123,
            snapshot=snapshot,
            source_manifest_ids=("src",),
        )
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=1,
            variant=cast(ResourceVariant, "main"),
            file_list_no=123,
            snapshot=snapshot,
            source_manifest_ids=("src",),
        )
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=1,
            variant=ResourceVariant.MAIN,
            file_list_no=123,
            snapshot=cast(ReleaseSnapshot, "bad"),
            source_manifest_ids=("src",),
        )
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=1,
            variant=ResourceVariant.MAIN,
            file_list_no=cast(int, True),
            snapshot=snapshot,
            source_manifest_ids=("src",),
        )
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=1,
            variant=ResourceVariant.MAIN,
            file_list_no=0,
            snapshot=snapshot,
            source_manifest_ids=("src",),
        )
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=1,
            variant=ResourceVariant.MAIN,
            file_list_no=123,
            snapshot=snapshot,
            source_manifest_ids=cast(tuple[str, ...], ["src"]),
        )
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=1,
            variant=ResourceVariant.MAIN,
            file_list_no=123,
            snapshot=snapshot,
            source_manifest_ids=("",),
        )

    main = _make_manifest()
    with pytest.raises(PublishError):
        ReleaseBundlePayload(
            schema_version=cast(int, True),
            manifests=(main,),
            baseline_bundle_id=None,
        )
    with pytest.raises(PublishError):
        ReleaseBundlePayload(
            schema_version=1,
            manifests=cast(tuple[Any, ...], [main]),
            baseline_bundle_id=None,
        )
    with pytest.raises(PublishError):
        ReleaseBundlePayload(
            schema_version=1,
            manifests=(main,),
            baseline_bundle_id="",
        )
    with pytest.raises(PublishError):
        ReleaseBundlePayload(
            schema_version=1,
            manifests=(),
            baseline_bundle_id=None,
        )
    with pytest.raises(PublishError):
        ReleaseBundlePayload(
            schema_version=1,
            manifests=cast(tuple[Any, ...], ("not-manifest",)),
            baseline_bundle_id=None,
        )


def test_manifest_codec_read_write_error_paths(tmp_path: Path) -> None:
    """验证 ReleaseManifest 编解码对非法 JSON/结构/schema 的拒绝。

    Given: 合法清单与若干篡改磁盘文档。
    When: ``read_release_manifest`` / 非法字段解析。
    Then: 抛出 ``PublishError``；合法 round-trip 仍成功。
    """
    manifest = _make_manifest()
    path = tmp_path / "m.json"
    write_release_manifest(manifest, path)
    assert read_release_manifest(path).manifest_id == manifest.manifest_id

    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_manifest(path)

    path.write_text(json.dumps(["not-object"], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_manifest(path)

    path.write_text(
        json.dumps({"manifest_id": manifest.manifest_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_manifest(path)

    path.write_text(
        json.dumps(
            {
                "manifest_id": "",
                "payload": release_manifest_payload_dict(manifest.payload),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_manifest(path)

    bad_payload = release_manifest_payload_dict(manifest.payload)
    bad_payload["schema_version"] = 999
    path.write_text(
        json.dumps(
            {"manifest_id": manifest.manifest_id, "payload": bad_payload},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_manifest(path)

    # memberships 非列表
    tampered = release_manifest_payload_dict(manifest.payload)
    cast(dict[str, Any], cast(dict[str, Any], tampered["snapshot"])["entries"][0])[
        "memberships"
    ] = "bad"
    path.write_text(
        json.dumps(
            {"manifest_id": manifest.manifest_id, "payload": tampered},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_manifest(path)

    # 非法枚举 / 依赖非列表 / source ids 非列表 / entries 非列表
    for key, value in (
        ("variant", "nope"),
        ("source_manifest_ids", "bad"),
    ):
        doc = release_manifest_payload_dict(manifest.payload)
        doc[key] = value
        path.write_text(
            json.dumps({"manifest_id": "x" * 64, "payload": doc}, ensure_ascii=False),
            encoding="utf-8",
        )
        with pytest.raises(PublishError):
            read_release_manifest(path)

    doc = release_manifest_payload_dict(manifest.payload)
    cast(dict[str, Any], doc["snapshot"])["entries"] = "bad"
    path.write_text(
        json.dumps({"manifest_id": "x" * 64, "payload": doc}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_manifest(path)

    doc = release_manifest_payload_dict(manifest.payload)
    entry0 = cast(dict[str, Any], cast(dict[str, Any], doc["snapshot"])["entries"][0])
    entry0["assetbundle_dependencies"] = "bad"
    path.write_text(
        json.dumps({"manifest_id": "x" * 64, "payload": doc}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_manifest(path)

    doc = release_manifest_payload_dict(manifest.payload)
    entry0 = cast(dict[str, Any], cast(dict[str, Any], doc["snapshot"])["entries"][0])
    entry0["artifact_class"] = "nope"
    path.write_text(
        json.dumps({"manifest_id": "x" * 64, "payload": doc}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_manifest(path)

    # 带 redirect_slice 的编码路径：构造容器+切片后 round-trip 覆盖 _redirect_slice_dict
    container_entry = ReleaseSnapshotEntry(
        release_entry=_entry(logical_path="container.ab", object_version="123"),
        artifact_class=ReleaseArtifactClass.REDIRECT_CONTAINER,
        memberships=_BOTH,
        assetbundle_dependencies=(),
        redirect_slice=None,
    )
    slice_entry = ReleaseSnapshotEntry(
        release_entry=_entry(logical_path="slice.ab", object_version="123"),
        artifact_class=ReleaseArtifactClass.REDIRECT_SLICE,
        memberships=frozenset({ReleaseMembership.ASSET_BUNDLE_DATABASE}),
        assetbundle_dependencies=(),
        redirect_slice=RedirectSlice(
            container_logical_path="container.ab",
            container=container_entry.release_entry.transfer_blob,
            offset=0,
            length=10,
        ),
    )
    snap = ReleaseSnapshot.create(ResourceVariant.MAIN, (container_entry, slice_entry))
    payload = ReleaseManifestPayload(
        schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
        variant=ResourceVariant.MAIN,
        file_list_no=123,
        snapshot=snap,
        source_manifest_ids=("src",),
    )
    with_redirect = ReleaseManifestFactory.create(payload)
    redirect_path = tmp_path / "redirect.json"
    write_release_manifest(with_redirect, redirect_path)
    assert read_release_manifest(redirect_path).manifest_id == with_redirect.manifest_id
    encoded = release_manifest_payload_dict(with_redirect.payload)
    assert any(
        cast(dict[str, Any], item).get("redirect_slice") is not None
        for item in cast(list[object], cast(dict[str, Any], encoded["snapshot"])["entries"])
    )


def test_bundle_codec_read_write_error_paths(tmp_path: Path) -> None:
    """验证 ReleaseBundle 编解码对非法 JSON/结构/嵌套字段的拒绝。

    Given: 合法 bundle 与篡改文档。
    When: ``read_release_bundle``。
    Then: 抛出 ``PublishError``。
    """
    main = _make_manifest()
    payload = ReleaseBundlePayload(
        schema_version=RELEASE_BUNDLE_SCHEMA_VERSION,
        manifests=(main,),
        baseline_bundle_id=None,
    )
    bundle = ReleaseBundleFactory.create(payload)
    path = tmp_path / "b.json"
    write_release_bundle(bundle, path)
    assert read_release_bundle(path).bundle_id == bundle.bundle_id

    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_bundle(path)

    path.write_text(json.dumps(123, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_bundle(path)

    path.write_text(
        json.dumps({"bundle_id": bundle.bundle_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_bundle(path)

    path.write_text(
        json.dumps(
            {"bundle_id": "", "payload": release_bundle_payload_dict(payload)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_bundle(path)

    encoded = release_bundle_payload_dict(payload)
    encoded["manifests"] = "bad"
    path.write_text(
        json.dumps({"bundle_id": "x" * 64, "payload": encoded}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_bundle(path)

    encoded = release_bundle_payload_dict(payload)
    encoded["baseline_bundle_id"] = 123
    path.write_text(
        json.dumps({"bundle_id": "x" * 64, "payload": encoded}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_bundle(path)

    # 嵌套 manifest 缺 payload / 空 id / 非法 membership
    encoded = release_bundle_payload_dict(payload)
    manifests = cast(list[dict[str, Any]], encoded["manifests"])
    manifests[0] = {"manifest_id": "y" * 64}
    path.write_text(
        json.dumps({"bundle_id": "x" * 64, "payload": encoded}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_bundle(path)

    encoded = release_bundle_payload_dict(payload)
    manifests = cast(list[dict[str, Any]], encoded["manifests"])
    manifests[0]["manifest_id"] = ""
    path.write_text(
        json.dumps({"bundle_id": "x" * 64, "payload": encoded}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_bundle(path)

    encoded = release_bundle_payload_dict(payload)
    nested_payload = cast(
        dict[str, Any],
        cast(list[dict[str, Any]], encoded["manifests"])[0]["payload"],
    )
    nested_payload["schema_version"] = 999
    path.write_text(
        json.dumps({"bundle_id": "x" * 64, "payload": encoded}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_bundle(path)

    def _first_snapshot_entry(doc: dict[str, object]) -> dict[str, Any]:
        """从 bundle payload 文档取出第一条 snapshot entry。"""
        manifests_list = cast(list[dict[str, Any]], doc["manifests"])
        nested = cast(dict[str, Any], manifests_list[0]["payload"])
        snapshot = cast(dict[str, Any], nested["snapshot"])
        entries = cast(list[dict[str, Any]], snapshot["entries"])
        return entries[0]

    # release_entry 枚举非法
    encoded = release_bundle_payload_dict(payload)
    entry0 = _first_snapshot_entry(encoded)
    cast(dict[str, Any], entry0["release_entry"])["variant"] = "nope"
    path.write_text(
        json.dumps({"bundle_id": "x" * 64, "payload": encoded}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_bundle(path)

    # membership 非法字符串
    encoded = release_bundle_payload_dict(payload)
    entry0 = _first_snapshot_entry(encoded)
    entry0["memberships"] = ["nope"]
    path.write_text(
        json.dumps({"bundle_id": "x" * 64, "payload": encoded}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PublishError):
        read_release_bundle(path)
