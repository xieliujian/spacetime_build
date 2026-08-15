"""验证协议无关 ``ReleaseSnapshot`` 分类、membership 与交叉引用。

本模块覆盖第二阶段 Task 13 Step 1：快照锁定单一 ``ResourceVariant``，保留有序
重复 AB 依赖，并用 ``RedirectSlice`` 表达容器路径/Blob/偏移/长度；校验非法
分类-membership、依赖/容器缺失、Blob 不匹配与越界。测试不访问 SVN、Unity、
Jenkins、CDN，也不导入 ``compatibility``。
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
from release.snapshots import (
    RedirectSlice,
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64
_MD5_A = "1" * 32
_MD5_B = "2" * 32
_MD5_C = "3" * 32
_MD5_D = "4" * 32

_BOTH = frozenset({ReleaseMembership.FILE_LIST, ReleaseMembership.ASSET_BUNDLE_DATABASE})
_FILE_ONLY = frozenset({ReleaseMembership.FILE_LIST})
_AB_DB_ONLY = frozenset({ReleaseMembership.ASSET_BUNDLE_DATABASE})


def _blob(sha256: str, *, size: int) -> BlobRef:
    """构造测试用持久 ``BlobRef``。

    参数：
        sha256: 64 位小写十六进制内容哈希。
        size: Blob 字节大小。

    返回：
        合法内容寻址 Blob 引用。
    """
    return BlobRef(
        locator=f"sha256:{sha256}",
        sha256=sha256,
        size=size,
    )


def _entry(
    *,
    logical_path: str,
    variant: ResourceVariant = ResourceVariant.MAIN,
    source_sha: str = _SHA_A,
    transfer_sha: str = _SHA_B,
    source_md5: str = _MD5_A,
    original_size: int = 100,
    transfer_size: int = 80,
) -> ReleaseEntry:
    """构造测试用 ``ReleaseEntry``。

    参数：
        logical_path: 客户端逻辑路径。
        variant: 主/低清变体。
        source_sha: 源内容 SHA256。
        transfer_sha: 传输内容 SHA256。
        source_md5: 原始 MD5。
        original_size: 原始大小。
        transfer_size: 传输大小。

    返回：
        合法的 ``CURRENT_UPLOAD`` 发布条目。
    """
    return ReleaseEntry(
        logical_path=logical_path,
        variant=variant,
        source_blob=_blob(source_sha, size=original_size),
        source_md5=source_md5,
        original_size=original_size,
        transfer_blob=_blob(transfer_sha, size=transfer_size),
        transfer_size=transfer_size,
        list_version=1,
        object_version=("{current}" if variant is ResourceVariant.MAIN else "{current}_low"),
        file_url=f"https://cdn.example/{logical_path}",
        subpackage_flag=0,
        object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
    )


def _snap_entry(
    release_entry: ReleaseEntry,
    *,
    artifact_class: ReleaseArtifactClass,
    memberships: frozenset[ReleaseMembership],
    assetbundle_dependencies: tuple[str, ...] = (),
    redirect_slice: RedirectSlice | None = None,
) -> ReleaseSnapshotEntry:
    """组装 ``ReleaseSnapshotEntry``。

    参数：
        release_entry: 底层发布条目。
        artifact_class: 协议无关产物分类。
        memberships: 发布成员资格集合。
        assetbundle_dependencies: 有序 AB 依赖。
        redirect_slice: 可选 Redirect 切片。

    返回：
        快照条目实例。
    """
    return ReleaseSnapshotEntry(
        release_entry=release_entry,
        artifact_class=artifact_class,
        memberships=memberships,
        assetbundle_dependencies=assetbundle_dependencies,
        redirect_slice=redirect_slice,
    )


def test_release_snapshot_locks_variant_and_classifies_publication_membership() -> None:
    """验证单 variant 锁定、依赖保序、Redirect 与 membership 分类规则。

    测试无参数和返回值。断言：

    - snapshot 锁定单一 ``ResourceVariant``，混入另一 variant 失败；
    - 依赖 ``("b", "a", "b")`` 原样保留；
    - Redirect 保存容器路径/Blob/offset/length；
    - 被替代原 AB 为 ``REDIRECT_SLICE`` 且不具 ``FILE_LIST``；
    - Redirect 容器为 ``REDIRECT_CONTAINER``，路径唯一且同时具
      ``FILE_LIST``/``ASSET_BUNDLE_DATABASE``；
    - 普通文件没有 ``ASSET_BUNDLE_DATABASE``；
    - 依赖目标/容器缺失、Blob 不匹配、越界或非法分类-membership 组合均失败。

    当 ``release.snapshots`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除临时断言外不产生外部副作用。
    """
    container_blob = _blob(_SHA_C, size=1000)
    container_entry = _entry(
        logical_path="packs/container.ab",
        source_sha=_SHA_C,
        transfer_sha=_SHA_C,
        source_md5=_MD5_C,
        original_size=1000,
        transfer_size=1000,
    )
    # 被 Redirect 替代的原 AB：仅进 AB 数据库，依赖保留重复。
    redirected_ab = _snap_entry(
        _entry(
            logical_path="scene/old.ab",
            source_sha=_SHA_A,
            transfer_sha=_SHA_A,
            source_md5=_MD5_A,
            original_size=200,
            transfer_size=200,
        ),
        artifact_class=ReleaseArtifactClass.REDIRECT_SLICE,
        memberships=_AB_DB_ONLY,
        assetbundle_dependencies=("b", "a", "b"),
        redirect_slice=RedirectSlice(
            container_logical_path="packs/container.ab",
            container=container_blob,
            offset=10,
            length=200,
        ),
    )
    container = _snap_entry(
        container_entry,
        artifact_class=ReleaseArtifactClass.REDIRECT_CONTAINER,
        memberships=_BOTH,
    )
    regular = _snap_entry(
        _entry(
            logical_path="config/settings.json",
            source_sha=_SHA_D,
            transfer_sha=_SHA_D,
            source_md5=_MD5_D,
            original_size=50,
            transfer_size=50,
        ),
        artifact_class=ReleaseArtifactClass.REGULAR_FILE,
        memberships=_FILE_ONLY,
    )
    # 依赖目标必须出现在快照中（可仅为普通文件或 AB）。
    dep_a = _snap_entry(
        _entry(
            logical_path="a",
            source_sha=_SHA_B,
            transfer_sha=_SHA_B,
            source_md5=_MD5_B,
            original_size=10,
            transfer_size=10,
        ),
        artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
        memberships=_BOTH,
    )
    dep_b = _snap_entry(
        _entry(
            logical_path="b",
            source_sha=_SHA_D,
            transfer_sha=_SHA_D,
            source_md5=_MD5_D,
            original_size=11,
            transfer_size=11,
        ),
        artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
        memberships=_BOTH,
    )

    snapshot = ReleaseSnapshot.create(
        ResourceVariant.MAIN,
        (redirected_ab, container, regular, dep_a, dep_b),
    )

    assert snapshot.variant is ResourceVariant.MAIN
    by_path = {item.release_entry.logical_path: item for item in snapshot.entries}
    assert by_path["scene/old.ab"].assetbundle_dependencies == ("b", "a", "b")
    slice_info = by_path["scene/old.ab"].redirect_slice
    assert slice_info is not None
    assert slice_info.container_logical_path == "packs/container.ab"
    assert slice_info.container == container_blob
    assert slice_info.offset == 10
    assert slice_info.length == 200
    assert by_path["scene/old.ab"].artifact_class is (ReleaseArtifactClass.REDIRECT_SLICE)
    assert ReleaseMembership.FILE_LIST not in by_path["scene/old.ab"].memberships
    assert ReleaseMembership.ASSET_BUNDLE_DATABASE in (by_path["scene/old.ab"].memberships)

    assert by_path["packs/container.ab"].artifact_class is (ReleaseArtifactClass.REDIRECT_CONTAINER)
    assert by_path["packs/container.ab"].memberships == _BOTH
    container_paths = [
        item.release_entry.logical_path
        for item in snapshot.entries
        if item.artifact_class is ReleaseArtifactClass.REDIRECT_CONTAINER
    ]
    assert container_paths == ["packs/container.ab"]

    assert (
        ReleaseMembership.ASSET_BUNDLE_DATABASE not in by_path["config/settings.json"].memberships
    )

    # 混入另一 variant 必须失败。
    low_entry = _snap_entry(
        _entry(
            logical_path="other/low.ab",
            variant=ResourceVariant.LOW,
            source_sha=_SHA_A,
            transfer_sha=_SHA_A,
            original_size=20,
            transfer_size=20,
        ),
        artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
        memberships=_BOTH,
    )
    with pytest.raises(PublishError):
        ReleaseSnapshot.create(
            ResourceVariant.MAIN,
            (dep_a, dep_b, low_entry),
        )

    # 依赖目标缺失。
    with pytest.raises(PublishError):
        ReleaseSnapshot.create(
            ResourceVariant.MAIN,
            (redirected_ab, container, regular),
        )

    # 容器路径缺失。
    orphan_slice = _snap_entry(
        _entry(
            logical_path="scene/orphan.ab",
            source_sha=_SHA_A,
            transfer_sha=_SHA_A,
            original_size=50,
            transfer_size=50,
        ),
        artifact_class=ReleaseArtifactClass.REDIRECT_SLICE,
        memberships=_AB_DB_ONLY,
        redirect_slice=RedirectSlice(
            container_logical_path="packs/missing.ab",
            container=container_blob,
            offset=0,
            length=10,
        ),
    )
    with pytest.raises(PublishError):
        ReleaseSnapshot.create(ResourceVariant.MAIN, (orphan_slice, dep_a, dep_b))

    # Blob 与容器条目不一致。
    wrong_blob_slice = _snap_entry(
        _entry(
            logical_path="scene/wrongblob.ab",
            source_sha=_SHA_A,
            transfer_sha=_SHA_A,
            original_size=50,
            transfer_size=50,
        ),
        artifact_class=ReleaseArtifactClass.REDIRECT_SLICE,
        memberships=_AB_DB_ONLY,
        redirect_slice=RedirectSlice(
            container_logical_path="packs/container.ab",
            container=_blob(_SHA_D, size=1000),
            offset=0,
            length=10,
        ),
    )
    with pytest.raises(PublishError):
        ReleaseSnapshot.create(
            ResourceVariant.MAIN,
            (wrong_blob_slice, container, dep_a, dep_b),
        )

    # 切片越界。
    oob_slice = _snap_entry(
        _entry(
            logical_path="scene/oob.ab",
            source_sha=_SHA_A,
            transfer_sha=_SHA_A,
            original_size=50,
            transfer_size=50,
        ),
        artifact_class=ReleaseArtifactClass.REDIRECT_SLICE,
        memberships=_AB_DB_ONLY,
        redirect_slice=RedirectSlice(
            container_logical_path="packs/container.ab",
            container=container_blob,
            offset=900,
            length=200,
        ),
    )
    with pytest.raises(PublishError):
        ReleaseSnapshot.create(
            ResourceVariant.MAIN,
            (oob_slice, container, dep_a, dep_b),
        )

    # 非法分类-membership：普通文件不得进入 AB 数据库。
    illegal_regular = _snap_entry(
        _entry(
            logical_path="config/bad.json",
            source_sha=_SHA_D,
            transfer_sha=_SHA_D,
            source_md5=_MD5_D,
            original_size=5,
            transfer_size=5,
        ),
        artifact_class=ReleaseArtifactClass.REGULAR_FILE,
        memberships=_BOTH,
    )
    with pytest.raises(PublishError):
        ReleaseSnapshot.create(ResourceVariant.MAIN, (illegal_regular,))

    # REDIRECT_SLICE 不得带 FILE_LIST。
    illegal_slice = _snap_entry(
        _entry(
            logical_path="scene/listed_slice.ab",
            source_sha=_SHA_A,
            transfer_sha=_SHA_A,
            original_size=50,
            transfer_size=50,
        ),
        artifact_class=ReleaseArtifactClass.REDIRECT_SLICE,
        memberships=_BOTH,
        redirect_slice=RedirectSlice(
            container_logical_path="packs/container.ab",
            container=container_blob,
            offset=0,
            length=10,
        ),
    )
    with pytest.raises(PublishError):
        ReleaseSnapshot.create(
            ResourceVariant.MAIN,
            (illegal_slice, container),
        )
