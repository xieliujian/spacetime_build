"""验证 ReleaseBundle 工厂规范化、身份哈希与严格读写。

本模块覆盖第二阶段 Task 14 Step 1：``ReleaseBundleFactory`` 对无序 manifest
集合稳定化，拒绝直接构造；读写时重算 ID 并拒绝空/陈旧 ID、未知 schema、
重复 variant 与 FileListNo 不一致。测试不访问外部系统。
"""

from __future__ import annotations

from typing import Any, cast

import hashlib
import json
from pathlib import Path

import pytest

from core.artifacts import BlobRef
from core.errors import PublishError
from core.manifest_codec import canonical_json_bytes
from release.bundles import (
    RELEASE_BUNDLE_SCHEMA_VERSION,
    ReleaseBundle,
    ReleaseBundlePayload,
)
from release.bundle_codec import (
    ReleaseBundleFactory,
    read_release_bundle,
    release_bundle_payload_dict,
    write_release_bundle,
)
from release.entries import (
    ReleaseEntry,
    ReleaseObjectOrigin,
    ResourceVariant,
)
from release.manifest_codec import ReleaseManifestFactory
from release.manifests import (
    RELEASE_MANIFEST_SCHEMA_VERSION,
    ReleaseManifestPayload,
)
from release.snapshots import (
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_MD5_A = "1" * 32
_BOTH = frozenset({ReleaseMembership.FILE_LIST, ReleaseMembership.ASSET_BUNDLE_DATABASE})


def _blob(sha256: str, *, size: int = 100) -> BlobRef:
    """构造测试用 ``BlobRef``。"""
    return BlobRef(locator=f"sha256:{sha256}", sha256=sha256, size=size)


def _entry(
    *,
    logical_path: str,
    variant: ResourceVariant,
    object_version: str,
    object_origin: ReleaseObjectOrigin = ReleaseObjectOrigin.CURRENT_UPLOAD,
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


def _ab(release_entry: ReleaseEntry) -> ReleaseSnapshotEntry:
    """包装为 AssetBundle 快照条目。"""
    return ReleaseSnapshotEntry(
        release_entry=release_entry,
        artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
        memberships=_BOTH,
        assetbundle_dependencies=(),
        redirect_slice=None,
    )


def _make_manifest(
    *,
    variant: ResourceVariant,
    file_list_no: int = 123,
    logical_path: str = "scene/a.ab",
    source_manifest_ids: tuple[str, ...] = ("src-a",),
):
    """组装并经工厂创建的 ``ReleaseManifest``。"""
    object_version = str(file_list_no) if variant is ResourceVariant.MAIN else f"{file_list_no}_low"
    snapshot = ReleaseSnapshot.create(
        variant,
        (
            _ab(
                _entry(
                    logical_path=logical_path,
                    variant=variant,
                    object_version=object_version,
                )
            ),
        ),
    )
    payload = ReleaseManifestPayload(
        schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
        variant=variant,
        file_list_no=file_list_no,
        snapshot=snapshot,
        source_manifest_ids=source_manifest_ids,
    )
    return ReleaseManifestFactory.create(payload)


def _bundle_payload(
    *,
    schema_version: int = RELEASE_BUNDLE_SCHEMA_VERSION,
    manifests: tuple[Any, ...],
    baseline_bundle_id: str | None = None,
) -> ReleaseBundlePayload:
    """组装已校验的 ``ReleaseBundlePayload``。"""
    return ReleaseBundlePayload(
        schema_version=schema_version,
        manifests=manifests,
        baseline_bundle_id=baseline_bundle_id,
    )


def test_release_bundle_factory_stabilizes_unordered_manifests_and_hashes_all_identity_fields() -> (
    None
):
    """验证无序 manifest 稳定化，且身份字段任一变化都会改变 ID。

    测试无参数和返回值。断言：

    - 交换 main/low 输入顺序不改变 ``bundle_id``；
    - schema、main ID、low 存在/ID、baseline 任一变化改变 ID；
    - 直接构造 ``ReleaseBundle`` 失败。

    当 ``release.bundles`` / ``bundle_codec`` 尚未创建时，测试收集
    阶段应以 ``ModuleNotFoundError`` 失败。除导入外不产生外部副作用。
    """
    main = _make_manifest(
        variant=ResourceVariant.MAIN,
        source_manifest_ids=("main-src",),
    )
    low = _make_manifest(
        variant=ResourceVariant.LOW,
        source_manifest_ids=("low-src",),
    )

    payload1 = _bundle_payload(
        manifests=(main, low),
        baseline_bundle_id="baseline-1",
    )
    payload2 = _bundle_payload(
        manifests=(low, main),
        baseline_bundle_id="baseline-1",
    )

    bundle1 = ReleaseBundleFactory.create(payload1)
    bundle2 = ReleaseBundleFactory.create(payload2)
    assert bundle1.bundle_id == bundle2.bundle_id
    expected_id = hashlib.sha256(
        canonical_json_bytes(release_bundle_payload_dict(payload1))
    ).hexdigest()
    assert bundle1.bundle_id == expected_id
    assert len(bundle1.bundle_id) == 64

    with pytest.raises(TypeError):
        ReleaseBundle(bundle_id="", payload=payload1)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        ReleaseBundle(
            bundle_id=expected_id,
            payload=payload1,
        )  # type: ignore[call-arg]

    # schema 变化。
    schema_mutated = _bundle_payload(
        schema_version=RELEASE_BUNDLE_SCHEMA_VERSION + 1,
        manifests=(main, low),
        baseline_bundle_id="baseline-1",
    )
    assert ReleaseBundleFactory.create(schema_mutated).bundle_id != bundle1.bundle_id

    # main ID 变化（不同 source → 不同 manifest_id）。
    main_other = _make_manifest(
        variant=ResourceVariant.MAIN,
        source_manifest_ids=("main-CHANGED",),
    )
    main_mutated = _bundle_payload(
        manifests=(main_other, low),
        baseline_bundle_id="baseline-1",
    )
    assert ReleaseBundleFactory.create(main_mutated).bundle_id != bundle1.bundle_id

    # low 存在性变化：去掉 low。
    no_low = _bundle_payload(
        manifests=(main,),
        baseline_bundle_id="baseline-1",
    )
    assert ReleaseBundleFactory.create(no_low).bundle_id != bundle1.bundle_id

    # low ID 变化。
    low_other = _make_manifest(
        variant=ResourceVariant.LOW,
        source_manifest_ids=("low-CHANGED",),
    )
    low_mutated = _bundle_payload(
        manifests=(main, low_other),
        baseline_bundle_id="baseline-1",
    )
    assert ReleaseBundleFactory.create(low_mutated).bundle_id != bundle1.bundle_id

    # baseline 变化。
    baseline_mutated = _bundle_payload(
        manifests=(main, low),
        baseline_bundle_id="baseline-CHANGED",
    )
    assert ReleaseBundleFactory.create(baseline_mutated).bundle_id != bundle1.bundle_id


def test_read_release_bundle_rejects_empty_stale_or_unknown_schema(
    tmp_path: Path,
) -> None:
    """验证原子写读 round-trip，并拒绝空/陈旧 ID、未知 schema 与不变量破坏。

    参数：
        tmp_path: pytest 临时目录。

    返回：
        无。断言读写成功后 ID 一致；空 ID、陈旧 ID、未知 schema、重复 variant、
        FileListNo 不一致均抛 ``PublishError``，不返回半合法对象。

    当编解码模块尚未创建时，收集阶段应以 ``ModuleNotFoundError`` 失败。
    仅向临时目录写文件。
    """
    main = _make_manifest(variant=ResourceVariant.MAIN)
    low = _make_manifest(variant=ResourceVariant.LOW)
    payload = _bundle_payload(
        manifests=(main, low),
        baseline_bundle_id="baseline-rt",
    )
    bundle = ReleaseBundleFactory.create(payload)
    path = tmp_path / "release_bundle.json"

    write_release_bundle(bundle, path)
    assert path.is_file()
    leftovers = list(tmp_path.glob("*.tmp*")) + list(tmp_path.glob(".*tmp*"))
    assert leftovers == []

    loaded = read_release_bundle(path)
    assert loaded.bundle_id == bundle.bundle_id
    assert loaded.payload.schema_version == bundle.payload.schema_version
    assert loaded.payload.baseline_bundle_id == bundle.payload.baseline_bundle_id
    loaded_ids = {m.manifest_id for m in loaded.payload.manifests}
    original_ids = {m.manifest_id for m in bundle.payload.manifests}
    assert loaded_ids == original_ids

    empty_id_doc = {
        "bundle_id": "",
        "payload": release_bundle_payload_dict(payload),
    }
    path.write_text(json.dumps(empty_id_doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_bundle(path)

    stale_doc = {
        "bundle_id": "0" * 64,
        "payload": release_bundle_payload_dict(payload),
    }
    path.write_text(json.dumps(stale_doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_bundle(path)

    unknown_schema = {
        "bundle_id": bundle.bundle_id,
        "payload": {
            **release_bundle_payload_dict(payload),
            "schema_version": 999,
        },
    }
    path.write_text(json.dumps(unknown_schema, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_bundle(path)

    # 重复 variant：磁盘上塞入两个 main（篡改 payload）。
    encoded = release_bundle_payload_dict(payload)
    manifests_doc = cast(list[dict[str, Any]], encoded["manifests"])
    main_item = next(
        item
        for item in manifests_doc
        if cast(dict[str, Any], item["payload"])["variant"] == ResourceVariant.MAIN.value
    )
    dup_variant_doc = {
        "bundle_id": bundle.bundle_id,
        "payload": {
            **encoded,
            "manifests": [main_item, main_item],
        },
    }
    path.write_text(json.dumps(dup_variant_doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_bundle(path)

    # FileListNo 不一致：把 low 的 file_list_no 改掉（需同步 CURRENT_UPLOAD 版本）。
    low_item = next(
        item
        for item in manifests_doc
        if cast(dict[str, Any], item["payload"])["variant"] == ResourceVariant.LOW.value
    )
    tampered_low = json.loads(json.dumps(low_item))
    tampered_low["payload"]["file_list_no"] = 999
    for entry in tampered_low["payload"]["snapshot"]["entries"]:
        if entry["release_entry"]["object_origin"] == "current_upload":
            entry["release_entry"]["object_version"] = "999_low"
    # 故意留下陈旧 manifest_id，读路径应在 payload 校验或 ID 重算处失败。
    mismatch_doc = {
        "bundle_id": bundle.bundle_id,
        "payload": {
            **encoded,
            "manifests": [main_item, tampered_low],
        },
    }
    path.write_text(json.dumps(mismatch_doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PublishError):
        read_release_bundle(path)
