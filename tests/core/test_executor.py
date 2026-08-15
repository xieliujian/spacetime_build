"""验证确定性同步 TaskExecutor 的分层执行、恢复跳过与输出契约。

本模块按第二阶段 Task 11 分步覆盖 ``TaskExecutor.execute``：按规划层稳定
顺序执行、仅消费 ``VerifiedFrontier``、严格校验 ``TaskResult`` 输出路径集合，
以及失败即停止新调度。测试不访问 SVN、Unity、Jenkins 或 CDN。
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from types import MappingProxyType
from typing import Callable, get_type_hints

import pytest

from core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    BlobRef,
    LogicalArtifact,
)
from core.errors import ArtifactValidationError, ToolExecutionError
from core.executor import ExecutionResult, TaskExecutor
from core.frontier import (
    CompletedTaskRecord,
    ExecutionFrontier,
    VerifiedFrontier,
)
from core.planner import BuildPlanner
from core.tasks import (
    ArtifactCollection,
    BuildContext,
    TaskPlan,
    TaskResult,
    TaskSpec,
)

_VALID_SHA256_A = "a" * 64
_VALID_SHA256_B = "b" * 64
_VALID_SHA256_C = "c" * 64
_VALID_SHA256_D = "d" * 64


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


def _artifact(
    logical_path: str,
    *,
    sha256: str = _VALID_SHA256_A,
    size: int = 128,
    source_task: str = "task",
) -> LogicalArtifact:
    """构造测试用完整 ``LogicalArtifact``。

    参数：
        logical_path: 客户端逻辑路径。
        sha256: Blob SHA256。
        size: Blob 字节大小。
        source_task: metadata 中的来源任务名。

    返回：
        含 kind、Blob、依赖、分包与 metadata 的不可变逻辑产物。
    """
    return LogicalArtifact(
        logical_path=logical_path,
        kind=ArtifactKind.ASSET_BUNDLE,
        blob=BlobRef(
            locator=f"sha256:{sha256}",
            sha256=sha256,
            size=size,
        ),
        dependencies=(),
        subpackage_ids=frozenset({1}),
        metadata=ArtifactMetadata(
            source_task=source_task,
            source_revision="r100",
            toolchain_digest="toolchain-v1",
            attributes=(),
        ),
    )


class _RecordingTask:
    """记录 execute 调用顺序与输入路径的测试任务。

    职责：
        实现最小 ``BuildTask`` 表面，按构造时给定的输出路径返回产物，并把
        每次 ``execute`` 的任务名与输入逻辑路径追加到共享调用日志。
    """

    def __init__(
        self,
        name: str,
        output_paths: frozenset[str],
        call_log: list[tuple[str, frozenset[str]]],
        *,
        sha256: str = _VALID_SHA256_A,
        execute_hook: Callable[[str, ArtifactCollection], None] | None = None,
    ) -> None:
        """初始化记录型任务。

        参数：
            name: 任务逻辑名。
            output_paths: 本任务声明并产出的逻辑路径集合。
            call_log: 共享调用日志，元素为 ``(task_name, input_paths)``。
            sha256: 产物 Blob 哈希前缀素材。
            execute_hook: 可选钩子，在返回结果前调用，便于注入失败。
        """
        self._name = name
        self._output_paths = output_paths
        self._call_log = call_log
        self._sha256 = sha256
        self._execute_hook = execute_hook

    @property
    def name(self) -> str:
        """返回任务逻辑名。

        返回：
            构造时给定的任务名。
        """
        return self._name

    def plan(self, context: BuildContext) -> TaskPlan:
        """返回与输出路径对齐的完整 ``TaskPlan``。

        参数：
            context: 共享构建上下文（本桩忽略字段细节）。

        返回：
            含本任务依赖占位与输出声明的 ``TaskPlan``。
        """
        del context
        return _plan(self._name, outputs=self._output_paths)

    def execute(
        self,
        context: BuildContext,
        inputs: ArtifactCollection,
    ) -> TaskResult:
        """记录调用并返回声明路径对应的产物。

        参数：
            context: 共享构建上下文。
            inputs: 上游产物集合。

        返回：
            ``outputs`` 为声明路径产物元组的 ``TaskResult``。

        异常：
            若设置了 ``execute_hook`` 且钩子抛出异常，则原样逸出。
        """
        del context
        input_paths = frozenset(inputs)
        self._call_log.append((self._name, input_paths))
        if self._execute_hook is not None:
            self._execute_hook(self._name, inputs)
        outputs = tuple(
            _artifact(
                path,
                sha256=self._sha256,
                source_task=self._name,
            )
            for path in sorted(self._output_paths, key=lambda p: p.encode("utf-8"))
        )
        return TaskResult(outputs=outputs)


def test_executor_runs_layers_in_stable_order_and_collects_artifacts() -> None:
    """验证 TaskExecutor 按规划层稳定顺序执行并收集全部产物。

    测试无参数和返回值。断言：

    - ``execute(planned_build, tasks, context, verified_frontier=None)`` 返回
      ``ExecutionResult``；
    - 调用顺序与 planner 层及层内 UTF-8 稳定顺序一致；
    - 每个节点只收到显式上游依赖任务的产物，而非全局 registry；
    - 结果包含全部 ``LogicalArtifact``。

    当 ``core.executor`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入与内存构造外不产生外部副作用。
    """
    # 层 0：leaf.b、leaf.a（UTF-8 下 a 在 b 前）；层 1：mid；层 2：top
    plan_leaf_a = _plan("leaf.a", outputs=frozenset({"leaf.a/out"}))
    plan_leaf_b = _plan("leaf.b", outputs=frozenset({"leaf.b/out"}))
    plan_mid = _plan(
        "mid",
        ("leaf.a",),
        outputs=frozenset({"mid/out"}),
    )
    plan_top = _plan(
        "top",
        ("mid", "leaf.b"),
        outputs=frozenset({"top/out"}),
    )
    # 故意打乱输入排列，验证执行顺序仍由 layers 决定。
    plans = (plan_top, plan_leaf_b, plan_mid, plan_leaf_a)
    context = _context()
    planned = BuildPlanner().plan(plans, context)
    assert planned.layers == (
        ("leaf.a", "leaf.b"),
        ("mid",),
        ("top",),
    )

    call_log: list[tuple[str, frozenset[str]]] = []
    tasks: Mapping[str, _RecordingTask] = {
        "leaf.a": _RecordingTask(
            "leaf.a", frozenset({"leaf.a/out"}), call_log, sha256=_VALID_SHA256_A
        ),
        "leaf.b": _RecordingTask(
            "leaf.b", frozenset({"leaf.b/out"}), call_log, sha256=_VALID_SHA256_B
        ),
        "mid": _RecordingTask("mid", frozenset({"mid/out"}), call_log, sha256=_VALID_SHA256_C),
        "top": _RecordingTask("top", frozenset({"top/out"}), call_log, sha256=_VALID_SHA256_D),
    }

    result = TaskExecutor().execute(planned, tasks, context, verified_frontier=None)

    assert isinstance(result, ExecutionResult)
    assert [name for name, _ in call_log] == ["leaf.a", "leaf.b", "mid", "top"]
    # leaf 无上游；mid 只见 leaf.a；top 只见 mid 与 leaf.b，不见 leaf.a。
    assert call_log[0] == ("leaf.a", frozenset())
    assert call_log[1] == ("leaf.b", frozenset())
    assert call_log[2] == ("mid", frozenset({"leaf.a/out"}))
    assert call_log[3] == ("top", frozenset({"mid/out", "leaf.b/out"}))

    artifact_paths = {item.logical_path for item in result.artifacts}
    assert artifact_paths == {
        "leaf.a/out",
        "leaf.b/out",
        "mid/out",
        "top/out",
    }
    assert all(isinstance(item, LogicalArtifact) for item in result.artifacts)
    assert isinstance(result.artifacts, tuple)


def test_executor_accepts_only_verified_frontier_and_skips_exact_verified_set() -> None:
    """验证 TaskExecutor 只接受 VerifiedFrontier 并跳过已验证集合。

    测试无参数和返回值。断言：

    - ``verified_frontier`` 类型签名与运行时均拒绝原始 ``ExecutionFrontier`` /
      ``CompletedTaskRecord``；
    - ``VerifiedFrontier.task_names`` 中的节点不调用 ``execute``，其已验证
      输出注入 registry；
    - 未验证节点仍执行，并可消费已验证上游产物。

    当前一步最小 GREEN 对任何非空 ``verified_frontier`` 抛出
    ``NotImplementedError`` 时，本测试在传入 ``VerifiedFrontier`` 时确定失败。
    除导入与内存构造外不产生外部副作用。
    """
    plan_leaf = _plan("leaf", outputs=frozenset({"leaf/out"}))
    plan_mid = _plan("mid", ("leaf",), outputs=frozenset({"mid/out"}))
    plan_top = _plan("top", ("mid",), outputs=frozenset({"top/out"}))
    context = _context()
    planned = BuildPlanner().plan((plan_leaf, plan_mid, plan_top), context)

    verified_leaf_outputs = (_artifact("leaf/out", sha256=_VALID_SHA256_A, source_task="leaf"),)
    frontier = VerifiedFrontier(
        task_names=frozenset({"leaf"}),
        outputs=MappingProxyType({"leaf": verified_leaf_outputs}),
    )

    call_log: list[tuple[str, frozenset[str]]] = []
    tasks: Mapping[str, _RecordingTask] = {
        "leaf": _RecordingTask("leaf", frozenset({"leaf/out"}), call_log, sha256=_VALID_SHA256_B),
        "mid": _RecordingTask("mid", frozenset({"mid/out"}), call_log, sha256=_VALID_SHA256_C),
        "top": _RecordingTask("top", frozenset({"top/out"}), call_log, sha256=_VALID_SHA256_D),
    }

    result = TaskExecutor().execute(planned, tasks, context, verified_frontier=frontier)

    # leaf 在 verified set 中：不得调用 execute；mid/top 必须执行。
    assert [name for name, _ in call_log] == ["mid", "top"]
    assert call_log[0] == ("mid", frozenset({"leaf/out"}))
    assert call_log[1] == ("top", frozenset({"mid/out"}))

    # 注入的是 frontier 中已验证产物（sha256 A），而非 leaf 任务若执行会产生的 B。
    by_path = {item.logical_path: item for item in result.artifacts}
    assert by_path["leaf/out"].blob.sha256 == _VALID_SHA256_A
    assert by_path["mid/out"].blob.sha256 == _VALID_SHA256_C
    assert by_path["top/out"].blob.sha256 == _VALID_SHA256_D

    hints = get_type_hints(TaskExecutor.execute)
    frontier_hint = hints.get("verified_frontier")
    assert frontier_hint is not None
    hint_text = str(frontier_hint)
    assert "VerifiedFrontier" in hint_text
    assert "ExecutionFrontier" not in hint_text or "VerifiedFrontier" in hint_text
    # 签名不得把原始 ExecutionFrontier / CompletedTaskRecord 标为可接受类型。
    source = inspect.getsource(TaskExecutor.execute)
    assert "CompletedTaskRecord" not in source
    assert "ExecutionFrontier" not in source

    executor = TaskExecutor()
    with pytest.raises(TypeError):
        executor.execute(
            planned,
            tasks,
            context,
            verified_frontier=ExecutionFrontier,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        executor.execute(
            planned,
            tasks,
            context,
            verified_frontier=CompletedTaskRecord(  # type: ignore[arg-type]
                task_name="leaf",
                task_identity=planned.expected_identities["leaf"],
                outputs=verified_leaf_outputs,
                request_digest=context.request_digest,
                revision=context.revision,
                toolchain_digest=context.toolchain_digest,
                baseline_id=context.baseline_id,
                schema_version=context.schema_version,
                upstream_identities=(),
            ),
        )


@pytest.mark.parametrize(
    ("case_name", "result_factory"),
    [
        (
            "missing",
            lambda: TaskResult(
                outputs=(_artifact("ok/a", sha256=_VALID_SHA256_A, source_task="bad"),)
            ),
        ),
        (
            "undeclared",
            lambda: TaskResult(
                outputs=(
                    _artifact("ok/a", sha256=_VALID_SHA256_A, source_task="bad"),
                    _artifact("ok/b", sha256=_VALID_SHA256_B, source_task="bad"),
                    _artifact("ok/extra", sha256=_VALID_SHA256_C, source_task="bad"),
                )
            ),
        ),
        (
            "duplicate",
            lambda: TaskResult(
                outputs=(
                    _artifact("ok/a", sha256=_VALID_SHA256_A, source_task="bad"),
                    _artifact("ok/b", sha256=_VALID_SHA256_B, source_task="bad"),
                    _artifact("ok/a", sha256=_VALID_SHA256_C, source_task="bad"),
                )
            ),
        ),
    ],
)
def test_executor_rejects_missing_undeclared_and_duplicate_task_outputs(
    case_name: str,
    result_factory: Callable[[], TaskResult],
) -> None:
    """验证 TaskExecutor 在登记前拒绝缺失、未声明与重复输出。

    参数：
        case_name: 参数化场景名（missing / undeclared / duplicate）。
        result_factory: 构造非法 ``TaskResult`` 的工厂。

    返回：
        无。断言三种非法结果均在写入 registry 或调度下游前抛出
        ``ArtifactValidationError``，消息列出 missing、undeclared、duplicates；
        仅当实际路径集合与 ``TaskSpec.outputs`` 完全相等时才登记。

    当前两步最小 GREEN 直接登记 ``TaskResult.outputs`` 时，三种无效结果均不会
    抛出 ``ArtifactValidationError``，每个 ``pytest.raises`` 确定失败。除导入与
    内存构造外不产生外部副作用。
    """
    del case_name
    declared = frozenset({"ok/a", "ok/b"})
    plan = _plan("bad", outputs=declared)
    context = _context()
    planned = BuildPlanner().plan((plan,), context)

    call_log: list[tuple[str, frozenset[str]]] = []
    illegal = result_factory()

    class _BadOutputTask(_RecordingTask):
        """返回预置非法 TaskResult 的任务桩。

        职责：
            覆盖 ``execute``，忽略声明路径，直接返回参数化非法结果。
        """

        def execute(
            self,
            context: BuildContext,
            inputs: ArtifactCollection,
        ) -> TaskResult:
            """记录调用后返回预置非法结果。

            参数：
                context: 共享构建上下文。
                inputs: 上游产物集合。

            返回：
                参数化构造的非法 ``TaskResult``。
            """
            del context
            self._call_log.append((self._name, frozenset(inputs)))
            return illegal

    tasks = {
        "bad": _BadOutputTask("bad", declared, call_log, sha256=_VALID_SHA256_A),
    }

    with pytest.raises(ArtifactValidationError) as exc_info:
        TaskExecutor().execute(planned, tasks, context, verified_frontier=None)

    message = str(exc_info.value).lower()
    assert "missing" in message or "undeclared" in message or "duplicate" in message
    # 失败发生在登记前：不应出现任何已收集产物的成功结果。
    assert call_log == [("bad", frozenset())]

    # 合法等集结果应通过，证明只拒绝契约违例。
    good_log: list[tuple[str, frozenset[str]]] = []
    good_tasks = {
        "ok": _RecordingTask("ok", declared, good_log, sha256=_VALID_SHA256_A),
    }
    good_plan = _plan("ok", outputs=declared)
    good_planned = BuildPlanner().plan((good_plan,), context)
    good_result = TaskExecutor().execute(good_planned, good_tasks, context, verified_frontier=None)
    assert {item.logical_path for item in good_result.artifacts} == declared


def test_executor_stops_after_task_failure_without_recursive_restart() -> None:
    """验证任务失败包装为 ToolExecutionError 且停止新调度。

    测试无参数和返回值。断言：

    - 节点抛出的 ``RuntimeError`` 被包装为 ``ToolExecutionError`` 并保留 cause；
    - 失败后同层后续节点与更深层节点均不执行；
    - 执行器不会递归调用自身或重跑已成功节点。

    当前三步最小 GREEN 让原始 ``RuntimeError`` 直接逸出时，测试期待
    ``ToolExecutionError`` 确定失败。除导入与内存构造外不产生外部副作用。
    """
    plan_a = _plan("ok.a", outputs=frozenset({"ok.a/out"}))
    plan_b = _plan("fail.b", outputs=frozenset({"fail.b/out"}))
    plan_c = _plan("skip.c", outputs=frozenset({"skip.c/out"}))
    plan_d = _plan(
        "skip.d",
        ("ok.a",),
        outputs=frozenset({"skip.d/out"}),
    )
    context = _context()
    # 层 0：fail.b、ok.a、skip.c（UTF-8：fail.b < ok.a < skip.c）
    # 层 1：skip.d
    planned = BuildPlanner().plan((plan_a, plan_b, plan_c, plan_d), context)
    assert planned.layers[0] == ("fail.b", "ok.a", "skip.c")

    call_log: list[tuple[str, frozenset[str]]] = []
    execute_counts: dict[str, int] = {}

    def _fail_on_b(name: str, inputs: ArtifactCollection) -> None:
        """在 fail.b 上抛出 RuntimeError。

        参数：
            name: 当前任务名。
            inputs: 上游产物集合（本钩子忽略）。
        """
        del inputs
        execute_counts[name] = execute_counts.get(name, 0) + 1
        if name == "fail.b":
            raise RuntimeError("simulated tool failure")

    def _count_only(name: str, inputs: ArtifactCollection) -> None:
        """仅累计调用次数。

        参数：
            name: 当前任务名。
            inputs: 上游产物集合（本钩子忽略）。
        """
        del inputs
        execute_counts[name] = execute_counts.get(name, 0) + 1

    tasks: Mapping[str, _RecordingTask] = {
        "fail.b": _RecordingTask(
            "fail.b",
            frozenset({"fail.b/out"}),
            call_log,
            sha256=_VALID_SHA256_A,
            execute_hook=_fail_on_b,
        ),
        "ok.a": _RecordingTask(
            "ok.a",
            frozenset({"ok.a/out"}),
            call_log,
            sha256=_VALID_SHA256_B,
            execute_hook=_count_only,
        ),
        "skip.c": _RecordingTask(
            "skip.c",
            frozenset({"skip.c/out"}),
            call_log,
            sha256=_VALID_SHA256_C,
            execute_hook=_count_only,
        ),
        "skip.d": _RecordingTask(
            "skip.d",
            frozenset({"skip.d/out"}),
            call_log,
            sha256=_VALID_SHA256_D,
            execute_hook=_count_only,
        ),
    }

    executor = TaskExecutor()
    original_execute = executor.execute
    recursive_calls = {"count": 0}

    def _guarded_execute(*args: object, **kwargs: object) -> ExecutionResult:
        """检测执行器是否递归调用自身。

        参数：
            args: 位置参数。
            kwargs: 关键字参数。

        返回：
            原始 ``execute`` 的结果。
        """
        recursive_calls["count"] += 1
        if recursive_calls["count"] > 1:
            raise AssertionError("执行器不得递归调用自身以重跑流水线")
        return original_execute(*args, **kwargs)  # type: ignore[arg-type]

    executor.execute = _guarded_execute  # type: ignore[method-assign]

    with pytest.raises(ToolExecutionError) as exc_info:
        executor.execute(planned, tasks, context, verified_frontier=None)

    err = exc_info.value
    assert isinstance(err.__cause__, RuntimeError)
    assert "simulated tool failure" in str(err.__cause__)

    # fail.b 是层内第一个；失败后 ok.a / skip.c / skip.d 均不得执行。
    assert [name for name, _ in call_log] == ["fail.b"]
    assert execute_counts == {"fail.b": 1}
    assert recursive_calls["count"] == 1
