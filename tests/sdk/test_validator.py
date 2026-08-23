"""验证 SDK hook 后的声明输出检查。"""

from __future__ import annotations

from pathlib import Path

from core.platforms import BuildPlatform
from sdk.model import SdkDescriptor, SdkOperation, SdkOperationKind, SdkStage
from sdk.planner import SdkHookPlanner
from sdk.validator import SdkHookValidator


def test_sdk_validator_reports_missing_declared_output(tmp_path: Path) -> None:
    """声明文件缺失时应返回失败报告而不是静默通过。"""
    descriptor = SdkDescriptor(
        "sdk",
        "1",
        BuildPlatform.WINDOWS,
        SdkStage.POST_BUILD,
        (),
        ("config/sdk.json",),
        (SdkOperation(SdkOperationKind.WRITE_FILE, "config/sdk.json", "{}", "sdk"),),
        (),
    )
    report = SdkHookValidator().validate(SdkHookPlanner.plan((descriptor,)), tmp_path)

    assert not report.valid
    assert report.errors == ("缺少 SDK 输出: config/sdk.json",)
