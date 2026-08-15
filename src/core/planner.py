"""构建计划器：循环检测、输出冲突校验与确定性分层。

本模块提供 ``BuildPlanner`` 与 ``PlannedBuild``。Planner 只接收应用层已完成的
``TaskPlan`` 元组，基于 ``BuildGraph`` 检测依赖环并报告稳定循环路径，按逻辑
输出路径建立唯一 owner 映射以拒绝隐式 fan-in，再用稳定优先队列执行 Kahn
分层并生成不可变 expected ``TaskIdentity`` 映射。导入本模块不执行构建，也不
访问外部系统。
"""

from __future__ import annotations

import heapq
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from core.errors import PlanningError
from core.graph import BuildGraph
from core.tasks import BuildContext, TaskIdentity, TaskPlan


def _utf8_key(name: str) -> bytes:
    """将任务名编码为 UTF-8 字节，供稳定排序与优先队列使用。

    参数：
        name: 任务逻辑名。

    返回：
        ``name.encode("utf-8")`` 字节串。

    异常：
        无；非法字符串由调用方保证不出现。

    约束与副作用：
        纯函数；无 I/O。
    """
    return name.encode("utf-8")


def _stable_cycle_path(graph: BuildGraph, cycle_nodes: set[str]) -> tuple[str, ...]:
    """在环节点子图中构造按 UTF-8 稳定起点的循环路径。

    参数：
        graph: 构建依赖图。
        cycle_nodes: Kahn 消元后仍残留的环上节点集合。

    返回：
        以字典序最小任务名为起点、沿下游边行走并闭合回起点的路径元组
        （末尾再次包含起点）。

    异常：
        无法闭合时抛出 ``PlanningError``（理论上环子图应可闭合）。

    约束与副作用：
        每一步在环内下游候选中选取 UTF-8 最小者，保证与输入排列无关。
    """
    start = min(cycle_nodes, key=_utf8_key)
    path: list[str] = [start]
    current = start
    # 环长有上界；防止异常图结构导致死循环。
    for _ in range(len(cycle_nodes) + 1):
        candidates = sorted(
            (dep for dep in graph.dependents_of(current) if dep in cycle_nodes),
            key=_utf8_key,
        )
        if not candidates:
            break
        nxt = candidates[0]
        if nxt == start:
            path.append(start)
            return tuple(path)
        path.append(nxt)
        current = nxt
    raise PlanningError(
        f"检测到依赖循环，但无法构造稳定路径：{', '.join(sorted(cycle_nodes, key=_utf8_key))}"
    )


def _assert_unique_output_owners(plans: tuple[TaskPlan, ...]) -> None:
    """按逻辑输出路径建立唯一 owner 映射，拒绝重复声明。

    参数：
        plans: 已通过构图与环检测的 ``TaskPlan`` 元组。

    返回：
        无；校验通过时静默返回。

    异常：
        两个及以上任务声明同一逻辑输出时抛出 ``PlanningError``，消息包含
        冲突路径与两个 owner 名（按 UTF-8 排序以保证稳定）。

    约束与副作用：
        隐式 fan-in 不允许：共享输出必须由显式聚合任务独占新路径。
        纯函数；无 I/O。
    """
    owners: dict[str, str] = {}
    for plan in plans:
        task_name = plan.spec.name
        for output_path in plan.spec.outputs:
            existing = owners.get(output_path)
            if existing is not None:
                # 稳定列出两个 owner，避免随输入顺序变化消息。
                first, second = sorted((existing, task_name), key=_utf8_key)
                raise PlanningError(
                    f"逻辑输出路径被多个任务声明：{output_path}；冲突 owner：{first}、{second}"
                )
            owners[output_path] = task_name


