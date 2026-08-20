"""验证 AssetBundle DTO 从 ReleaseSnapshot 单向派生及 Redirect 表达。"""

from dataclasses import replace

import pytest

from compatibility.assetbundle_dto import (
    AssetBundleDatabase,
    AssetBundleRecord,
    AssetBundleRedirectRecord,
    assetbundle_records_from_release_snapshot,
)
from core.errors import CompatibilityError
from release.snapshots import (
    ReleaseArtifactClass,
    RedirectSlice,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)

from .conftest import AB_ONLY, blob, release_entry


def test_assetbundle_dto_is_derived_from_validated_release_snapshot() -> None:
    """验证普通文件被过滤，AssetBundle 依赖顺序和重复被保留。"""
    snapshot = ReleaseSnapshot.create(
        __import__("release.entries", fromlist=["ResourceVariant"]).ResourceVariant.MAIN,
        (
            release_entry(
                "scene/a.assetbundle",
                dependencies=("scene/b.assetbundle", "scene/a.assetbundle", "scene/b.assetbundle"),
            ),
            release_entry(
                "scene/b.assetbundle",
                dependencies=(),
            ),
            release_entry(
                "config/settings.json",
                artifact_class=ReleaseArtifactClass.REGULAR_FILE,
            ),
        ),
    )
    records = assetbundle_records_from_release_snapshot(snapshot)
    assert [record.name for record in records] == ["scene/a.assetbundle", "scene/b.assetbundle"]
    assert records[0].dependencies == (
        "scene/b.assetbundle",
        "scene/a.assetbundle",
        "scene/b.assetbundle",
    )


def test_assetbundle_dto_represents_redirect_membership_once() -> None:
    """验证 Redirect slice 引用容器，容器自身只形成一条记录。"""
    container_entry = release_entry(
        "scene/redirect/container.assetbundle",
        artifact_class=ReleaseArtifactClass.REDIRECT_CONTAINER,
        source_sha="b" * 64,
        transfer_sha="b" * 64,
        source_size=10,
        transfer_size=10,
    )
    original_entry = release_entry(
        "scene/a.assetbundle",
        artifact_class=ReleaseArtifactClass.REDIRECT_SLICE,
        dependencies=("scene/redirect/container.assetbundle",),
    )
    original_entry = ReleaseSnapshotEntry(
        release_entry=original_entry.release_entry,
        artifact_class=original_entry.artifact_class,
        memberships=AB_ONLY,
        assetbundle_dependencies=original_entry.assetbundle_dependencies,
        redirect_slice=RedirectSlice(
            container_logical_path="scene/redirect/container.assetbundle",
            container=blob("b" * 64, size=10),
            offset=0,
            length=3,
        ),
    )
    snapshot = ReleaseSnapshot.create(
        __import__("release.entries", fromlist=["ResourceVariant"]).ResourceVariant.MAIN,
        (original_entry, container_entry),
    )
    records = assetbundle_records_from_release_snapshot(snapshot)
    assert len(records) == 2
    assert records[0].redirect is not None
    assert records[0].redirect.container_name == "scene/redirect/container.assetbundle"
    assert records[1].name == "scene/redirect/container.assetbundle"


def test_assetbundle_dto_cannot_be_directly_constructed_or_replaced() -> None:
    """验证三类协议 DTO 不能被调用方直接构造或替换。"""
    snapshot = ReleaseSnapshot.create(
        __import__("release.entries", fromlist=["ResourceVariant"]).ResourceVariant.MAIN,
        (release_entry("scene/a.assetbundle"),),
    )
    record = assetbundle_records_from_release_snapshot(snapshot)[0]
    with pytest.raises(TypeError):
        AssetBundleRecord("scene/a.assetbundle", (), None)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        AssetBundleRedirectRecord("scene/c.assetbundle", 0, 1)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        AssetBundleDatabase("assetbundledb_scene.txt", (record,))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        replace(record, name="scene/b.assetbundle")


def test_assetbundle_dto_rejects_invalid_protocol_path() -> None:
    """验证快照中的控制字符路径在兼容边界被显式拒绝。"""
    snapshot = ReleaseSnapshot.create(
        __import__("release.entries", fromlist=["ResourceVariant"]).ResourceVariant.MAIN,
        (release_entry("scene/a.assetbundle"),),
    )
    object.__setattr__(snapshot.entries[0].release_entry, "logical_path", "scene/a\n.assetbundle")
    with pytest.raises(CompatibilityError):
        assetbundle_records_from_release_snapshot(snapshot)
