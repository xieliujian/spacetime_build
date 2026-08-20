"""资源构建 application 用例编排。

本模块只组合已有的 ``BuildTask.plan``、``BuildPlanner``、``TaskExecutor`` 和
``BuildManifestFactory``。它不定义资源类型规则、不拼 Unity 参数、不访问 SVN/CDN；
dry-run 只完成计划，正式执行才登记内存产物并生成确定性 BuildManifest。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from application.model import ApplicationRequest, RunState
from core.build_records import BuildManifest, BuildManifestPayload
from core.errors import BuildError
from core.executor import ExecutionResult, TaskExecutor
from core.frontier import VerifiedFrontier
from core.manifest_codec import BuildManifestFactory
from core.planner import BuildPlanner, PlannedBuild
from core.tasks import BuildContext, BuildTask, TaskPlan


class ResourceBuildError(BuildError):
    """表示资源用例输入、规划或任务输出不满足统一契约。"""


@dataclass(frozen=True, slots=True)
class ResourceBuildResult:
    """资源用例的计划、执行结果和可选确定性 BuildManifest。"""

    state: RunState
    planned_build: PlannedBuild
    execution: ExecutionResult | None
    manifest: BuildManifest | None


class BuildResourcesUseCase:
    """编排资源任务的 plan、DAG、execute 和 manifest 阶段。"""

    def __init__(
        self,
        planner: BuildPlanner | None = None,
        executor: TaskExecutor | None = None,
    ) -> None:
        """保存可替换的纯 Python planner/executor；不执行任务。"""
        self._planner = planner if planner is not None else BuildPlanner()
        self._executor = executor if executor is not None else TaskExecutor()

    def run(
        self,
        request: ApplicationRequest,
        context: BuildContext,
        tasks: Mapping[str, BuildTask],
        *,
        verified_frontier: VerifiedFrontier | None = None,
    ) -> ResourceBuildResult:
        """按统一顺序规划并可选执行资源任务。

        参数：
            request: 已通过 application 请求校验的运行请求。
            context: 固定 revision、工具链和请求摘要上下文。
            tasks: 任务名到 ``BuildTask`` 的显式映射。
            verified_frontier: 可选、已校验的恢复边界。

        返回：
            dry-run 返回无 manifest 的 PLANNED 结果；正式运行返回 SUCCEEDED 和清单。

        异常：
            输入类型、DAG、输出契约或任务执行失败时透传领域异常，并停止后续调度。

        约束与副作用：
            不自动发现任务、不递归重启流水线；本用例自身不写记录或外部对象。
        """
        if not isinstance(request, ApplicationRequest):
            raise ResourceBuildError("request 必须是 ApplicationRequest")
        if not isinstance(context, BuildContext):
            raise ResourceBuildError("context 必须是 BuildContext")
        if not isinstance(tasks, Mapping) or not tasks:
            raise ResourceBuildError("tasks 必须是非空 Mapping")
        plans: list[TaskPlan] = []
        ordered_tasks: dict[str, BuildTask] = {}
        for name in sorted(tasks, key=lambda value: value.encode("utf-8")):
            task = tasks[name]
            if not isinstance(name, str) or not isinstance(task, BuildTask):
                raise ResourceBuildError("tasks 必须是任务名到 BuildTask 的映射")
            if task.name != name:
                raise ResourceBuildError(f"任务映射键与 task.name 不一致: {name}")
            plan = task.plan(context)
            if not isinstance(plan, TaskPlan) or plan.spec.name != name:
                raise ResourceBuildError(f"任务 {name} 未返回匹配的 TaskPlan")
            plans.append(plan)
            ordered_tasks[name] = task
        planned = self._planner.plan(tuple(plans), context)
        if request.dry_run:
            return ResourceBuildResult(RunState.PLANNED, planned, None, None)
        execution = self._executor.execute(planned, ordered_tasks, context, verified_frontier)
        identities = tuple(
            planned.expected_identities[name].digest for layer in planned.layers for name in layer
        )
        payload = BuildManifestPayload(
            schema_version=context.schema_version,
            request_digest=context.request_digest,
            revision=context.revision,
            toolchain_digest=context.toolchain_digest,
            baseline_id=context.baseline_id,
            artifacts=execution.artifacts,
            task_identities=identities,
        )
        manifest = BuildManifestFactory.create(payload)
        return ResourceBuildResult(RunState.SUCCEEDED, planned, execution, manifest)

    def build(self, *args: object, **kwargs: object) -> ResourceBuildResult:
        """``run`` 的命令处理器友好别名。"""
        return self.run(*args, **kwargs)  # type: ignore[arg-type]


__all__ = ["BuildResourcesUseCase", "ResourceBuildError", "ResourceBuildResult"]
