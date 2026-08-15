"""不可变构建任务依赖图。

本模块提供 ``BuildGraph``：从完整 ``TaskPlan`` 集合构图，暴露依赖、被依赖与
根节点查询。图在构造时复制计划与边集合，之后不可变。局部结构校验（重复名、
缺失依赖、自依赖）在构图阶段拒绝非法输入；循环与输出冲突由 planner 负责。
导入本模块不执行构建，也不访问外部系统。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from core.errors import PlanningError
from core.tasks import TaskPlan


@dataclass(frozen=True, slots=True)
class BuildGraph:
    """通过局部结构校验的不可变任务依赖图。

    职责：
        以任务名为键保存完整 ``TaskPlan``，并维护依赖边与反向被依赖边，供
        Planner / Frontier / Executor 查询拓扑邻居与根节点。

    参数：
        通过 ``from_plans`` 工厂从 ``TaskPlan`` 可迭代对象构造；不提供可变字段
        的公开赋值入口。

    返回：
        无；通过查询方法与 ``roots`` 属性读取。

    异常：
        ``from_plans`` 在发现重复名、缺失依赖或自依赖时抛出 ``PlanningError``；
        查询未知任务名时抛出 ``KeyError``。

    约束与副作用：
        ``frozen=True, slots=True``；构造时复制输入计划映射与边集合，调用方
        之后修改输入容器不影响图。无 I/O，无外部副作用。
    """

    _plans: Mapping[str, TaskPlan]
    _dependencies: Mapping[str, frozenset[str]]
    _dependents: Mapping[str, frozenset[str]]
    _roots: frozenset[str]

    @classmethod
    def from_plans(cls, plans: Iterable[TaskPlan]) -> BuildGraph:
        """由完整 ``TaskPlan`` 序列构造不可变依赖图。

        参数：
            plans: ``TaskPlan`` 可迭代对象；允许为空。

        返回：
            通过局部结构校验后的查询可用 ``BuildGraph``。

        异常：
            重复任务名、自依赖或依赖指向图外未知任务时抛出 ``PlanningError``，
            消息包含相关任务名。循环与输出冲突不在本方法检测。

        约束与副作用：
            纯函数；复制计划与边，不修改入参，不访问文件系统。
        """
        plan_map: dict[str, TaskPlan] = {}
        dependencies: dict[str, frozenset[str]] = {}
        dependents: dict[str, set[str]] = {}

        for plan in plans:
            name = plan.spec.name
            # 图内任务名必须唯一，重复名无法建立稳定 plan_of 索引。
            if name in plan_map:
                raise PlanningError(f"重复的任务名：{name}")
            dep_names = plan.spec.dependencies
            # 自依赖是最短环，局部即可拒绝，无需等待全局环检测。
            if name in dep_names:
                raise PlanningError(f"任务存在自依赖：{name}")
            plan_map[name] = plan
            dep_set = frozenset(dep_names)
            dependencies[name] = dep_set
            dependents.setdefault(name, set())
            for dep in dep_set:
                dependents.setdefault(dep, set()).add(name)

        # 依赖必须指向图内已声明任务；缺失边会在执行期表现为未知上游。
        for name, dep_set in dependencies.items():
            for dep in dep_set:
                if dep not in plan_map:
                    raise PlanningError(f"任务 {name} 依赖缺失的上游任务：{dep}")

        frozen_dependents = {name: frozenset(deps) for name, deps in dependents.items()}
        # 根节点：自身无上游依赖。
        roots = frozenset(name for name, deps in dependencies.items() if not deps)
        return cls(
            _plans=dict(plan_map),
            _dependencies=dict(dependencies),
            _dependents=frozen_dependents,
            _roots=roots,
        )

    def plan_of(self, name: str) -> TaskPlan:
        """按任务名返回图内完整 ``TaskPlan``。

        参数：
            name: 任务逻辑名。

        返回：
            构图时保存的完整 ``TaskPlan``。

        异常：
            任务不存在时抛出 ``KeyError``。

        约束与副作用：
            只读；无副作用。
        """
        return self._plans[name]

    def dependencies_of(self, name: str) -> frozenset[str]:
        """返回指定任务的上游依赖名不可变集合。

        参数：
            name: 任务逻辑名。

        返回：
            上游依赖名 ``frozenset``；无依赖时为空集合。

        异常：
            任务不存在时抛出 ``KeyError``。

        约束与副作用：
            只读；返回不可变视图。
        """
        return self._dependencies[name]

    def dependents_of(self, name: str) -> frozenset[str]:
        """返回直接依赖指定任务的下游任务名不可变集合。

        参数：
            name: 任务逻辑名。

        返回：
            下游任务名 ``frozenset``；无下游时为空集合。

        异常：
            任务不在依赖索引中时抛出 ``KeyError``。

        约束与副作用：
            只读；返回不可变视图。
        """
        return self._dependents[name]

    @property
    def roots(self) -> frozenset[str]:
        """返回无上游依赖的根任务名不可变集合。

        返回：
            根任务名 ``frozenset``。

        异常：
            无。

        约束与副作用：
            只读属性；无副作用。
        """
        return self._roots
