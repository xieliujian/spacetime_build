"""验证 BuildPlanner 循环检测、输出冲突与确定性分层。

本模块按第二阶段 Task 9 分步覆盖 ``BuildPlanner.plan``：只接收完整
``TaskPlan``、稳定报告依赖环、拒绝重复输出 owner，以及生成确定性执行层与
expected identity 映射。测试不访问 SVN、Unity、Jenkins 或 CDN，也不执行真实
构建副作用。
"""

from __future__ import annotations

import pytest

from core.errors import PlanningError
from core.planner import BuildPlanner
from core.tasks import BuildContext, TaskPlan, TaskSpec


def _context() -> BuildContext:
    """构造测试用不可变 ``BuildContext``。

    返回：
        含固定请求、revision、工具链、基线与 schema 的上下文。
    """
    return BuildContext(
        request_digest="req-digest-1",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id="baseline-1",
        schema_version=1,
    )


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


def test_planner_rejects_cycle_with_stable_cycle_path() -> None:
    """验证 BuildPlanner 拒绝依赖环并以稳定任务键报告路径。

    测试无参数和返回值。断言：

    - ``plan`` 只接收已完成 ``TaskPlan`` 元组与 ``BuildContext``；
    - 存在循环时抛出 ``PlanningError``，消息包含稳定循环路径；
    - 改变输入 ``TaskPlan`` 排列不改变错误消息。

    当 ``core.planner`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入与内存构造外不产生外部副作用。
    """
    # a → b → c → a 构成环；另加无关叶子，避免只测最简三角。
    plan_a = _plan("cycle.a", ("cycle.c",))
    plan_b = _plan("cycle.b", ("cycle.a",))
    plan_c = _plan("cycle.c", ("cycle.b",))
    leaf = _plan("leaf.ok")

    orderings = (
        (plan_a, plan_b, plan_c, leaf),
        (leaf, plan_c, plan_a, plan_b),
        (plan_b, leaf, plan_c, plan_a),
    )
    messages: list[str] = []
    planner = BuildPlanner()
    context = _context()

    for plans in orderings:
        with pytest.raises(PlanningError) as exc_info:
            planner.plan(plans, context)
        messages.append(str(exc_info.value))

    assert len(set(messages)) == 1
    message = messages[0]
    # 稳定路径应按 UTF-8 任务键选择/格式化，消息中须出现环上全部任务名。
    assert "cycle.a" in message
    assert "cycle.b" in message
    assert "cycle.c" in message


def test_planner_rejects_duplicate_output_owners() -> None:
    """验证 BuildPlanner 拒绝同一逻辑输出的多个 owner。

    测试无参数和返回值。断言：两个无依赖任务声明同一逻辑输出路径时，
    ``plan`` 抛出 ``PlanningError``，消息列出两个 owner 任务名。隐式 fan-in
    不允许；新输出必须由显式聚合任务拥有。

    当 Step 1 最小 GREEN 尚未建立输出 owner 索引时，重复输出输入不会抛出
    ``PlanningError``，使 ``pytest.raises`` 失败。除导入与内存构造外不产生
    外部副作用。
    """
    shared_output = "shared/conflict.assetbundle"
    left = _plan(
        "owner.left",
        outputs=frozenset({shared_output, "owner.left/extra"}),
    )
    right = _plan(
        "owner.right",
        outputs=frozenset({shared_output}),
    )

    planner = BuildPlanner()
    with pytest.raises(PlanningError) as exc_info:
        planner.plan((left, right), _context())

    message = str(exc_info.value)
    assert "owner.left" in message
    assert "owner.right" in message
    assert shared_output in message


def test_planner_builds_deterministic_layers_and_expected_identity_map() -> None:
    """验证 BuildPlanner 生成确定性执行层与完整 expected identity 映射。

    测试无参数和返回值。断言：

    - 无依赖的独立 ``TaskPlan`` 落在同一层，层内按任务名 UTF-8 字节序排列；
    - 不同输入排列得到相同 ``layers``；
    - 有依赖的任务只出现在更早层之后；
    - ``expected_identities`` 对每个 plan 调用 ``TaskIdentity.from_plan``，
      上游身份按 ``spec.dependencies`` 顺序组装，映射完整且不可变；
    - planner 不接受 ``TaskSpec`` 集合，也不再调用 ``task.plan``。

    当前两步最小 GREEN 未定义 ``PlannedBuild.layers`` / ``expected_identities``
    时，读取属性应以 ``AttributeError`` 失败。除导入与内存构造外不产生外部
    副作用。
    """
    import dataclasses

    from core.tasks import TaskIdentity

    shared = _plan("shared.base")
    config = _plan("config.build")
    scene = _plan("scene.build", ("shared.base", "config.build"))
    context = _context()
    planner = BuildPlanner()

    orderings = (
        (shared, config, scene),
        (scene, shared, config),
        (config, scene, shared),
    )
    results = [planner.plan(plans, context) for plans in orderings]

    # 不同输入排列必须得到相同层与 identity。
    assert results[0].layers == results[1].layers == results[2].layers
    assert (
        results[0].expected_identities
        == results[1].expected_identities
        == results[2].expected_identities
    )

    layers = results[0].layers
    assert layers == (
        ("config.build", "shared.base"),
        ("scene.build",),
    )
    # 依赖只出现在更早层：scene 的上游均在第 0 层。
    layer_index = {name: idx for idx, layer in enumerate(layers) for name in layer}
    assert layer_index["shared.base"] < layer_index["scene.build"]
    assert layer_index["config.build"] < layer_index["scene.build"]

    expected = results[0].expected_identities
    assert set(expected) == {"shared.base", "config.build", "scene.build"}

    id_shared = TaskIdentity.from_plan(shared, context, ())
    id_config = TaskIdentity.from_plan(config, context, ())
    # upstream_identities 必须按 spec.dependencies 声明顺序，而非层内排序。
    id_scene = TaskIdentity.from_plan(
        scene,
        context,
        (id_shared, id_config),
    )
    assert expected["shared.base"] == id_shared
    assert expected["config.build"] == id_config
    assert expected["scene.build"] == id_scene

    with pytest.raises((TypeError, AttributeError, dataclasses.FrozenInstanceError)):
        expected["scene.build"] = id_shared  # type: ignore[index]
