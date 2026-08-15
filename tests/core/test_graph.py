"""验证 BuildGraph 不可变查询与局部结构校验。

本模块按第二阶段 Task 8 分步覆盖 ``BuildGraph.from_plans`` 查询 API 以及
重复名、缺失依赖、自依赖的 ``PlanningError`` 拒绝。测试不访问 SVN、Unity、
Jenkins 或 CDN，也不执行真实构建副作用。
"""

from __future__ import annotations

import dataclasses

import pytest

from core.errors import PlanningError
from core.graph import BuildGraph
from core.tasks import TaskPlan, TaskSpec


def _plan(
    name: str,
    dependencies: tuple[str, ...] = (),
    *,
    outputs: frozenset[str] | None = None,
) -> TaskPlan:
    """构造测试用完整 ``TaskPlan``。

    参数：
        name: 任务逻辑名。
        dependencies: 有序上游依赖名元组。
        outputs: 可选输出路径集合；默认使用 ``{name}/out``。

    返回：
        含固定摘要字段的不可变 ``TaskPlan``。
    """
    return TaskPlan(
        spec=TaskSpec(
            name=name,
            dependencies=dependencies,
            outputs=outputs if outputs is not None else frozenset({f"{name}/out"}),
            implementation_version="1.0.0",
            execution_attributes=(("cacheable", "true"),),
        ),
        resolved_input_digest=f"input-{name}",
        config_digest=f"config-{name}",
    )


def test_build_graph_exposes_dependencies_dependents_and_roots() -> None:
    """验证 BuildGraph 暴露依赖、被依赖与根节点查询且保持不可变。

    测试无参数和返回值。断言：

    - ``from_plans`` 构图后 ``plan_of`` 返回完整 ``TaskPlan``；
    - ``dependencies_of`` / ``dependents_of`` / ``roots`` 查询结果正确；
    - 查询视图不可变；输入列表后续修改不影响图内计划。

    当 ``core.graph`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入与内存构造外不产生外部副作用。
    """
    shared = _plan("shared.base")
    config = _plan("config.build")
    scene = _plan("scene.build", ("shared.base", "config.build"))

    # 使用可变 list，稍后清空以验证图已复制输入。
    plans: list[TaskPlan] = [shared, config, scene]
    graph = BuildGraph.from_plans(plans)
    plans.clear()

    assert graph.plan_of("shared.base") is shared or graph.plan_of("shared.base") == shared
    assert graph.plan_of("config.build").spec.name == "config.build"
    assert graph.plan_of("scene.build").spec.dependencies == (
        "shared.base",
        "config.build",
    )
    assert graph.plan_of("scene.build").resolved_input_digest == "input-scene.build"
    assert graph.plan_of("scene.build").config_digest == "config-scene.build"

    deps = graph.dependencies_of("scene.build")
    assert set(deps) == {"shared.base", "config.build"}
    assert isinstance(deps, (tuple, frozenset))
    with pytest.raises((TypeError, AttributeError)):
        deps.add("x")  # type: ignore[union-attr]

    dependents_shared = graph.dependents_of("shared.base")
    assert set(dependents_shared) == {"scene.build"}
    assert isinstance(dependents_shared, (tuple, frozenset))
    with pytest.raises((TypeError, AttributeError)):
        dependents_shared.append("x")  # type: ignore[union-attr]

    assert set(graph.dependents_of("config.build")) == {"scene.build"}
    assert set(graph.dependents_of("scene.build")) == set()
    assert set(graph.dependencies_of("shared.base")) == set()

    roots = graph.roots
    assert set(roots) == {"shared.base", "config.build"}
    assert isinstance(roots, (tuple, frozenset))
    with pytest.raises((TypeError, AttributeError, dataclasses.FrozenInstanceError)):
        if isinstance(roots, tuple):
            # 元组不可原地追加；属性替换应失败。
            graph.roots = ()  # type: ignore[misc]
        else:
            roots.add("x")  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("plans", "expected_name"),
    [
        (
            (_plan("dup.task"), _plan("dup.task", ("other",))),
            "dup.task",
        ),
        (
            (_plan("leaf", ("missing.upstream",)),),
            "missing.upstream",
        ),
        (
            (_plan("self.loop", ("self.loop",)),),
            "self.loop",
        ),
    ],
    ids=["duplicate_names", "missing_dependency", "self_edge"],
)
def test_build_graph_rejects_duplicate_names_missing_dependencies_and_self_edges(
    plans: tuple[TaskPlan, ...],
    expected_name: str,
) -> None:
    """验证构图拒绝重复名、缺失依赖与自依赖。

    参数：
        plans: 非法 ``TaskPlan`` 元组。
        expected_name: 错误消息中应出现的任务名。

    返回：
        无。断言 ``from_plans`` 抛出 ``PlanningError`` 且消息包含任务名。

    当 Step 1 最小 GREEN 尚未做局部校验时，每个用例因未抛 ``PlanningError``
    失败。循环与输出冲突由 planner 独立测试覆盖。除导入与内存构造外不产生
    外部副作用。
    """
    with pytest.raises(PlanningError) as exc_info:
        BuildGraph.from_plans(plans)
    assert expected_name in str(exc_info.value)
