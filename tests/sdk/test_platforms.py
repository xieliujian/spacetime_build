"""验证 Android/iOS/Windows SDK 映射只允许平台白名单目标。"""

from __future__ import annotations

import pytest

from core.platforms import BuildPlatform
from sdk.android import AndroidSdkMapper
from sdk.ios import IosSdkMapper
from sdk.model import SdkDescriptor, SdkOperation, SdkOperationKind, SdkStage
from sdk.planner import SdkHookPlanner
from sdk.windows import WindowsSdkMapper


def _descriptor(platform: BuildPlatform, target: str) -> SdkDescriptor:
    """创建单操作 SDK descriptor。"""
    return SdkDescriptor(
        "sdk",
        "1",
        platform,
        SdkStage.PRE_BUILD,
        (),
        (target,),
        (SdkOperation(SdkOperationKind.WRITE_FILE, target, "value", "target"),),
        (),
    )


@pytest.mark.parametrize(
    ("mapper", "platform", "target"),
    [
        (AndroidSdkMapper(), BuildPlatform.ANDROID, "res/sdk.xml"),
        (IosSdkMapper(), BuildPlatform.IOS, "Resources/sdk.plist"),
        (WindowsSdkMapper(), BuildPlatform.WINDOWS, "config/sdk.json"),
    ],
)
def test_platform_sdk_mapper_returns_structured_plan(
    mapper: object, platform: BuildPlatform, target: str
) -> None:
    """合法目标应映射为不含脚本的结构化计划。"""
    plan = mapper.map(SdkHookPlanner.plan((_descriptor(platform, target),)))  # type: ignore[attr-defined]
    assert plan.operations[0].target == target


def test_platform_sdk_mapper_rejects_cross_platform_target() -> None:
    """平台 mapper 必须 fail closed。"""
    with pytest.raises(ValueError):
        AndroidSdkMapper().map(
            SdkHookPlanner.plan((_descriptor(BuildPlatform.IOS, "Resources/a"),))
        )
