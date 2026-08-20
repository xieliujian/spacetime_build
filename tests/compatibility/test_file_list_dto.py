"""验证六字段文件列表 DTO 只能从已校验 ReleaseManifest 单向生成。"""

from dataclasses import replace

import pytest

from compatibility.file_list_dto import FileListRow, file_list_rows_from_manifest
from core.errors import CompatibilityError
from release.entries import ReleaseObjectOrigin
from release.snapshots import ReleaseArtifactClass, RedirectSlice, ReleaseSnapshotEntry

from .conftest import AB_ONLY, blob

from .conftest import manifest, release_entry


def test_file_list_rows_derive_all_legacy_fields_from_release_manifest() -> None:
    """验证六字段来自传输大小、源 MD5、对象版本和分包标志。"""
    payload = manifest(
        (
            release_entry(
                "config/空 格.txt",
                artifact_class=ReleaseArtifactClass.REGULAR_FILE,
                source_size=12,
                transfer_size=12,
                source_sha="a" * 64,
                transfer_sha="b" * 64,
                subpackage_flag=4,
            ),
        )
    )
    row = file_list_rows_from_manifest(payload)[0]
    assert (
        row.file_name,
        row.file_version,
        row.file_size,
        row.file_md5,
        row.file_url,
        row.subpackage_flag,
    ) == (
        "config/空 格.txt",
        123,
        12,
        "1" * 32,
        "123/config/%E7%A9%BA%20%E6%A0%BC.txt.zip",
        4,
    )


def test_file_list_rows_follow_membership_and_preserve_historical_url() -> None:
    """验证 Redirect 原条目不出文件列表，容器只出一次且历史 URL 不被改写。"""
    old = release_entry(
        "scene/old.assetbundle",
        artifact_class=ReleaseArtifactClass.REDIRECT_SLICE,
        object_origin=ReleaseObjectOrigin.HISTORICAL,
        object_version="99",
        file_url="99/scene/old.assetbundle",
    )
    old = ReleaseSnapshotEntry(
        release_entry=old.release_entry,
        artifact_class=ReleaseArtifactClass.REDIRECT_SLICE,
        memberships=AB_ONLY,
        assetbundle_dependencies=(),
        redirect_slice=RedirectSlice(
            container_logical_path="scene/redirect/container.assetbundle",
            container=blob("b" * 64, size=100),
            offset=0,
            length=3,
        ),
    )
    payload = manifest(
        (
            old,
            release_entry(
                "scene/redirect/container.assetbundle",
                artifact_class=ReleaseArtifactClass.REDIRECT_CONTAINER,
                transfer_sha="b" * 64,
                source_sha="b" * 64,
            ),
            release_entry(
                "script/hotfix.lua",
                artifact_class=ReleaseArtifactClass.REGULAR_FILE,
                object_origin=ReleaseObjectOrigin.HISTORICAL,
                object_version="99",
                file_url="99/script/hotfix.lua",
                source_size=20,
                transfer_size=20,
                source_sha="b" * 64,
                transfer_sha="b" * 64,
                source_md5="2" * 32,
                subpackage_flag=3,
            ),
        )
    )
    rows = file_list_rows_from_manifest(payload)
    assert [row.file_name for row in rows] == [
        "scene/redirect/container.assetbundle",
        "script/hotfix.lua",
    ]
    assert rows[1].file_url == "99/script/hotfix.lua"


def test_file_list_row_cannot_be_directly_constructed_or_replaced() -> None:
    """验证协议 DTO 不能绕过领域转换工厂或 dataclasses.replace。"""
    payload = manifest((release_entry("scene/a.assetbundle"),))
    row = file_list_rows_from_manifest(payload)[0]
    with pytest.raises(TypeError):
        FileListRow("a", 1, 1, "1" * 32, "1/a", 0)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        replace(row, file_size=2)


def test_file_list_rows_wrap_protocol_field_errors_as_compatibility_error() -> None:
    """验证控制字符和不可编码协议字段不会泄漏底层异常类型。"""
    payload = manifest(
        (
            release_entry(
                "scene/a.assetbundle",
                object_origin=ReleaseObjectOrigin.HISTORICAL,
                object_version="99",
                file_url="bad\nurl",
            ),
        )
    )
    with pytest.raises(CompatibilityError):
        file_list_rows_from_manifest(payload)
