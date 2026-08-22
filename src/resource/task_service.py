"""资源单任务执行用例。

本模块把资源任务的 ``plan -> identity -> execute -> output validation`` 固定为一个
纯 Python 服务。服务不自动触发其他任务、不扫描结果目录、不提交 SVN 或发布 CDN；
调用方必须显式传入任务和输入产物。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.artifacts import LogicalArtifact
from core.tasks import ArtifactCollection, BuildContext, TaskIdentity, TaskPlan, TaskResult
from resource.task_base import ResourceBuildTask


@dataclass(frozen=True, slots=True)
class ResourceBuildResult:
    """单个资源任务的确定性结果与任务身份。

    职责：
        把完整任务计划、身份和成功产物绑定，供跨 Jenkins 节点结果交接或显式聚合
        使用；不包含运行时间、日志路径或发布版本。

    参数：
        task_name: 任务逻辑名。
        plan: 已执行的任务计划。
        identity: 从计划和上下文重算的任务身份。
        result: 已通过输出集合校验的任务结果。

    返回：
        无；不可变数据对象。

    异常：
        无额外异常；字段由服务创建。

    约束与副作用：
        不保存工作区路径，不执行 I/O。
    """

    task_name: str
    plan: TaskPlan
    identity: TaskIdentity
    result: TaskResult


def _validate_outputs(plan: TaskPlan, result: TaskResult) -> None:
    """确认实际产物集合与任务声明严格相等。

    参数：
        plan: 任务声明的精确输出集合。
        result: 任务实际返回的产物。

    返回：
        ``None``，表示可进入结果交接。

    异常：
        产物类型、重复路径、缺失路径或未声明路径时抛出 ``ValueError`` / ``TypeError``。

    约束与副作用：
        纯内存校验；不登记、不上传、不修补输出。
    """
    if not isinstance(result, TaskResult):
        raise TypeError("result 必须是 TaskResult")
    paths: list[str] = []
    for artifact in result.outputs:
        if not isinstance(artifact, LogicalArtifact):
            raise TypeError("result.outputs 必须全部是 LogicalArtifact")
        paths.append(artifact.logical_path)
    if len(paths) != len(set(paths)):
        raise ValueError("任务结果包含重复逻辑路径")
    if frozenset(paths) != plan.spec.outputs:
        raise ValueError(
            f"任务 {plan.spec.name} 实际输出与声明不一致："
            f"actual={sorted(paths)!r}, expected={sorted(plan.spec.outputs)!r}"
        )


class ResourceBuildService:
    """显式执行一个资源任务的应用服务。

    职责：
        调用单一任务的精确规划和执行，重算无上游任务身份，并拒绝输出污染。

    参数：
        无；服务无隐式当前构建状态。

    返回：
        ``build`` 返回可交接的 ``ResourceBuildResult``。

    异常：
        任务类型、上下文或输出契约非法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        不自动执行依赖任务；外部文件和 CAS 副作用只能由任务收到的端口产生。
    """

    def build(
        self,
        task: ResourceBuildTask,
        context: BuildContext,
        inputs: ArtifactCollection,
    ) -> ResourceBuildResult:
        """执行指定资源任务并返回经过身份绑定的结果。

        参数：
            task: 一个具体 ``ResourceBuildTask``。
            context: 共享构建上下文。
            inputs: 调用方显式传入的产物集合。

        返回：
            含计划、身份和完整产物的 ``ResourceBuildResult``。

        异常：
            规划、工具执行或输出契约失败时透传对应异常；不会重试任务。

        约束与副作用：
            单次调用只执行传入 task，不读取其他任务目录，不自动补齐结果。
        """
        if not isinstance(task, ResourceBuildTask):
            raise TypeError("task 必须是 ResourceBuildTask")
        if not isinstance(context, BuildContext):
            raise TypeError("context 必须是 BuildContext")
        if not isinstance(inputs, ArtifactCollection):
            raise TypeError("inputs 必须是 ArtifactCollection")
        plan = task.plan_with_inputs(context, inputs)
        if plan.spec.dependencies:
            raise ValueError("正式版本资源任务不得声明工具内置依赖")
        identity = TaskIdentity.from_plan(plan, context, ())
        result = task.execute(context, inputs)
        _validate_outputs(plan, result)
        return ResourceBuildResult(task.name, plan, identity, result)
