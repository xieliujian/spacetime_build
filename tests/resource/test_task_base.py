"""资源任务基类的契约测试。"""

from pathlib import Path

import pytest

from core.tasks import ArtifactCollection
from core.platforms import BuildPlatform
from core.tasks import BuildContext, TaskResult
from release.entries import ResourceVariant
from resource.model import ResourceBuildInput, ResourceKind
from resource.task_base import ResourceBuildTask


class _Task(ResourceBuildTask):
    """测试用最小资源任务。"""

    def __init__(self, output_paths: tuple[str, ...]) -> None:
        """保存测试输出。"""
        super().__init__(
            resource_input=ResourceBuildInput(
                source_snapshot_id="source-1",
                resource_snapshot_id="resource-1",
                platform=BuildPlatform.WINDOWS,
                variant=ResourceVariant.MAIN,
                rule_version="rule-1",
                baseline_manifest_id=None,
            ),
            kind=ResourceKind.CONFIG,
            name="config",
            implementation_version="1",
        )
        self._output_paths = output_paths

    def discover_outputs(self, source_root: Path) -> tuple[str, ...]:
        """返回固定测试输出。"""
        del source_root
        return self._output_paths

    def build(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """返回空结果，验证基类契约。"""
        del context, inputs
        return TaskResult(())


def _context() -> BuildContext:
    """构造固定构建上下文。"""
    return BuildContext("a" * 64, "r1", "b" * 64, None, 1)


def test_resource_task_plan_owns_exact_outputs_without_dependencies(tmp_path: Path) -> None:
    """验证资源任务计划固定无依赖且输出集合唯一。"""
    task = _Task(("config/a.bin", "config/a.txt"))
    plan = task.plan(_context(), tmp_path)
    assert plan.spec.dependencies == ()
    assert plan.spec.outputs == frozenset({"config/a.bin", "config/a.txt"})


def test_resource_task_rejects_duplicate_or_invalid_outputs(tmp_path: Path) -> None:
    """验证任务不能声明重复或非法逻辑输出。"""
    with pytest.raises(ValueError):
        _Task(("config/a.bin", "config/a.bin")).plan(_context(), tmp_path)
    with pytest.raises(ValueError):
        _Task(("../escape",)).plan(_context(), tmp_path)
