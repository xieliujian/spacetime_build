"""正式版本端到端发布用例的本地 CDN 验收。"""

import hashlib
from pathlib import Path

from application.formal_release import FormalReleaseRequest, FormalReleaseUseCase
from application.model import RunState
from compatibility.line_endings import LineEnding
from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from core.build_records import BuildManifest, BuildManifestPayload
from core.manifest_codec import BuildManifestFactory
from core.platforms import BuildPlatform
from integrations.storage import FileSystemObjectStore
from release.entries import ResourceVariant
from release.remote_verifier import RemoteReleaseVerifier
from release.activator import ReleaseActivator
from release.uploader import ReleaseObjectUploader
from release.versioning import MemoryVersionStateStore, VersionAllocator, version_stream


def _manifest(content: bytes) -> BuildManifest:
    """构造只有一个 AssetBundle 的资源 BuildManifest。"""
    digest = hashlib.sha256(content).hexdigest()
    artifact = LogicalArtifact(
        "scene/main.assetbundle",
        ArtifactKind.ASSET_BUNDLE,
        BlobRef(f"blobs/{digest}", digest, len(content)),
        (),
        frozenset(),
        ArtifactMetadata("scene", "r100", "b" * 64, ()),
    )
    return BuildManifestFactory.create(
        BuildManifestPayload(1, "a" * 64, "r100", "b" * 64, None, (artifact,), ("task",))
    )


def test_formal_release_publishes_versioned_objects_and_entry(tmp_path: Path) -> None:
    """验证资源、兼容协议和版本入口按正式顺序落到本地 CDN。"""
    store = FileSystemObjectStore(tmp_path / "cdn")
    use_case = FormalReleaseUseCase(
        VersionAllocator(MemoryVersionStateStore()),
        ReleaseObjectUploader(store),
        RemoteReleaseVerifier(store),
        ReleaseActivator(store),
    )
    request = FormalReleaseRequest(
        build_manifest=_manifest(b"bundle"),
        artifact_contents={"scene/main.assetbundle": b"bundle"},
        variant=ResourceVariant.MAIN,
        platform=BuildPlatform.WINDOWS.value,
        stream=version_stream("prod", "local-cdn", "windows/entry.json"),
        build_id="jenkins-100",
        request_id="request-100",
        major=1,
        minor=0,
        version_entry_key="windows/entry.json",
        file_list_base_url="https://cdn.example/windows",
        expected_generation=0,
        expected_entry_digest="0" * 64,
        line_ending=LineEnding.LF,
    )

    result = use_case.run(request)

    assert result.state is RunState.SUCCEEDED
    assert result.reservation is not None
    assert result.reservation.file_list_no == 1
    assert result.reservation.status.value == "published"
    assert result.plan is not None
    assert (tmp_path / "cdn" / "windows" / "entry.json").is_file()
    assert (tmp_path / "cdn" / "1" / "scene" / "main.assetbundle").is_file()
    assert result.error is None
