"""SDK descriptor 不可变模型和危险操作拒绝测试。"""

import pytest
from typing import cast

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from sdk.model import SdkDescriptor, SdkOperation, SdkOperationKind, SdkStage


def _blob(char: str = "a") -> BlobRef:
    """构造固定 SDK payload Blob。"""
    digest = char * 64
    return BlobRef(f"blobs/{digest}", digest, 4)


def test_sdk_descriptor_normalizes_operations_and_keeps_secret_as_reference() -> None:
    """Given 结构化 SDK 操作，When 构造 descriptor，Then 输出和冲突键稳定排序。"""
    descriptor = SdkDescriptor(
        sdk_id="com.example.analytics",
        version="1.2.0",
        platform=BuildPlatform.ANDROID,
        stage=SdkStage.PRE_BUILD,
        inputs=(_blob(),),
        outputs=("res/sdk.xml", "libs/analytics.aar"),
        operations=(
            SdkOperation(SdkOperationKind.WRITE_FILE, "res/sdk.xml", "<sdk/>", "res/sdk.xml"),
            SdkOperation(SdkOperationKind.SET_PROPERTY, "manifest/appId", "com.example", "appId"),
        ),
        secret_refs=("secret://analytics/key",),
        validation_rules=("manifest-present",),
    )

    assert descriptor.sdk_id == "com.example.analytics"
    assert descriptor.operations[0].target == "manifest/appId"
    assert "secret://analytics/key" not in repr(descriptor)


def test_sdk_model_rejects_command_like_operation_and_duplicate_output() -> None:
    """验证 SDK 数据不能携带 shell/module/script，并拒绝重复输出所有权。"""
    with pytest.raises(ValueError, match="command"):
        SdkOperation(cast(SdkOperationKind, "command"), "tool", "run", "tool")
    with pytest.raises(ValueError, match="重复"):
        SdkDescriptor(
            "sdk",
            "1.0.0",
            BuildPlatform.IOS,
            SdkStage.POST_BUILD,
            (),
            ("Frameworks/X.framework", "Frameworks/X.framework"),
            (),
            (),
            (),
        )
