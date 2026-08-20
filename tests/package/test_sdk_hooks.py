"""包体 SDK hook 声明和冲突规划测试。"""

import pytest

from package.sdk_hooks import SdkHookPlanner, SdkHookPlan


class _Hook:
    """声明一个固定的 SDK hook。"""

    name = "analytics"
    order = 10

    def plan(self, context: str) -> SdkHookPlan:
        """返回一个固定的配置变换计划。"""
        return SdkHookPlan(self.name, self.order, (("analytics.enabled", "true"),))


def test_sdk_hook_planner_orders_hooks_and_supports_empty_input() -> None:
    """验证无 SDK 时为空计划，有 SDK 时按 order 稳定排序。"""
    assert SdkHookPlanner.plan((), "release") == ()
    assert SdkHookPlanner.plan((_Hook(),), "release")[0].name == "analytics"


def test_sdk_hook_planner_rejects_conflicting_outputs() -> None:
    """验证两个 hook 写入同一键不同值时失败。"""

    class _Conflict(_Hook):
        """声明与 analytics 冲突的 hook。"""

        name = "conflict"

        def plan(self, context: str) -> SdkHookPlan:
            """返回冲突配置。"""
            return SdkHookPlan(self.name, 20, (("analytics.enabled", "false"),))

    with pytest.raises(ValueError):
        SdkHookPlanner.plan((_Hook(), _Conflict()), "release")
