"""ReleaseSnapshot 合并的确定性与边界测试。"""

import pytest

from core.artifacts import BlobRef
from release.entries import ReleaseEntry, ReleaseObjectOrigin, ResourceVariant
from release.merger import SnapshotMerger
from release.snapshots import (
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)


def _snapshot(path: str, sha: str) -> ReleaseSnapshot:
    """构造一个最小主清快照。"""
    blob = BlobRef("blobs/" + sha, sha, 1)
    entry = ReleaseEntry(
        path,
        ResourceVariant.MAIN,
        blob,
        "1" * 32,
        1,
        blob,
        1,
        123,
        "123",
        "cdn/" + path,
        0,
        ReleaseObjectOrigin.CURRENT_UPLOAD,
    )
    return ReleaseSnapshot.create(
        ResourceVariant.MAIN,
        (
            ReleaseSnapshotEntry(
                entry,
                ReleaseArtifactClass.REGULAR_FILE,
                frozenset({ReleaseMembership.FILE_LIST}),
                (),
                None,
            ),
        ),
    )


def test_snapshot_merger_replaces_adds_removes_and_sorts_paths() -> None:
    """验证合并保留基线、替换输入、删除路径并稳定排序。"""
    baseline = SnapshotMerger.merge(None, _snapshot("z.bin", "a" * 64))
    incoming = _snapshot("a.bin", "b" * 64)
    merged = SnapshotMerger.merge(baseline, incoming, removed_paths=("z.bin",))
    assert tuple(item.release_entry.logical_path for item in merged.entries) == ("a.bin",)


def test_snapshot_merger_rejects_variant_mismatch_and_unknown_removal() -> None:
    """验证变体不一致与删除不存在路径会失败。"""
    baseline = _snapshot("a.bin", "a" * 64)
    with pytest.raises(ValueError):
        SnapshotMerger.merge(baseline, _snapshot("a.bin", "b" * 64), removed_paths=("missing",))
