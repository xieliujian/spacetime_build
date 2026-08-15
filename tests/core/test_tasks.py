"""验证任务协议、规划契约与 TaskIdentity 身份摘要。

本模块按第二阶段 Task 7 分步覆盖 ``TaskSpec`` / ``TaskPlan`` / ``BuildTask``
规划契约，以及仅从 ``TaskPlan`` 生成的 ``TaskIdentity``。测试不访问 SVN、
Unity、Jenkins 或 CDN，也不执行真实构建副作用。
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import get_type_hints

import pytest

from core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    BlobRef,
    LogicalArtifact,
)
from core.manifest_codec import canonical_json_bytes
from core.tasks import (
    ArtifactCollection,
    BuildContext,
    BuildTask,
    TaskIdentity,
    TaskPlan,
    TaskResult,
    TaskSpec,
)

_VALID_SHA256 = "c" * 64


def _sample_artifact(logical_path: str) -> LogicalArtifact:
    """构造测试用合法 ``LogicalArtifact``。

    参数：
        logical_path: 客户端逻辑路径。

    返回：
        含最小合法字段的不可变逻辑产物。
    """
    return LogicalArtifact(
        logical_path=logical_path,
        kind=ArtifactKind.ASSET_BUNDLE,
        blob=BlobRef(
            locator=f"sha256:{_VALID_SHA256}",
            sha256=_VALID_SHA256,
            size=512,
        ),
        dependencies=(),
        subpackage_ids=frozenset(),
        metadata=ArtifactMetadata(
            source_task="scene.build",
            source_revision="r100",
            toolchain_digest="toolchain-v1",
            attributes=(),
        ),
    )


def test_task_spec_plan_and_build_task_share_single_planning_contract() -> None:
    """验证 TaskSpec、TaskPlan 与 BuildTask 共享唯一规划契约。

    测试无参数和返回值。断言：

    - ``TaskSpec`` 保存 name、有序 dependencies、无序 outputs、实现版本和执行属性；
    - ``BuildTask.plan(context)`` 返回引用同一 spec 的完整不可变 ``TaskPlan``；
    - ``execute(context, inputs)`` 返回 ``TaskResult``，其 ``outputs`` 为 tuple；
    - 协议没有 SVN、Unity、Jenkins 或上传方法。

    当 ``core.tasks`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入与内存构造外不产生外部副作用。
    """
    spec = TaskSpec(
        name="scene.build",
        dependencies=("shared.base", "config.build", "shared.base"),
        outputs=frozenset({"scene/a.assetbundle", "scene/b.assetbundle"}),
        implementation_version="1.0.0",
        execution_attributes=(
            ("cacheable", "true"),
            ("parallel", "true"),
        ),
    )
    assert spec.name == "scene.build"
    assert spec.dependencies == ("shared.base", "config.build", "shared.base")
    assert spec.outputs == frozenset({"scene/a.assetbundle", "scene/b.assetbundle"})
    assert spec.implementation_version == "1.0.0"
    assert spec.execution_attributes == (
        ("cacheable", "true"),
        ("parallel", "true"),
    )
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        spec.name = "other"  # type: ignore[misc]

    context = BuildContext(
        request_digest="req-digest-1",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id="baseline-1",
        schema_version=1,
    )
    assert context.request_digest == "req-digest-1"
    assert context.revision == "r100"
    assert context.toolchain_digest == "toolchain-v1"
    assert context.baseline_id == "baseline-1"
    assert context.schema_version == 1
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        context.revision = "r200"  # type: ignore[misc]

    class _StubTask:
        """满足 BuildTask 协议的最小桩实现。

        职责：
            在测试中固定返回给定 ``TaskSpec`` 的 ``TaskPlan``，以及 tuple 形式的
            ``TaskResult.outputs``，用于验证协议契约。

        参数：
            无构造参数；``plan`` / ``execute`` 接收上下文与输入集合。

        返回：
            ``plan`` 返回 ``TaskPlan``；``execute`` 返回 ``TaskResult``。

        异常：
            无。

        约束与副作用：
            纯内存桩，不访问外部系统。
        """

        @property
        def name(self) -> str:
            """返回任务逻辑名。

            返回：
                与 ``TaskSpec.name`` 一致的任务名字符串。
            """
            return spec.name

        def plan(self, context: BuildContext) -> TaskPlan:
            """根据上下文生成完整不可变 ``TaskPlan``。

            参数：
                context: 构建上下文；本桩不读取字段，仅满足签名。

            返回：
                引用同一 ``spec`` 且含 resolved input/config digest 的 ``TaskPlan``。
            """
            return TaskPlan(
                spec=spec,
                resolved_input_digest="input-digest-1",
                config_digest="config-digest-1",
            )

        def execute(
            self,
            context: BuildContext,
            inputs: ArtifactCollection,
        ) -> TaskResult:
            """执行任务并返回 tuple 形式输出。

            参数：
                context: 构建上下文。
                inputs: 上游产物集合。

            返回：
                ``outputs`` 为 ``tuple[LogicalArtifact, ...]`` 的 ``TaskResult``。
            """
            del context, inputs
            return TaskResult(
                outputs=(
                    _sample_artifact("scene/a.assetbundle"),
                    _sample_artifact("scene/b.assetbundle"),
                )
            )

    task: BuildTask = _StubTask()
    assert isinstance(task, BuildTask)

    plan = task.plan(context)
    assert isinstance(plan, TaskPlan)
    assert plan.spec is spec
    assert plan.resolved_input_digest == "input-digest-1"
    assert plan.config_digest == "config-digest-1"
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        plan.config_digest = "other"  # type: ignore[misc]

    artifact_a = _sample_artifact("shared/base.assetbundle")
    inputs = ArtifactCollection.from_artifacts((artifact_a,))
    assert inputs["shared/base.assetbundle"] is artifact_a
    assert list(inputs) == ["shared/base.assetbundle"]
    assert len(inputs) == 1

    result = task.execute(context, inputs)
    assert isinstance(result, TaskResult)
    assert isinstance(result.outputs, tuple)
    assert len(result.outputs) == 2
    assert result.outputs[0].logical_path == "scene/a.assetbundle"
    assert result.outputs[1].logical_path == "scene/b.assetbundle"

    # 协议不得暴露集成层副作用入口。
    forbidden = (
        "svn",
        "unity",
        "jenkins",
        "upload",
        "commit",
        "publish",
        "cdn",
    )
    for attr_name in dir(BuildTask):
        lowered = attr_name.lower()
        assert not any(token in lowered for token in forbidden)

    hints = get_type_hints(BuildTask.plan)
    assert hints["context"].__name__ == "BuildContext" or hints["context"] is BuildContext
    assert hints["return"].__name__ == "TaskPlan" or hints["return"] is TaskPlan
    exec_hints = get_type_hints(BuildTask.execute)
    assert (
        exec_hints["inputs"].__name__ == "ArtifactCollection"
        or exec_hints["inputs"] is ArtifactCollection
    )
    assert exec_hints["return"].__name__ == "TaskResult" or exec_hints["return"] is TaskResult


def _expected_identity_digest(
    plan: TaskPlan,
    context: BuildContext,
    upstream_identities: tuple[TaskIdentity, ...],
) -> str:
    """按规范 JSON 计算期望的任务身份摘要（测试侧独立实现）。

    参数：
        plan: 完整 ``TaskPlan``。
        context: 构建上下文。
        upstream_identities: 有序上游 ``TaskIdentity`` 元组。

    返回：
        64 位小写十六进制 SHA256 摘要字符串。

    异常：
        无；输入须为可规范编码的领域对象。

    约束与副作用：
        仅用于断言，不依赖生产 ``TaskIdentity`` 内部私有 helper。
    """
    payload = {
        "baseline_id": context.baseline_id,
        "config_digest": plan.config_digest,
        "request_digest": context.request_digest,
        "resolved_input_digest": plan.resolved_input_digest,
        "revision": context.revision,
        "schema_version": context.schema_version,
        "spec": {
            "dependencies": list(plan.spec.dependencies),
            "execution_attributes": [list(pair) for pair in plan.spec.execution_attributes],
            "implementation_version": plan.spec.implementation_version,
            "name": plan.spec.name,
            # 无序 outputs：按 UTF-8 字节序排序后编码，保证确定性。
            "outputs": sorted(plan.spec.outputs, key=lambda item: item.encode("utf-8")),
        },
        "toolchain_digest": context.toolchain_digest,
        "upstream_identities": [item.digest for item in upstream_identities],
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def test_task_identity_covers_request_revision_toolchain_baseline_schema_and_upstream() -> None:
    """验证 TaskIdentity 仅从 TaskPlan 生成且覆盖上下文与上游身份。

    测试无参数和返回值。断言：

    - ``TaskIdentity.from_plan(plan, context, upstream_identities)`` 的摘要覆盖
      ``TaskPlan`` 的 spec、resolved input/config digest，以及 context 的请求、
      固定 revision、工具链、基线、schema 和有序 upstream identities；
    - 公共 API 不提供 ``TaskIdentity.from_spec``；
    - 身份对象不可变。

    当前一步最小 GREEN 尚未定义 ``TaskIdentity`` 时，测试导入阶段应以
    ``ImportError`` 失败。除导入与内存计算外不产生外部副作用。
    """
    assert not hasattr(TaskIdentity, "from_spec")
    assert "from_spec" not in getattr(TaskIdentity, "__dict__", {})

    upstream_spec = TaskSpec(
        name="shared.base",
        dependencies=(),
        outputs=frozenset({"shared/base.assetbundle"}),
        implementation_version="1.0.0",
        execution_attributes=(("cacheable", "true"),),
    )
    upstream_plan = TaskPlan(
        spec=upstream_spec,
        resolved_input_digest="upstream-input",
        config_digest="upstream-config",
    )
    context = BuildContext(
        request_digest="req-digest-1",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id="baseline-1",
        schema_version=1,
    )
    upstream = TaskIdentity.from_plan(upstream_plan, context, ())
    assert isinstance(upstream, TaskIdentity)
    assert upstream.digest == _expected_identity_digest(upstream_plan, context, ())

    spec = TaskSpec(
        name="scene.build",
        dependencies=("shared.base", "config.build", "shared.base"),
        outputs=frozenset({"scene/b.assetbundle", "scene/a.assetbundle"}),
        implementation_version="2.1.0",
        execution_attributes=(
            ("parallel", "true"),
            ("cacheable", "false"),
        ),
    )
    plan = TaskPlan(
        spec=spec,
        resolved_input_digest="scene-input-digest",
        config_digest="scene-config-digest",
    )
    identity = TaskIdentity.from_plan(plan, context, (upstream,))
    assert identity.digest == _expected_identity_digest(plan, context, (upstream,))
    assert len(identity.digest) == 64
    assert identity.digest == identity.digest.lower()

    # 覆盖项任一变化都必须改变摘要。
    mutated_context = BuildContext(
        request_digest="req-digest-2",
        revision=context.revision,
        toolchain_digest=context.toolchain_digest,
        baseline_id=context.baseline_id,
        schema_version=context.schema_version,
    )
    assert TaskIdentity.from_plan(plan, mutated_context, (upstream,)).digest != identity.digest

    mutated_plan = TaskPlan(
        spec=spec,
        resolved_input_digest="scene-input-digest-other",
        config_digest=plan.config_digest,
    )
    assert TaskIdentity.from_plan(mutated_plan, context, (upstream,)).digest != identity.digest

    mutated_spec_plan = TaskPlan(
        spec=TaskSpec(
            name=spec.name,
            dependencies=spec.dependencies,
            outputs=spec.outputs,
            implementation_version="9.9.9",
            execution_attributes=spec.execution_attributes,
        ),
        resolved_input_digest=plan.resolved_input_digest,
        config_digest=plan.config_digest,
    )
    assert TaskIdentity.from_plan(mutated_spec_plan, context, (upstream,)).digest != identity.digest

    empty_upstream = TaskIdentity.from_plan(plan, context, ())
    assert empty_upstream.digest != identity.digest

    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        identity.digest = "0" * 64  # type: ignore[misc]
