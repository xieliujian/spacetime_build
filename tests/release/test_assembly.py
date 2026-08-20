"""ReleaseManifest/Bundle 组装服务测试。"""

import hashlib

import pytest

from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from release.assembly import ReleaseAssemblyItem, ReleaseAssembler
from release.entries import ResourceVariant


def _artifact(path: str, content: bytes, kind: ArtifactKind) -> LogicalArtifact:
    """构造测试逻辑产物。"""
    sha = hashlib.sha256(content).hexdigest()
    return LogicalArtifact(
        path,
        kind,
        BlobRef("blobs/" + sha, sha, len(content)),
        (),
        frozenset(),
        ArtifactMetadata("scene", "123", "tool", ()),
    )


def test_release_assembler_creates_current_manifest_and_bundle() -> None:
    """验证 AssetBundle 与普通文件拥有正确分类并绑定 FileListNo。"""
    result = ReleaseAssembler.assemble(
        variant=ResourceVariant.MAIN,
        file_list_no=123,
        source_manifest_ids=("build-1",),
        items=(
            ReleaseAssemblyItem(
                _artifact("scene/a.ab", b"ab", ArtifactKind.ASSET_BUNDLE), "1" * 32
            ),
            ReleaseAssemblyItem(_artifact("config/a.bin", b"cfg", ArtifactKind.FILE), "2" * 32),
        ),
    )
    assert result.bundle.payload.manifests[0].payload.file_list_no == 123
    assert result.manifest.payload.snapshot.entries[0].release_entry.object_version == "123"


def test_release_assembler_rejects_invalid_source_md5_or_empty_items() -> None:
    """验证源 MD5 和空产物集合不能进入发布组装。"""
    with pytest.raises(ValueError):
        ReleaseAssembler.assemble(ResourceVariant.MAIN, 123, ("build",), ())
    with pytest.raises(ValueError):
        ReleaseAssembler.assemble(
            ResourceVariant.MAIN,
            123,
            ("build",),
            (ReleaseAssemblyItem(_artifact("a", b"a", ArtifactKind.FILE), "bad"),),
        )
