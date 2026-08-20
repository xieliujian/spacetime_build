"""验证资源构建用例按 plan、DAG、execute、manifest 顺序组合。"""

from dataclasses import dataclass

from application.build_resources import BuildResourcesUseCase
from application.model import ApplicationRequest
from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from core.platforms import BuildPlatform
from core.tasks import BuildContext, TaskPlan, TaskResult, TaskSpec


def _artifact(path: str, task: str) -> LogicalArtifact:
    """创建可复用的内存逻辑产物。"""
    digest = ("a" if path == "a.txt" else "b") * 64
    return LogicalArtifact(
        path,
        ArtifactKind.FILE,
        BlobRef(f"blobs/{digest}", digest, 1),
        (),
        frozenset(),
        ArtifactMetadata(task, "123", "toolchain", ()),
    )


@dataclass
class _Task:
    """提供完整 plan 和确定性结果的假任务。"""

    task_name: str
    output: str
    executed: int = 0

    @property
    def name(self) -> str:
        """返回任务名。"""
        return self.task_name

    def plan(self, context: BuildContext) -> TaskPlan:
        """返回任务计划。"""
        return TaskPlan(
            TaskSpec(self.task_name, (), frozenset({self.output}), "1", ()),
            "input",
            "config",
        )

    def execute(self, context: BuildContext, inputs: object) -> TaskResult:
        """返回声明的单个产物。"""
        self.executed += 1
        return TaskResult((_artifact(self.output, self.task_name),))


def test_resource_use_case_produces_manifest_from_fake_task() -> None:
    """Given 假任务，When 执行资源用例，Then 先规划后执行并生成清单。"""
    task = _Task("config", "a.txt")
    request = ApplicationRequest("run-1", "release", "123", BuildPlatform.ANDROID, False)
    context = BuildContext("request", "123", "toolchain", None, 1)

    result = BuildResourcesUseCase().run(request, context, {"config": task})

    assert result.manifest is not None
    assert task.executed == 1
    assert result.state.value == "succeeded"


def test_resource_use_case_dry_run_does_not_execute_tasks() -> None:
    """Given dry-run 请求，When 生成计划，Then 不调用任务 execute。"""
    task = _Task("config", "a.txt")
    request = ApplicationRequest("run-1", "release", "123", BuildPlatform.ANDROID, True)
    context = BuildContext("request", "123", "toolchain", None, 1)

    result = BuildResourcesUseCase().run(request, context, {"config": task})

    assert result.manifest is None
    assert result.planned_build is not None
    assert task.executed == 0
