"""包体 SDK 扩展 hook 的声明和冲突检查。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SdkHookPlan:
    """一个 SDK hook 声明的结构化配置变换。"""

    name: str
    order: int
    outputs: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """校验 hook 名称、顺序和输出键唯一性。"""
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("hook name 必须是非空字符串")
        if not isinstance(self.order, int) or isinstance(self.order, bool):
            raise TypeError("hook order 必须是 int")
        if not isinstance(self.outputs, tuple):
            raise TypeError("outputs 必须是 tuple")
        keys: set[str] = set()
        for key, value in self.outputs:
            if not isinstance(key, str) or not key or not isinstance(value, str):
                raise ValueError("SDK hook output 必须是字符串键值")
            if key in keys:
                raise ValueError("同一 SDK hook 不得重复输出键")
            keys.add(key)


class PackageSdkHook(Protocol):
    """SDK 扩展实现必须提供的纯计划协议。"""

    name: str
    order: int

    def plan(self, context: str) -> SdkHookPlan:
        """根据公开包体上下文生成配置变换声明。"""
        ...


class SdkHookPlanner:
    """按声明顺序聚合 SDK hook 并拒绝跨 hook 冲突。"""

    @staticmethod
    def plan(hooks: Iterable[PackageSdkHook], context: str) -> tuple[SdkHookPlan, ...]:
        """执行各 hook 的纯 plan 并检查同一输出键的值冲突。

        参数：
            hooks: SDK hook 实例集合。
            context: 不含秘密的公开构建上下文标签。

        返回：
            按 ``order``、名称 UTF-8 字节序排列的不可变计划元组。

        异常：
            hook 类型、名称重复、返回类型或输出值冲突时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            不执行 SDK 工具、不写工程；只聚合结构化声明。
        """
        if not isinstance(context, str) or not context:
            raise ValueError("context 必须是非空字符串")
        plans = tuple(hook.plan(context) for hook in hooks)
        if not all(isinstance(item, SdkHookPlan) for item in plans):
            raise TypeError("hook.plan 必须返回 SdkHookPlan")
        ordered = tuple(sorted(plans, key=lambda item: (item.order, item.name.encode("utf-8"))))
        if len({item.name for item in ordered}) != len(ordered):
            raise ValueError("SDK hook 名称不得重复")
        outputs: dict[str, str] = {}
        for item in ordered:
            for key, value in item.outputs:
                if key in outputs and outputs[key] != value:
                    raise ValueError(f"SDK hook 输出冲突: {key}")
                outputs[key] = value
        return ordered
