"""验证 ``ReleaseManifestPayload`` 变体锁定与 CURRENT_UPLOAD 对象版本契约。

本模块覆盖第二阶段 Task 13 Step 2：payload 不含 ID，要求 snapshot 与条目变体
一致，且 main/low 的 ``CURRENT_UPLOAD`` 分别使用具体 FileListNo /
``{FileListNo}_low``，``HISTORICAL`` 保留历史值。测试不访问外部系统，也不导入
``compatibility``。
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
    list_version: int = 123,
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
        list_version=list_version,
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


def test_release_manifest_payload_locks_variant_and_current_object_versions() -> None:
    """验证 payload 不含 ID，并强制变体与 CURRENT_UPLOAD 具体 FileListNo。

    测试无参数和返回值。断言：

    - payload 字段不含 manifest ID；
    - ``snapshot.variant`` 必须等于 ``payload.variant``；
    - 每个 entry variant 必须一致；
    - main/low 的 ``CURRENT_UPLOAD`` 分别严格使用 ``str(file_list_no)`` /
      ``f"{file_list_no}_low"``；
    - ``HISTORICAL`` 保留历史 ``object_version``；
    - 哨兵或错误 FileListNo 字符串在 payload 构造时失败。

    当 ``release.manifests`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入外不产生外部副作用。
    """
    file_list_no = 123
    main_current = _ab_entry(
        _entry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.MAIN,
            object_version="123",
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        ),
        deps=("b", "a", "b"),
    )
    main_hist = _ab_entry(
        _entry(
            logical_path="scene/b.ab",
            variant=ResourceVariant.MAIN,
            object_version="20240101_120000",
            object_origin=ReleaseObjectOrigin.HISTORICAL,
            source_sha=_SHA_B,
            transfer_sha=_SHA_A,
            source_md5=_MD5_B,
        )
    )
    # 依赖目标也需出现在快照中。
    dep_a = _ab_entry(
        _entry(
            logical_path="a",
            variant=ResourceVariant.MAIN,
            object_version="99",
            object_origin=ReleaseObjectOrigin.HISTORICAL,
            source_sha=_SHA_B,
            transfer_sha=_SHA_B,
            source_md5=_MD5_B,
        )
    )
    dep_b = _ab_entry(
        _entry(
            logical_path="b",
            variant=ResourceVariant.MAIN,
            object_version="98",
            object_origin=ReleaseObjectOrigin.HISTORICAL,
            source_sha=_SHA_A,
            transfer_sha=_SHA_A,
            source_md5=_MD5_A,
        )
    )
    main_snapshot = ReleaseSnapshot.create(
        ResourceVariant.MAIN,
        (main_current, main_hist, dep_a, dep_b),
    )

    payload = ReleaseManifestPayload(
        schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
        variant=ResourceVariant.MAIN,
        file_list_no=file_list_no,
        snapshot=main_snapshot,
        source_manifest_ids=("build-aaa", "build-bbb"),
    )
    assert not hasattr(payload, "manifest_id") or "manifest_id" not in getattr(
        payload, "__dataclass_fields__", {}
    )
    assert payload.variant is ResourceVariant.MAIN
    assert payload.snapshot.variant is ResourceVariant.MAIN
    assert payload.file_list_no == 123
    current_entry = next(
        item for item in payload.snapshot.entries if item.release_entry.logical_path == "scene/a.ab"
    )
    assert current_entry.release_entry.object_version == "123"
    hist_entry = next(
        item for item in payload.snapshot.entries if item.release_entry.logical_path == "scene/b.ab"
    )
    assert hist_entry.release_entry.object_version == "20240101_120000"
    assert current_entry.assetbundle_dependencies == ("b", "a", "b")

    low_current = _ab_entry(
        _entry(
            logical_path="scene/a.ab",
            variant=ResourceVariant.LOW,
            object_version="123_low",
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )
    )
    low_snapshot = ReleaseSnapshot.create(ResourceVariant.LOW, (low_current,))
    low_payload = ReleaseManifestPayload(
        schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
        variant=ResourceVariant.LOW,
        file_list_no=123,
        snapshot=low_snapshot,
        source_manifest_ids=("build-low",),
    )
    assert low_payload.snapshot.entries[0].release_entry.object_version == "123_low"

    # snapshot.variant 与 payload.variant 不一致。
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
            variant=ResourceVariant.LOW,
            file_list_no=123,
            snapshot=main_snapshot,
            source_manifest_ids=("build-aaa",),
        )

    # CURRENT_UPLOAD 仍使用哨兵，未展开为具体 FileListNo。
    sentinel_snap = ReleaseSnapshot.create(
        ResourceVariant.MAIN,
        (
            _ab_entry(
                _entry(
                    logical_path="scene/c.ab",
                    variant=ResourceVariant.MAIN,
                    object_version="{current}",
                    object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
                )
            ),
        ),
    )
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
            variant=ResourceVariant.MAIN,
            file_list_no=123,
            snapshot=sentinel_snap,
            source_manifest_ids=("build-aaa",),
        )

    # CURRENT_UPLOAD 使用错误的 FileListNo 字符串。
    wrong_ver_snap = ReleaseSnapshot.create(
        ResourceVariant.MAIN,
        (
            _ab_entry(
                _entry(
                    logical_path="scene/d.ab",
                    variant=ResourceVariant.MAIN,
                    object_version="999",
                    object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
                )
            ),
        ),
    )
    with pytest.raises(PublishError):
        ReleaseManifestPayload(
            schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
            variant=ResourceVariant.MAIN,
            file_list_no=123,
            snapshot=wrong_ver_snap,
            source_manifest_ids=("build-aaa",),
        )


def test_release_manifest_payload_requires_entry_list_version_to_equal_file_list_no() -> None:
    """验证每个发布条目的 list_version 必须等于 manifest 的 FileListNo。"""
    entry = _ab_entry(
        _entry(
            logical_path="scene/list-version.ab",
            variant=ResourceVariant.MAIN,
            object_version="123",
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
            list_version=1,
        )
    )
    snapshot = ReleaseSnapshot.create(ResourceVariant.MAIN, (entry,))
    with pytest.raises(PublishError, match="list_version"):
        ReleaseManifestPayload(
            schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
            variant=ResourceVariant.MAIN,
            file_list_no=123,
            snapshot=snapshot,
            source_manifest_ids=("build-aaa",),
        )
