"""验证 SDK 结构化操作的原子应用和幂等行为。"""

from __future__ import annotations

from pathlib import Path

from core.platforms import BuildPlatform
from sdk.apply import SdkHookApplier
from sdk.model import SdkDescriptor, SdkOperation, SdkOperationKind, SdkStage
from sdk.planner import SdkHookPlanner


def test_sdk_applier_writes_only_declared_workspace_targets(tmp_path: Path) -> None:
    """SDK hook 应在 workspace 内写入声明文件并允许重复应用。"""
    descriptor = SdkDescriptor(
        "sdk",
        "1",
        BuildPlatform.ANDROID,
        SdkStage.PRE_BUILD,
        (),
        ("res/sdk.xml",),
        (SdkOperation(SdkOperationKind.WRITE_FILE, "res/sdk.xml", "<sdk/>", "sdk"),),
        (),
    )
    plan = SdkHookPlanner.plan((descriptor,))

    first = SdkHookApplier().apply(plan, tmp_path)
    second = SdkHookApplier().apply(plan, tmp_path)

    assert first.applied
    assert second.applied
    assert (tmp_path / "res/sdk.xml").read_text(encoding="utf-8") == "<sdk/>"
