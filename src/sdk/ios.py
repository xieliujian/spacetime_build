"""iOS SDK hook 的结构化目标映射。"""

from __future__ import annotations

from dataclasses import dataclass

from core.platforms import BuildPlatform
from sdk.model import SdkOperation
from sdk.planner import SdkHookPlan


@dataclass(frozen=True, slots=True)
class IosSdkPlan:
    """保存 iOS SDK 允许的结构化操作。"""

    plan_id: str
    operations: tuple[SdkOperation, ...]


class IosSdkMapper:
    """把通用 SDK hook 限制到 plist、Framework 和资源白名单。"""

    _PREFIXES = ("Resources/", "Frameworks/", "Libraries/", "Entitlements/", "BuildSettings/")
    _FILES = ("Info.plist",)

    def map(self, plan: SdkHookPlan) -> IosSdkPlan:
        """校验平台并返回 iOS 结构化计划，不操作 keychain/pbxproj。"""
        if not isinstance(plan, SdkHookPlan):
            raise TypeError("plan 必须是 SdkHookPlan")
        if any(descriptor.platform is not BuildPlatform.IOS for descriptor in plan.descriptors):
            raise ValueError("SDK plan 不是 iOS 平台")
        for operation in plan.operations:
            if (
                not operation.target.startswith(self._PREFIXES)
                and operation.target not in self._FILES
            ):
                raise ValueError(f"iOS SDK 目标不在白名单: {operation.target}")
        return IosSdkPlan(plan.plan_id, plan.operations)


__all__ = ["IosSdkMapper", "IosSdkPlan"]
