"""Redirect 规划的确定性和兼容策略测试。"""

import hashlib

from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from release.assembly import ReleaseAssemblyItem, ReleaseAssembler
from release.entries import ResourceVariant
from release.redirect import RedirectPlanner


def _snapshot():
    """构造一个包含可重定向和排除 AssetBundle 的快照。"""
    items = (
        ReleaseAssemblyItem(
            _artifact("scene/a.assetbundle", b"a" * (100 * 1024)),
            "a" * 32,
        ),
        ReleaseAssemblyItem(
            _artifact("scene/a-stream.assetbundle", b"b" * (100 * 1024)),
            "b" * 32,
        ),
        ReleaseAssemblyItem(
            _artifact("config/a.bin", b"c"),
            "c" * 32,
        ),
    )
    return ReleaseAssembler.assemble(
        ResourceVariant.MAIN, 7, ("build",), items
    ).manifest.payload.snapshot


def _artifact(path: str, content: bytes) -> LogicalArtifact:
    """构造测试产物。"""
    digest = hashlib.sha256(content).hexdigest()
    return LogicalArtifact(
        path,
        ArtifactKind.ASSET_BUNDLE if path.endswith(".assetbundle") else ArtifactKind.FILE,
        BlobRef("blobs/" + digest, digest, len(content)),
        (),
        frozenset(),
        ArtifactMetadata("scene", "1", "tool", ()),
    )


def test_redirect_planner_is_stable_and_excludes_stream_bundle() -> None:
    """验证阈值、排除规则、偏移和策略版本确定。"""
    plan = RedirectPlanner.plan(_snapshot())
    assert plan.strategy_version == "redirect-v1"
    assert len(plan.slices) == 1
    assert plan.slices[0].logical_path == "scene/a.assetbundle"
    assert plan.slices[0].offset == 0
    assert plan.slices[0].length == 100 * 1024
    assert plan.slices[0].container_logical_path.startswith("scene/redirect/")


def test_redirect_planner_orders_entries_before_assigning_offsets() -> None:
    """验证输入排列不会改变桶内偏移。"""
    snapshot = _snapshot()
    reversed_snapshot = type(snapshot).create(snapshot.variant, tuple(reversed(snapshot.entries)))
    assert RedirectPlanner.plan(snapshot) == RedirectPlanner.plan(reversed_snapshot)
