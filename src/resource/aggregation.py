"""显式资源任务结果聚合和 BuildManifest 组装。

聚合器只消费调用方明确提供的 ``ResourceBuildResult``，不扫描目录、不补齐缺失任务、
不自动执行 Jenkins 顺序。它验证无依赖任务的输出所有权和身份，再通过现有
``BuildManifestFactory`` 生成确定性清单；发布语义仍由 ``release`` 层负责。
"""

from __future__ import annotations

from collections.abc import Iterable

from core.artifacts import LogicalArtifact
from core.build_records import BuildManifest, BuildManifestPayload
from core.errors import ArtifactValidationError
from core.manifest_codec import BuildManifestFactory
from core.tasks import BuildContext, TaskIdentity
from resource.task_service import ResourceBuildResult


class ResourceManifestAggregator:
    """把显式任务结果聚合为确定性 ``BuildManifest``。

    职责：
        检查结果唯一性、任务无依赖、任务身份可重算、产物路径不冲突，并按任务名
        稳定排序身份和产物来源。

    参数：
        required_tasks: 可选正式发布任务集合；提供后必须恰好收到这些任务。

    返回：
        ``aggregate`` 返回现有核心 ``BuildManifest``。

    异常：
        缺失/多余任务、身份不一致、输出冲突或上下文类型错误时抛出
        ``ValueError`` / ``ArtifactValidationError``。

    约束与副作用：
        纯内存聚合；不生成 ReleaseSnapshot，不写兼容协议，不上传和激活。
    """

    def __init__(self, required_tasks: Iterable[str] = ()) -> None:
        """冻结正式任务集合并校验任务名。

        参数：
            required_tasks: 需要完整聚合的任务名迭代器。

        返回：
            ``None``。

        异常：
            空任务名或重复任务名时抛出 ``ValueError``。

        约束与副作用：
            只保存排序后的 tuple，不执行任务。
        """
        names = tuple(required_tasks)
        if any(not isinstance(name, str) or not name for name in names):
            raise ValueError("required_tasks 必须是非空字符串元组")
        if len(set(names)) != len(names):
            raise ValueError("required_tasks 不得重复")
        self._required_tasks = tuple(sorted(names, key=lambda value: value.encode("utf-8")))

    def aggregate(
        self,
        context: BuildContext,
        results: Iterable[ResourceBuildResult],
    ) -> BuildManifest:
        """验证并聚合显式任务结果。

        参数：
            context: 所有任务共享的构建上下文。
            results: 调用方显式传入的成功任务结果。

        返回：
            由现有 ``BuildManifestFactory`` 创建的确定性构建清单。

        异常：
            任务集合、身份、输出或上下文不一致时抛出 ``ValueError`` /
            ``ArtifactValidationError``。

        约束与副作用：
            不修改输入结果；任务身份按计划和共享上下文重新计算。
        """
        if not isinstance(context, BuildContext):
            raise TypeError("context 必须是 BuildContext")
        materialized = tuple(results)
        by_name: dict[str, ResourceBuildResult] = {}
        artifacts: list[LogicalArtifact] = []
        for item in materialized:
            if not isinstance(item, ResourceBuildResult):
                raise TypeError("results 必须全部是 ResourceBuildResult")
            if item.task_name in by_name:
                raise ValueError(f"任务结果重复: {item.task_name}")
            if item.plan.spec.dependencies:
                raise ValueError(f"资源任务不得有隐式依赖: {item.task_name}")
            expected = TaskIdentity.from_plan(item.plan, context, ())
            if expected != item.identity:
                raise ArtifactValidationError(f"任务身份不匹配: {item.task_name}")
            by_name[item.task_name] = item
            artifacts.extend(item.result.outputs)
        actual_names = frozenset(by_name)
        if self._required_tasks and actual_names != frozenset(self._required_tasks):
            raise ValueError(
                "正式聚合任务集合不完整："
                f"actual={sorted(actual_names)!r}, required={list(self._required_tasks)!r}"
            )
        paths = [artifact.logical_path for artifact in artifacts]
        if len(paths) != len(set(paths)):
            raise ArtifactValidationError("不同资源任务的逻辑输出发生冲突")
        ordered = tuple(
            by_name[name] for name in sorted(by_name, key=lambda value: value.encode("utf-8"))
        )
        payload = BuildManifestPayload(
            schema_version=context.schema_version,
            request_digest=context.request_digest,
            revision=context.revision,
            toolchain_digest=context.toolchain_digest,
            baseline_id=context.baseline_id,
            artifacts=tuple(artifact for item in ordered for artifact in item.result.outputs),
            task_identities=tuple(item.identity.digest for item in ordered),
        )
        return BuildManifestFactory.create(payload)
