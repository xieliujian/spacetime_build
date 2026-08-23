"""Windows SDK hook 的结构化目标映射。"""

from __future__ import annotations

from dataclasses import dataclass

from core.platforms import BuildPlatform
from sdk.model import SdkOperation
from sdk.planner import SdkHookPlan


@dataclass(frozen=True, slots=True)
class WindowsSdkPlan:
    """保存 Windows SDK 允许的结构化操作。"""

    plan_id: str
    operations: tuple[SdkOperation, ...]


class WindowsSdkMapper:
    """把通用 SDK hook 限制到 Windows layout/config/runtime 目录。"""

    _PREFIXES = ("config/", "runtime/", "plugins/", "resources/", "Launcher/")

    def map(self, plan: SdkHookPlan) -> WindowsSdkPlan:
        """校验平台并返回 Windows 结构化计划，不直接签名或上传。"""
        if not isinstance(plan, SdkHookPlan):
            raise TypeError("plan 必须是 SdkHookPlan")
        if any(descriptor.platform is not BuildPlatform.WINDOWS for descriptor in plan.descriptors):
            raise ValueError("SDK plan 不是 Windows 平台")
        for operation in plan.operations:
            if not operation.target.startswith(self._PREFIXES):
                raise ValueError(f"Windows SDK 目标不在白名单: {operation.target}")
        return WindowsSdkPlan(plan.plan_id, plan.operations)


__all__ = ["WindowsSdkMapper", "WindowsSdkPlan"]
