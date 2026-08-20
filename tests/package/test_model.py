"""客户端包体公共模型测试。"""

import pytest

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from package.model import PackageArtifact, PackageExecutionRecord, PackageRequest, PackageStatus


def test_package_request_validates_fixed_identity_and_version() -> None:
    """验证包体请求使用固定 revision、ReleaseBundle 身份和合法版本。"""
    request = PackageRequest(
        BuildPlatform.ANDROID,
        "svn:123",
        "a" * 64,
        "2022.3.62f2",
        "com.example.game",
        "1.2.3",
        12,
        "release",
    )
    assert request.platform is BuildPlatform.ANDROID
    assert request.version_code == 12
    assert request.request_id


def test_package_request_rejects_head_and_bad_application_id() -> None:
    """验证浮动 HEAD 和非法 application ID 不能进入包体阶段。"""
    values = [
        ("HEAD", "com.example.game"),
        ("svn:1", "not an id"),
    ]
    for revision, application_id in values:
        with pytest.raises(ValueError):
            PackageRequest(
                BuildPlatform.ANDROID,
                revision,
                "a" * 64,
                "2022.3.62f2",
                application_id,
                "1.0.0",
                1,
                "release",
            )


def test_package_execution_record_is_immutable_and_artifact_is_typed() -> None:
    """验证运行记录和包体产物都不携带秘密或可变状态引用。"""
    artifact = PackageArtifact(
        "game.apk",
        BlobRef("blobs/" + "b" * 64, "b" * 64, 10),
        "apk",
    )
    record = PackageExecutionRecord("run-1", "a" * 64, PackageStatus.PLANNED, None)
    assert artifact.kind == "apk"
    assert record.status is PackageStatus.PLANNED
    with pytest.raises((AttributeError, TypeError)):
        record.status = PackageStatus.FAILED  # type: ignore[misc]
