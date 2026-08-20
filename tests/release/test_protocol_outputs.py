"""兼容协议 Blob 登记测试。"""

import hashlib

from compatibility.line_endings import LineEnding
from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from release.assembly import ReleaseAssemblyItem, ReleaseAssembler
from release.entries import ResourceVariant
from release.protocol_outputs import ProtocolOutputBuilder


def test_protocol_output_builder_uses_compatibility_writers_for_main() -> None:
    """验证五库和主清文件列表均以确定性 Blob 输出。"""
    main = ReleaseAssembler.assemble(
        ResourceVariant.MAIN,
        3,
        ("build-main",),
        (ReleaseAssemblyItem(_artifact("scene/a.assetbundle", b"a"), "a" * 32),),
    ).manifest
    outputs = ProtocolOutputBuilder.build((main,), LineEnding.LF)
    assert len(outputs) == 6
    assert all(output.content == b"" or output.content.endswith(b"\n") for output in outputs)
    assert all(
        output.blob.sha256 == hashlib.sha256(output.content).hexdigest() for output in outputs
    )


def _artifact(path: str, content: bytes) -> LogicalArtifact:
    """构造测试 AssetBundle。"""
    digest = hashlib.sha256(content).hexdigest()
    return LogicalArtifact(
        path,
        ArtifactKind.ASSET_BUNDLE,
        BlobRef("blobs/" + digest, digest, len(content)),
        (),
        frozenset(),
        ArtifactMetadata("scene", "1", "tool", ()),
    )
