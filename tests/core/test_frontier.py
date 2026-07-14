"""验证可恢复执行 Frontier 的完成证据与身份校验。

本模块按第二阶段 Task 10 分步覆盖 ``CompletedTaskRecord`` / ``ResumeContext``
完成证据，以及 ``ExecutionFrontier.verify`` 对身份、产物哈希与 DAG 传播的
校验。测试不访问 SVN、Unity、Jenkins 或 CDN，也不执行真实构建副作用。
"""

from __future__ import annotations

import dataclasses

import pytest

from st.build.core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    BlobRef,
    LogicalArtifact,
)
from st.build.core.frontier import (
    BlobHashVerifier,
    CompletedTaskRecord,
    ExecutionFrontier,
    ResumeContext,
    VerifiedFrontier,
)
from st.build.core.graph import BuildGraph
from st.build.core.tasks import TaskIdentity, TaskPlan, TaskSpec


_VALID_SHA256_A = "a" * 64
_VALID_SHA256_B = "b" * 64


def _artifact(
    logical_path: str,
    *,
    sha256: str = _VALID_SHA256_A,
    size: int = 128,
    dependencies: tuple[str, ...] = (),
    subpackage_ids: frozenset[int] = frozenset({1, 2}),
) -> LogicalArtifact:
    """构造测试用完整 ``LogicalArtifact``。

    参数：
        logical_path: 客户端逻辑路径。
        sha256: Blob SHA256。
        size: Blob 字节大小。
        dependencies: 有序依赖路径。
        subpackage_ids: 分包 ID 集合。

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
        dependencies=dependencies,
        subpackage_ids=subpackage_ids,
        metadata=ArtifactMetadata(
            source_task="scene.build",
            source_revision="r100",
            toolchain_digest="toolchain-v1",
            attributes=(("quality", "high"),),
        ),
    )


def test_completed_task_record_captures_all_resume_identity_fields() -> None:
    """验证 CompletedTaskRecord 捕获恢复所需的全部身份与完整产物字段。

    测试无参数和返回值。断言：

    - ``outputs`` 是完整不可变 ``tuple[LogicalArtifact, ...]``，保留逻辑路径、
      kind、Blob、依赖、分包和 metadata；
    - 记录同时包含 task identity、request、固定 revision、toolchain、baseline、
      schema 和 upstream identities；
    - 禁止退化为仅保存 BlobRef 集合或最后任务名/布尔 completed。

    当 ``st.build.core.frontier`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入与内存构造外不产生外部副作用。
    """
    upstream = TaskIdentity(digest="u" * 64)
    identity = TaskIdentity(digest="t" * 64)
    outputs = (
        _artifact(
            "scene/a.assetbundle",
            dependencies=("shared/base.assetbundle",),
            subpackage_ids=frozenset({3, 7}),
        ),
        _artifact(
            "scene/b.assetbundle",
            sha256=_VALID_SHA256_B,
            size=256,
        ),
    )

    record = CompletedTaskRecord(
        task_name="scene.build",
        task_identity=identity,
        outputs=outputs,
        request_digest="req-digest-1",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id="baseline-1",
        schema_version=1,
        upstream_identities=(upstream,),
    )

    assert record.task_name == "scene.build"
    assert record.task_identity is identity or record.task_identity == identity
    assert isinstance(record.outputs, tuple)
    assert len(record.outputs) == 2
    assert record.outputs[0].logical_path == "scene/a.assetbundle"
    assert record.outputs[0].kind is ArtifactKind.ASSET_BUNDLE
    assert record.outputs[0].blob.sha256 == _VALID_SHA256_A
    assert record.outputs[0].blob.locator == f"sha256:{_VALID_SHA256_A}"
    assert record.outputs[0].blob.size == 128
    assert record.outputs[0].dependencies == ("shared/base.assetbundle",)
    assert record.outputs[0].subpackage_ids == frozenset({3, 7})
    assert record.outputs[0].metadata.source_task == "scene.build"
    assert record.outputs[0].metadata.attributes == (("quality", "high"),)
    assert record.outputs[1].logical_path == "scene/b.assetbundle"
    assert record.outputs[1].blob.sha256 == _VALID_SHA256_B

    assert record.request_digest == "req-digest-1"
    assert record.revision == "r100"
    assert record.toolchain_digest == "toolchain-v1"
    assert record.baseline_id == "baseline-1"
    assert record.schema_version == 1
    assert record.upstream_identities == (upstream,)

    # 禁止退化为 BlobRef 集合：outputs 元素必须是完整 LogicalArtifact。
    assert all(isinstance(item, LogicalArtifact) for item in record.outputs)
    assert not isinstance(record.outputs[0], BlobRef)

    context = ResumeContext(
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
        record.task_name = "other"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        context.revision = "r200"  # type: ignore[misc]


def _plan(
    name: str,
    dependencies: tuple[str, ...] = (),
    *,
    outputs: frozenset[str] | None = None,
) -> TaskPlan:
    """构造测试用完整 ``TaskPlan``。

    参数：
        name: 任务逻辑名。
        dependencies: 有序上游依赖名。
        outputs: 可选输出路径集合；默认 ``{name}/out``。

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


def _resume_context() -> ResumeContext:
    """构造测试用默认 ``ResumeContext``。

    返回：
        含固定请求、revision、工具链、基线与 schema 的恢复上下文。
    """
    return ResumeContext(
        request_digest="req-digest-1",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id="baseline-1",
        schema_version=1,
    )


def _record(
    task_name: str,
    identity: TaskIdentity,
    *,
    outputs: tuple[LogicalArtifact, ...] | None = None,
    upstream_identities: tuple[TaskIdentity, ...] = (),
    request_digest: str = "req-digest-1",
    revision: str = "r100",
    toolchain_digest: str = "toolchain-v1",
    baseline_id: str | None = "baseline-1",
    schema_version: int = 1,
) -> CompletedTaskRecord:
    """构造测试用 ``CompletedTaskRecord``。

    参数：
        task_name: 任务名。
        identity: 任务身份。
        outputs: 可选产物元组；默认单产物 ``{task_name}/out``。
        upstream_identities: 上游身份元组。
        request_digest / revision / toolchain_digest / baseline_id /
            schema_version: 恢复上下文字段。

    返回：
        不可变完成记录。
    """
    if outputs is None:
        outputs = (_artifact(f"{task_name}/out"),)
    return CompletedTaskRecord(
        task_name=task_name,
        task_identity=identity,
        outputs=outputs,
        request_digest=request_digest,
        revision=revision,
        toolchain_digest=toolchain_digest,
        baseline_id=baseline_id,
        schema_version=schema_version,
        upstream_identities=upstream_identities,
    )


class _AcceptAllVerifier:
    """Step 2 固定接受全部输出的 verifier 替身。

    职责：
        对任意 ``LogicalArtifact`` 返回校验通过，使本步只覆盖身份比较。
    """

    def verify_blob(self, artifact: LogicalArtifact) -> bool:
        """始终接受产物完整性。

        参数：
            artifact: 待校验逻辑产物。

        返回：
            恒为 ``True``。
        """
        return True


def test_frontier_requires_expected_identity_for_every_node_and_rejects_all_identity_mismatches() -> (
    None
):
    """验证 Frontier 要求显式 expected identities 并拒绝全部身份字段失配。

    测试无参数和返回值。断言：调用方显式传入 ``PlannedBuild.expected_identities``；
    缺少任一 expected identity、记录 identity 与 expected 不同，以及 request、
    revision、toolchain、baseline、schema、upstream 任一不同时，该节点均不进入
    ``VerifiedFrontier``。匹配节点保留完整 ``LogicalArtifact`` 元组。

    当 Step 1 最小 GREEN 尚未定义 ``ExecutionFrontier`` / ``VerifiedFrontier`` /
    ``verify`` 时，导入应以 ``ImportError`` 失败。本步 verifier 固定接受全部
    输出。除导入与内存构造外不产生外部副作用。
    """
    leaf_plan = _plan("leaf.ok", outputs=frozenset({"leaf.ok/out"}))
    graph = BuildGraph.from_plans((leaf_plan,))
    expected_id = TaskIdentity(digest="e" * 64)
    matching_outputs = (_artifact("leaf.ok/out"),)
    context = _resume_context()
    verifier = _AcceptAllVerifier()

    # 完整匹配：进入结果，outputs 为完整 LogicalArtifact 元组。
    ok_record = _record("leaf.ok", expected_id, outputs=matching_outputs)
    verified = ExecutionFrontier.verify(
        graph,
        {"leaf.ok": ok_record},
        {"leaf.ok": expected_id},
        context,
        verifier,
    )
    assert isinstance(verified, VerifiedFrontier)
    assert verified.task_names == frozenset({"leaf.ok"})
    assert verified.outputs["leaf.ok"] == matching_outputs
    assert isinstance(verified.outputs["leaf.ok"], tuple)
    assert isinstance(verified.outputs["leaf.ok"][0], LogicalArtifact)

    # 缺少 expected identity。
    missing_expected = ExecutionFrontier.verify(
        graph,
        {"leaf.ok": ok_record},
        {},
        context,
        verifier,
    )
    assert "leaf.ok" not in missing_expected.task_names

    # 记录 identity 与 expected 不同。
    wrong_identity = ExecutionFrontier.verify(
        graph,
        {"leaf.ok": _record("leaf.ok", TaskIdentity(digest="w" * 64))},
        {"leaf.ok": expected_id},
        context,
        verifier,
    )
    assert "leaf.ok" not in wrong_identity.task_names

    mismatch_cases: list[CompletedTaskRecord] = [
        _record("leaf.ok", expected_id, request_digest="other-req"),
        _record("leaf.ok", expected_id, revision="r999"),
        _record("leaf.ok", expected_id, toolchain_digest="other-toolchain"),
        _record("leaf.ok", expected_id, baseline_id="other-baseline"),
        _record("leaf.ok", expected_id, schema_version=99),
        _record(
            "leaf.ok",
            expected_id,
            upstream_identities=(TaskIdentity(digest="x" * 64),),
        ),
    ]
    for bad_record in mismatch_cases:
        rejected = ExecutionFrontier.verify(
            graph,
            {"leaf.ok": bad_record},
            {"leaf.ok": expected_id},
            context,
            verifier,
        )
        assert "leaf.ok" not in rejected.task_names

    # 有上游时，upstream identities 必须与 expected 上游一致。
    upstream_plan = _plan("shared.base")
    child_plan = _plan(
        "scene.build",
        ("shared.base",),
        outputs=frozenset({"scene.build/out"}),
    )
    dep_graph = BuildGraph.from_plans((upstream_plan, child_plan))
    upstream_id = TaskIdentity(digest="1" * 64)
    child_id = TaskIdentity(digest="2" * 64)
    expected_map = {"shared.base": upstream_id, "scene.build": child_id}
    good_child = _record(
        "scene.build",
        child_id,
        outputs=(_artifact("scene.build/out"),),
        upstream_identities=(upstream_id,),
    )
    bad_child = _record(
        "scene.build",
        child_id,
        outputs=(_artifact("scene.build/out"),),
        upstream_identities=(TaskIdentity(digest="9" * 64),),
    )
    good_upstream = _record(
        "shared.base",
        upstream_id,
        outputs=(_artifact("shared.base/out"),),
    )
    accepted = ExecutionFrontier.verify(
        dep_graph,
        {"shared.base": good_upstream, "scene.build": good_child},
        expected_map,
        context,
        verifier,
    )
    assert accepted.task_names == frozenset({"shared.base", "scene.build"})

    rejected_upstream = ExecutionFrontier.verify(
        dep_graph,
        {"shared.base": good_upstream, "scene.build": bad_child},
        expected_map,
        context,
        verifier,
    )
    assert "scene.build" not in rejected_upstream.task_names
    assert "shared.base" in rejected_upstream.task_names


class _RecordingVerifier:
    """可配置的 Blob 完整性 verifier 替身。

    职责：
        按 ``BlobRef.sha256`` 决定是否通过；记录被校验的产物以便断言。
    """

    def __init__(self, *, failing_sha256: frozenset[str] = frozenset()) -> None:
        """初始化失败集合。

        参数：
            failing_sha256: 应判失败的 Blob SHA256 集合。
        """
        self._failing_sha256 = failing_sha256
        self.checked: list[LogicalArtifact] = []

    def verify_blob(self, artifact: LogicalArtifact) -> bool:
        """按配置校验产物 Blob。

        参数：
            artifact: 待校验逻辑产物。

        返回：
            SHA256 不在失败集合中时为 ``True``。
        """
        self.checked.append(artifact)
        return artifact.blob.sha256 not in self._failing_sha256


@pytest.mark.parametrize(
    "case",
    [
        "missing_path",
        "undeclared_path",
        "duplicate_path",
        "blob_integrity",
    ],
)
def test_frontier_rejects_output_path_or_blob_integrity_mismatch(case: str) -> None:
    """验证 Frontier 拒绝输出路径集合或 Blob 完整性失配的节点。

    参数：
        case: 参数化场景名——缺失路径、未声明路径、重复路径或 Blob 哈希失败。

    测试断言：先从 ``BuildGraph.plan_of(task).spec.outputs`` 取得 expected
    paths；实际路径缺失、未声明或重复时节点不可复用；路径严格相等后，再对
    每个完整 ``LogicalArtifact`` 校验 locator 存在、实际 SHA256 与大小。

    当前一步最小 GREEN 固定接受 verifier 且未比较 TaskPlan outputs 时，路径
    或哈希不匹配的节点仍进入集合，参数化断言确定失败。除导入与内存构造外
    不产生外部副作用。
    """
    plan = _plan(
        "scene.build",
        outputs=frozenset({"scene/a.assetbundle", "scene/b.assetbundle"}),
    )
    graph = BuildGraph.from_plans((plan,))
    identity = TaskIdentity(digest="s" * 64)
    expected = {"scene.build": identity}
    context = _resume_context()

    art_a = _artifact("scene/a.assetbundle", sha256=_VALID_SHA256_A)
    art_b = _artifact("scene/b.assetbundle", sha256=_VALID_SHA256_B)

    if case == "missing_path":
        outputs: tuple[LogicalArtifact, ...] = (art_a,)
        verifier: BlobHashVerifier = _AcceptAllVerifier()
    elif case == "undeclared_path":
        outputs = (
            art_a,
            art_b,
            _artifact("scene/extra.assetbundle", sha256="c" * 64),
        )
        verifier = _AcceptAllVerifier()
    elif case == "duplicate_path":
        outputs = (art_a, art_b, _artifact("scene/a.assetbundle", sha256="d" * 64))
        verifier = _AcceptAllVerifier()
    else:
        outputs = (art_a, art_b)
        verifier = _RecordingVerifier(failing_sha256=frozenset({_VALID_SHA256_B}))

    record = _record("scene.build", identity, outputs=outputs)
    verified = ExecutionFrontier.verify(
        graph,
        {"scene.build": record},
        expected,
        context,
        verifier,
    )
    assert "scene.build" not in verified.task_names
    assert "scene.build" not in verified.outputs

    # 对照：路径完整且 Blob 全部通过时节点可复用。
    if case == "blob_integrity":
        ok_verifier = _AcceptAllVerifier()
        ok = ExecutionFrontier.verify(
            graph,
            {"scene.build": _record("scene.build", identity, outputs=(art_a, art_b))},
            expected,
            context,
            ok_verifier,
        )
        assert "scene.build" in ok.task_names
        assert ok.outputs["scene.build"] == (art_a, art_b)


def test_frontier_keeps_all_verified_completed_nodes_and_invalidates_descendants() -> None:
    """验证 Frontier 返回全部已验证节点并沿 DAG 使下游失效。

    测试无参数和返回值。断言：返回值是不可变 ``VerifiedFrontier``；多个独立
    已验证节点均可复用；任一上游无效会使其所有下游不可复用，但不影响独立
    分支。

    当前一步最小 GREEN 逐节点独立验证但尚未沿 DAG 传播无效性时，“上游坏、
    下游记录自身正确”的输入仍错误保留下游，断言确定失败。除导入与内存构造
    外不产生外部副作用。
    """
    # 拓扑：shared.base → scene.build；independent.leaf 为无关分支。
    shared = _plan("shared.base")
    scene = _plan("scene.build", ("shared.base",))
    independent = _plan("independent.leaf")
    graph = BuildGraph.from_plans((shared, scene, independent))

    shared_id = TaskIdentity(digest="1" * 64)
    scene_id = TaskIdentity(digest="2" * 64)
    independent_id = TaskIdentity(digest="3" * 64)
    expected = {
        "shared.base": shared_id,
        "scene.build": scene_id,
        "independent.leaf": independent_id,
    }
    context = _resume_context()
    verifier = _AcceptAllVerifier()

    # 上游 shared.base 身份故意失配 → 自身与下游 scene 均不可复用。
    bad_shared = _record(
        "shared.base",
        TaskIdentity(digest="9" * 64),
        outputs=(_artifact("shared.base/out"),),
    )
    # 下游记录自身身份、上下文、路径与 Blob 均正确。
    good_scene = _record(
        "scene.build",
        scene_id,
        outputs=(_artifact("scene.build/out"),),
        upstream_identities=(shared_id,),
    )
    good_independent = _record(
        "independent.leaf",
        independent_id,
        outputs=(_artifact("independent.leaf/out"),),
    )

    verified = ExecutionFrontier.verify(
        graph,
        {
            "shared.base": bad_shared,
            "scene.build": good_scene,
            "independent.leaf": good_independent,
        },
        expected,
        context,
        verifier,
    )

    assert isinstance(verified, VerifiedFrontier)
    assert "shared.base" not in verified.task_names
    # 上游无效时下游即使记录自身正确也不可复用。
    assert "scene.build" not in verified.task_names
    assert "scene.build" not in verified.outputs
    # 独立分支不受影响。
    assert "independent.leaf" in verified.task_names
    assert verified.outputs["independent.leaf"] == (_artifact("independent.leaf/out"),)

    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        verified.task_names = frozenset()  # type: ignore[misc]

    # 全部上游有效时，多个已验证节点均可复用。
    good_shared = _record(
        "shared.base",
        shared_id,
        outputs=(_artifact("shared.base/out"),),
    )
    all_good = ExecutionFrontier.verify(
        graph,
        {
            "shared.base": good_shared,
            "scene.build": good_scene,
            "independent.leaf": good_independent,
        },
        expected,
        context,
        verifier,
    )
    assert all_good.task_names == frozenset({"shared.base", "scene.build", "independent.leaf"})
    assert set(all_good.outputs) == {
        "shared.base",
        "scene.build",
        "independent.leaf",
    }
