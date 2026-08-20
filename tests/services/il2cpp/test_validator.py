"""IL2CPP 输出归档的摘要、文件表和必需文件验证测试。"""

from pathlib import Path

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from services.il2cpp.archive import Il2CppArchiveCodec
from services.il2cpp.model import Il2CppBuildRequest, Il2CppExecutionMode
from services.il2cpp.validator import Il2CppOutputValidator


def _request() -> Il2CppBuildRequest:
    """构造固定 Android IL2CPP 请求。"""
    digest = "a" * 64
    return Il2CppBuildRequest(
        "request-1",
        BuildPlatform.ANDROID,
        "arm64-v8a",
        BlobRef(f"blobs/{digest}", digest, 10),
        "2022.3.62f2",
        "toolchain-digest",
        Il2CppExecutionMode.LOCAL,
        None,
    )


def test_validator_accepts_complete_archive_and_reports_deterministic_entries(
    tmp_path: Path,
) -> None:
    """Given 完整输出归档，When validate，Then 返回有效且排序后的报告。"""
    source = tmp_path / "source"
    source.joinpath("lib", "arm64-v8a").mkdir(parents=True)
    source.joinpath("metadata").mkdir()
    source.joinpath("lib", "arm64-v8a", "libil2cpp.so").write_bytes(b"library")
    source.joinpath("metadata", "global-metadata.dat").write_bytes(b"metadata")
    archive = Il2CppArchiveCodec.create(source, tmp_path / "output.zip")

    report = Il2CppOutputValidator.validate(
        archive,
        _request(),
        required_files=("metadata/global-metadata.dat", "lib/arm64-v8a/libil2cpp.so"),
    )

    assert report.valid is True
    assert report.errors == ()
    assert tuple(item.path for item in report.entries) == (
        "lib/arm64-v8a/libil2cpp.so",
        "metadata/global-metadata.dat",
    )


def test_validator_reports_missing_file_and_archive_digest_mismatch(tmp_path: Path) -> None:
    """验证缺少必需文件或归档摘要被篡改时返回无效报告。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "metadata.dat").write_bytes(b"metadata")
    archive = Il2CppArchiveCodec.create(source, tmp_path / "output.zip")
    archive.path.write_bytes(b"tampered")

    report = Il2CppOutputValidator.validate(
        archive,
        _request(),
        required_files=("metadata/global-metadata.dat",),
    )

    assert report.valid is False
    assert any("摘要" in error for error in report.errors)
    assert any("必需文件" in error for error in report.errors)