def _kahn_layers(
    graph: BuildGraph,
    plan_names: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    """使用稳定优先队列执行 Kahn 分层。

    参数：
        graph: 无环 ``BuildGraph``。
        plan_names: 图内全部任务名。

    返回：
        执行层元组；每层为按任务名 UTF-8 字节序排列的任务名元组。独立任务
        （无相互依赖）落在同一层；依赖只出现在更早层。

    异常：
        仍存在未归零入度节点时抛出 ``PlanningError`` 并附带稳定循环路径。

    约束与副作用：
        层内用 ``heapq`` 按 UTF-8 字节弹出，保证与输入排列无关；纯函数。
    """
    indegree: dict[str, int] = {name: len(graph.dependencies_of(name)) for name in plan_names}
    name_by_key: dict[bytes, str] = {_utf8_key(name): name for name in plan_names}
    remaining: set[str] = set(plan_names)
    layers: list[tuple[str, ...]] = []

    while remaining:
        # 当前层：所有入度已归零的就绪节点，经优先队列稳定排序。
        ready_heap: list[bytes] = []
        for name in remaining:
            if indegree[name] == 0:
                heapq.heappush(ready_heap, _utf8_key(name))
        if not ready_heap:
            path = _stable_cycle_path(graph, remaining)
            raise PlanningError(f"检测到依赖循环：{' -> '.join(path)}")

        layer: list[str] = []
        while ready_heap:
            key = heapq.heappop(ready_heap)
            name = name_by_key[key]
            layer.append(name)
            remaining.remove(name)
            for dependent in graph.dependents_of(name):
                indegree[dependent] -= 1
        layers.append(tuple(layer))

    return tuple(layers)


def _expected_identities_for_layers(
    plans: tuple[TaskPlan, ...],
    context: BuildContext,
    layers: tuple[tuple[str, ...], ...],
) -> Mapping[str, TaskIdentity]:
    """按层为每个 TaskPlan 生成不可变 expected identity 映射。

    参数：
        plans: 完整 ``TaskPlan`` 元组。
        context: 共享 ``BuildContext``。
        layers: Kahn 分层结果。

    返回：
        任务名到 ``TaskIdentity`` 的只读映射；对每个 plan 调用
        ``TaskIdentity.from_plan(plan, context, upstream_identities)``，其中
        ``upstream_identities`` 为 ``plan.spec.dependencies`` 声明顺序对应的
        已计算身份。

    异常：
        依赖身份缺失时抛出 ``KeyError``（分层正确时不应发生）。

    约束与副作用：
        不执行任务；返回 ``MappingProxyType`` 防止调用方就地修改。
    """
    plan_by_name = {plan.spec.name: plan for plan in plans}
    identities: dict[str, TaskIdentity] = {}
    for layer in layers:
        for name in layer:
            plan = plan_by_name[name]
            # 上游身份必须按 spec.dependencies 顺序，而非层内 UTF-8 顺序。
            upstream = tuple(identities[dep_name] for dep_name in plan.spec.dependencies)
            identities[name] = TaskIdentity.from_plan(plan, context, upstream)
    return MappingProxyType(identities)


@dataclass(frozen=True, slots=True)
class PlannedBuild:
    """规划成功后的不可变构建计划结果。

    职责：
        承载通过循环检测与输出 owner 唯一性校验的 ``BuildGraph``、确定性执行
        层以及完整 expected ``TaskIdentity`` 映射，供 Frontier / Executor 消费。

    参数：
        graph: 已校验的 ``BuildGraph``。
        layers: Kahn 分层；层内按任务名 UTF-8 字节序排列。
        expected_identities: 任务名到 expected ``TaskIdentity`` 的只读映射。

    返回：
        无；本类为不可变数据载体。

    异常：
        无；非法输入由 ``BuildPlanner.plan`` 在构造前拒绝。

    约束与副作用：
        ``frozen=True, slots=True``；不执行任务，无外部副作用。
    """

    graph: BuildGraph
    layers: tuple[tuple[str, ...], ...]
    expected_identities: Mapping[str, TaskIdentity]


class BuildPlanner:
    """从完整 TaskPlan 集合生成可执行规划结果的计划器。

    职责：
        接收应用层已完成的 ``TaskPlan`` 元组与 ``BuildContext``，构图、检测
        依赖环、校验输出 owner 唯一性，并生成确定性执行层与 expected identity
        映射。不接受 ``TaskSpec`` 集合，也不再调用 ``BuildTask.plan``。

    参数：
        无构造参数；通过 ``plan`` 方法接收计划与上下文。

    返回：
        ``plan`` 返回 ``PlannedBuild``。

    异常：
        依赖环、输出冲突或构图局部错误时抛出 ``PlanningError``。

    约束与副作用：
        纯领域逻辑；无 I/O；不执行任务。
    """

    def plan(
        self,
        plans: tuple[TaskPlan, ...],
        context: BuildContext,
    ) -> PlannedBuild:
        """根据完整 TaskPlan 集合生成分层规划结果。

        参数：
            plans: 已完成的 ``TaskPlan`` 元组；不得传入 ``TaskSpec``。
            context: 共享构建上下文，用于 ``TaskIdentity.from_plan``。

        返回：
            含 ``graph``、``layers`` 与 ``expected_identities`` 的 ``PlannedBuild``。

        异常：
            构图失败、存在依赖环或同一逻辑输出被多个任务声明时抛出
            ``PlanningError``；环路径与冲突 owner 均按稳定任务键报告。

        约束与副作用：
            不调用 ``task.plan``；不执行任务；identity 映射不可变。
        """
        graph = BuildGraph.from_plans(plans)
        plan_names = tuple(plan.spec.name for plan in plans)
        # Kahn 分层同时完成环检测；有环时优先报告稳定路径。
        layers = _kahn_layers(graph, plan_names)
        _assert_unique_output_owners(plans)
        expected = _expected_identities_for_layers(plans, context, layers)
        return PlannedBuild(
            graph=graph,
            layers=layers,
            expected_identities=expected,
        )
