"""Android SDK hook 的结构化目标映射。"""

from __future__ import annotations

from dataclasses import dataclass

from core.platforms import BuildPlatform
from sdk.model import SdkOperation
from sdk.planner import SdkHookPlan


@dataclass(frozen=True, slots=True)
class AndroidSdkPlan:
    """保存 Android SDK 允许的结构化操作。"""

    plan_id: str
    operations: tuple[SdkOperation, ...]


class AndroidSdkMapper:
    """把通用 SDK hook 限制到 Android 工程目录白名单。"""

    _ROOTS = ("manifest/", "gradle/", "libs/", "jni/", "res/", "assets/")

    def map(self, plan: SdkHookPlan) -> AndroidSdkPlan:
        """校验平台并返回 Android 结构化计划，不生成 Gradle 代码。"""
        if not isinstance(plan, SdkHookPlan):
            raise TypeError("plan 必须是 SdkHookPlan")
        if any(descriptor.platform is not BuildPlatform.ANDROID for descriptor in plan.descriptors):
            raise ValueError("SDK plan 不是 Android 平台")
        self._validate_targets(plan.operations)
        return AndroidSdkPlan(plan.plan_id, plan.operations)

    def _validate_targets(self, operations: tuple[SdkOperation, ...]) -> None:
        """拒绝 Android 工程根外的目标。"""
        for operation in operations:
            if not operation.target.startswith(self._ROOTS):
                raise ValueError(f"Android SDK 目标不在白名单: {operation.target}")


__all__ = ["AndroidSdkMapper", "AndroidSdkPlan"]
