"""验证 ``ReleaseBundlePayload`` 的 main/low 组合与共享 FileListNo 契约。

本模块覆盖第二阶段 Task 14 Step 1：payload 不含 ID，无序 manifest 集合必须恰有
一个 main、至多一个 low，并共享 ``file_list_no``；历史低清 ``object_version``
不被 bundle 二次拒绝。测试不访问外部系统，也不导入 ``compatibility``。
"""

from __future__ import annotations

import pytest

from core.artifacts import BlobRef
from core.errors import PublishError
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
from release.bundles import (
    RELEASE_BUNDLE_SCHEMA_VERSION,
    ReleaseBundlePayload,
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
_MD5_B = "2" * 32
_BOTH = frozenset({ReleaseMembership.FILE_LIST, ReleaseMembership.ASSET_BUNDLE_DATABASE})


def _blob(sha256: str, *, size: int = 100) -> BlobRef:
    """构造测试用 ``BlobRef``。"""
    return BlobRef(locator=f"sha256:{sha256}", sha256=sha256, size=size)


def _entry(
    *,
    logical_path: str,
    variant: ResourceVariant,
    object_version: str,
    object_origin: ReleaseObjectOrigin,
    source_sha: str = _SHA_A,
    transfer_sha: str = _SHA_B,
    source_md5: str = _MD5_A,
) -> ReleaseEntry:
    """构造带指定对象版本的 ``ReleaseEntry``。"""
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


def _ab_entry(
    release_entry: ReleaseEntry,
    *,
    deps: tuple[str, ...] = (),
) -> ReleaseSnapshotEntry:
    """将条目包装为 AssetBundle 快照项。"""
    return ReleaseSnapshotEntry(
        release_entry=release_entry,
        artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
        memberships=_BOTH,
        assetbundle_dependencies=deps,
        redirect_slice=None,
    )


def _make_manifest(
    *,
    variant: ResourceVariant,
    file_list_no: int,
    entries: tuple[ReleaseSnapshotEntry, ...],
    source_manifest_ids: tuple[str, ...] = ("build-src",),
):
    """组装并经工厂创建的 ``ReleaseManifest``。"""
    snapshot = ReleaseSnapshot.create(variant, entries)
    payload = ReleaseManifestPayload(
        schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
        variant=variant,
        file_list_no=file_list_no,
        snapshot=snapshot,
        source_manifest_ids=source_manifest_ids,
    )
    return ReleaseManifestFactory.create(payload)


def test_release_bundle_payload_accepts_one_main_and_optional_low_with_shared_file_list_no() -> (
    None
):
    """验证 payload 不含 ID，且强制 one-main / optional-low / 共享 FileListNo。

    测试无参数和返回值。断言：

    - payload 字段不含 bundle ID；
    - 恰有一个 ``MAIN`` manifest，可附带一个 ``LOW``；
    - 所有 manifest 共享同一 ``file_list_no``；
    - 低清 ``HISTORICAL`` 的历史 ``object_version`` 不被 bundle 二次拒绝；
    - 缺少 main、两个 main、两个 low、FileListNo 不一致均失败。

    当 ``release.bundles`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入外不产生外部副作用。
    """
    file_list_no = 123
    main_manifest = _make_manifest(
        variant=ResourceVariant.MAIN,
        file_list_no=file_list_no,
        entries=(
            _ab_entry(
                _entry(
                    logical_path="scene/a.ab",
                    variant=ResourceVariant.MAIN,
                    object_version="123",
                    object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
                )
            ),
        ),
        source_manifest_ids=("main-src",),
    )

    # 仅 main：合法。
    main_only = ReleaseBundlePayload(
        schema_version=RELEASE_BUNDLE_SCHEMA_VERSION,
        manifests=(main_manifest,),
        baseline_bundle_id=None,
    )
    assert not hasattr(main_only, "bundle_id") or "bundle_id" not in getattr(
        main_only, "__dataclass_fields__", {}
    )
    assert len(main_only.manifests) == 1
    assert main_only.manifests[0].payload.variant is ResourceVariant.MAIN
    assert main_only.baseline_bundle_id is None

    # low 含 CURRENT_UPLOAD 与 HISTORICAL；历史版本不得被 bundle 二次拒绝。
    low_manifest = _make_manifest(
        variant=ResourceVariant.LOW,
        file_list_no=file_list_no,
        entries=(
            _ab_entry(
                _entry(
                    logical_path="scene/a.ab",
                    variant=ResourceVariant.LOW,
                    object_version="123_low",
                    object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
                )
            ),
            _ab_entry(
                _entry(
                    logical_path="scene/old.ab",
                    variant=ResourceVariant.LOW,
                    object_version="20240101_120000_low",
                    object_origin=ReleaseObjectOrigin.HISTORICAL,
                    source_sha=_SHA_B,
                    transfer_sha=_SHA_A,
                    source_md5=_MD5_B,
                )
            ),
        ),
        source_manifest_ids=("low-src",),
    )
    with_low = ReleaseBundlePayload(
        schema_version=RELEASE_BUNDLE_SCHEMA_VERSION,
        manifests=(low_manifest, main_manifest),
        baseline_bundle_id="baseline-aaa",
    )
    assert with_low.baseline_bundle_id == "baseline-aaa"
    variants = {m.payload.variant for m in with_low.manifests}
    assert variants == {ResourceVariant.MAIN, ResourceVariant.LOW}
    low_hist = next(
        item.release_entry.object_version
        for m in with_low.manifests
        if m.payload.variant is ResourceVariant.LOW
        for item in m.payload.snapshot.entries
        if item.release_entry.logical_path == "scene/old.ab"
    )
    assert low_hist == "20240101_120000_low"

    # 缺少 main。
    with pytest.raises(PublishError):
        ReleaseBundlePayload(
            schema_version=RELEASE_BUNDLE_SCHEMA_VERSION,
            manifests=(low_manifest,),
            baseline_bundle_id=None,
        )

    # 两个 main。
    main_dup = _make_manifest(
        variant=ResourceVariant.MAIN,
        file_list_no=file_list_no,
        entries=(
            _ab_entry(
                _entry(
                    logical_path="scene/b.ab",
                    variant=ResourceVariant.MAIN,
                    object_version="123",
                    object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
                )
            ),
        ),
        source_manifest_ids=("main-dup",),
    )
    with pytest.raises(PublishError):
        ReleaseBundlePayload(
            schema_version=RELEASE_BUNDLE_SCHEMA_VERSION,
            manifests=(main_manifest, main_dup),
            baseline_bundle_id=None,
        )

    # 两个 low。
    low_dup = _make_manifest(
        variant=ResourceVariant.LOW,
        file_list_no=file_list_no,
        entries=(
            _ab_entry(
                _entry(
                    logical_path="scene/b.ab",
                    variant=ResourceVariant.LOW,
                    object_version="123_low",
                    object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
                )
            ),
        ),
        source_manifest_ids=("low-dup",),
    )
    with pytest.raises(PublishError):
        ReleaseBundlePayload(
            schema_version=RELEASE_BUNDLE_SCHEMA_VERSION,
            manifests=(main_manifest, low_manifest, low_dup),
            baseline_bundle_id=None,
        )

    # FileListNo 不一致。
    low_mismatch = _make_manifest(
        variant=ResourceVariant.LOW,
        file_list_no=124,
        entries=(
            _ab_entry(
                _entry(
                    logical_path="scene/a.ab",
                    variant=ResourceVariant.LOW,
                    object_version="124_low",
                    object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
                )
            ),
        ),
        source_manifest_ids=("low-mismatch",),
    )
    with pytest.raises(PublishError):
        ReleaseBundlePayload(
            schema_version=RELEASE_BUNDLE_SCHEMA_VERSION,
            manifests=(main_manifest, low_mismatch),
            baseline_bundle_id=None,
        )
