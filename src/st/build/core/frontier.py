"""可验证恢复 Frontier：完成证据与执行边界校验。

本模块定义恢复用的 ``ResumeContext``、``CompletedTaskRecord``、
``BlobHashVerifier``，以及 ``ExecutionFrontier.verify``：将已完成记录与当前
规划 expected identities、恢复上下文、输出路径集合与 Blob 完整性比较，再按
拓扑层传播有效性，产出不可变 ``VerifiedFrontier``。完成记录必须保存完整
``LogicalArtifact`` 元组与全部身份字段，禁止退化为 BlobRef 集合或布尔
completed。导入本模块不执行构建，也不访问 SVN、Unity、Jenkins 或 CDN。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from st.build.core.artifacts import LogicalArtifact
from st.build.core.graph import BuildGraph
from st.build.core.tasks import TaskIdentity


@dataclass(frozen=True, slots=True)
class ResumeContext:
    """恢复校验用的不可变构建上下文镜像。

    职责：
        携带与 ``BuildContext`` 对齐的请求摘要、固定 revision、工具链摘要、
        可选基线与 schema 版本，供 Frontier 与已完成任务记录逐字段比较。

    参数：
        request_digest: 构建请求的确定性摘要。
        revision: 固定源码或输入快照 revision，不得为浮动 HEAD 语义。
        toolchain_digest: 工具链配置摘要。
        baseline_id: 可选基线标识；全量构建可为 ``None``。
        schema_version: 身份/规划 schema 整数版本。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        无；非法上下文由上层在进入领域前拒绝。

    约束与副作用：
        ``frozen=True, slots=True``；不读写磁盘，无外部副作用。
    """

    request_digest: str
    revision: str
    toolchain_digest: str
    baseline_id: str | None
    schema_version: int


@dataclass(frozen=True, slots=True)
class CompletedTaskRecord:
    """已完成任务的不可变恢复证据。

    职责：
        保存任务名、任务身份、完整输出产物元组，以及 request、revision、
        toolchain、baseline、schema 与上游身份，供 Frontier 与当前规划比较。

    参数：
        task_name: 任务逻辑名。
        task_identity: 完成时固化的 ``TaskIdentity``。
        outputs: 完整 ``tuple[LogicalArtifact, ...]``；须保留路径、kind、Blob、
            依赖、分包与 metadata，禁止退化为 BlobRef 集合。
        request_digest: 完成时请求摘要。
        revision: 完成时固定 revision。
        toolchain_digest: 完成时工具链摘要。
        baseline_id: 完成时基线标识；可为 ``None``。
        schema_version: 完成时 schema 版本。
        upstream_identities: 有序上游 ``TaskIdentity`` 元组。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        无；字段合法性由写入方保证。

    约束与副作用：
        ``frozen=True, slots=True``；不得仅保存最后任务名或布尔 completed。
        无 I/O，无外部副作用。
    """

    task_name: str
    task_identity: TaskIdentity
    outputs: tuple[LogicalArtifact, ...]
    request_digest: str
    revision: str
    toolchain_digest: str
    baseline_id: str | None
    schema_version: int
    upstream_identities: tuple[TaskIdentity, ...]


@dataclass(frozen=True, slots=True)
class VerifiedFrontier:
    """通过身份与恢复上下文校验的不可变执行边界。

    职责：
        承载可复用任务名集合，以及任务名到完整 ``LogicalArtifact`` 元组的
        不可变映射；供后续 Executor 仅接受本类型作为恢复输入。

    参数：
        task_names: 已验证可复用任务名 ``frozenset``。
        outputs: 任务名到完整产物元组的只读映射。

    返回：
        无；本类为不可变数据载体。

    异常：
        无。

    约束与副作用：
        ``frozen=True, slots=True``；``outputs`` 使用 ``MappingProxyType``。
        无 I/O。
    """

    task_names: frozenset[str]
    outputs: Mapping[str, tuple[LogicalArtifact, ...]]


@runtime_checkable
class BlobHashVerifier(Protocol):
    """校验逻辑产物 Blob 定位存在且哈希/大小一致的协议。

    职责：
        由调用方注入；对每个 ``LogicalArtifact`` 判断 locator 是否存在，以及
        实际 SHA256 / 大小是否与 ``BlobRef`` 声明一致。

    参数：
        ``verify_blob`` 接收完整 ``LogicalArtifact``。

    返回：
        ``verify_blob`` 在定位存在且哈希、大小均匹配时返回 ``True``。

    异常：
        由具体实现定义；协议本身不规定异常层次。

    约束与副作用：
        领域层不绑定具体 CAS 实现；副作用仅限只读校验。
    """

    def verify_blob(self, artifact: LogicalArtifact) -> bool:
        """校验产物 Blob 的定位、哈希与大小。

        参数：
            artifact: 含 ``BlobRef`` 的完整逻辑产物。

        返回：
            locator 存在且实际 SHA256、大小与声明一致时为 ``True``，否则
            ``False``。

        异常：
            由实现定义。

        约束与副作用：
            只读校验；不得修改产物或写盘。
        """
        ...


def _matches_resume_context(
    record: CompletedTaskRecord,
    context: ResumeContext,
) -> bool:
    """比较完成记录与恢复上下文的身份相关字段。

    参数：
        record: 已完成任务记录。
        context: 当前恢复上下文。

    返回：
        request、revision、toolchain、baseline、schema 全部相等时为 ``True``。

    异常：
        无。

    约束与副作用：
        纯函数；无 I/O。
    """
    return (
        record.request_digest == context.request_digest
        and record.revision == context.revision
        and record.toolchain_digest == context.toolchain_digest
        and record.baseline_id == context.baseline_id
        and record.schema_version == context.schema_version
    )


def _expected_upstream_identities(
    graph: BuildGraph,
    task_name: str,
    expected_identities: Mapping[str, TaskIdentity],
) -> tuple[TaskIdentity, ...] | None:
    """按计划依赖顺序组装 expected 上游身份。

    参数：
        graph: 当前构建图。
        task_name: 任务名。
        expected_identities: 规划期 expected identity 映射。

    返回：
        按 ``spec.dependencies`` 顺序排列的上游身份元组；任一上游缺少
        expected identity 时返回 ``None``。

    异常：
        任务不在图中时抛出 ``KeyError``。

    约束与副作用：
        纯函数；保序，不排序依赖。
    """
    plan = graph.plan_of(task_name)
    upstream: list[TaskIdentity] = []
    for dep_name in plan.spec.dependencies:
        identity = expected_identities.get(dep_name)
        if identity is None:
            return None
        upstream.append(identity)
    return tuple(upstream)


def _outputs_match_plan_and_blobs(
    graph: BuildGraph,
    task_name: str,
    outputs: tuple[LogicalArtifact, ...],
    verifier: BlobHashVerifier,
) -> bool:
    """校验实际输出路径集合与每个 Blob 的完整性。

    参数：
        graph: 当前构建图，用于读取 ``TaskPlan.spec.outputs``。
        task_name: 任务名。
        outputs: 完成记录中的产物元组。
        verifier: Blob 完整性校验器。

    返回：
        无重复路径、实际路径集合与 expected 严格相等，且每个产物
        ``verifier.verify_blob`` 通过时为 ``True``；否则 ``False``。

    异常：
        任务不在图中时抛出 ``KeyError``。

    约束与副作用：
        先拒绝重复路径并比较路径集合，再逐产物调用 verifier；只读。
    """
    expected_paths = graph.plan_of(task_name).spec.outputs
    actual_paths = [artifact.logical_path for artifact in outputs]
    # 重复路径会使集合比较掩盖冲突，必须先单独拒绝。
    if len(actual_paths) != len(set(actual_paths)):
        return False
    if set(actual_paths) != set(expected_paths):
        return False
    for artifact in outputs:
        if not verifier.verify_blob(artifact):
            return False
    return True


class ExecutionFrontier:
    """从完成记录与当前规划计算可复用执行边界。

    职责：
        ``verify`` 逐节点比较 expected identity、恢复上下文、输出路径集合与
        Blob 完整性，再按拓扑层传播有效性，产出不可变 ``VerifiedFrontier``。
        调用方必须显式传入 ``PlannedBuild.expected_identities``。

    参数：
        无构造状态；通过 ``verify`` 静态方法接收全部输入。

    返回：
        ``verify`` 返回 ``VerifiedFrontier``。

    异常：
        无；不匹配或上游无效的节点静默排除，不抛业务异常。

    约束与副作用：
        纯领域逻辑；按拓扑传播，不递归重启完整流水线。无 I/O。
    """

    @staticmethod
    def verify(
        graph: BuildGraph,
        records: Mapping[str, CompletedTaskRecord],
        expected_identities: Mapping[str, TaskIdentity],
        context: ResumeContext,
        verifier: BlobHashVerifier,
    ) -> VerifiedFrontier:
        """校验完成记录相对当前规划与恢复上下文的可复用边界。

        参数：
            graph: 当前 ``BuildGraph``。
            records: 任务名到 ``CompletedTaskRecord`` 的映射。
            expected_identities: 调用方显式传入的规划 expected identity 映射。
            context: 恢复上下文。
            verifier: Blob 完整性校验器。

        返回：
            ``VerifiedFrontier``：``task_names`` 为可复用名集合，``outputs`` 为
            任务名到完整已验证 ``LogicalArtifact`` 元组的不可变映射。

        异常：
            无；缺 expected、identity/上下文/upstream 失配、路径集合或 Blob
            完整性失败、或任一上游未通过时该节点不进入结果。

        约束与副作用：
            先做节点局部校验，再按拓扑层要求全部上游已进入边界；独立分支
            互不影响。不递归重启流水线。
        """
        locally_valid: dict[str, tuple[LogicalArtifact, ...]] = {}

        for task_name, record in records.items():
            expected = expected_identities.get(task_name)
            # 缺少 expected identity 的节点不可复用。
            if expected is None:
                continue
            if record.task_identity != expected:
                continue
            if not _matches_resume_context(record, context):
                continue
            expected_upstream = _expected_upstream_identities(graph, task_name, expected_identities)
            if expected_upstream is None:
                continue
            if record.upstream_identities != expected_upstream:
                continue
            if not _outputs_match_plan_and_blobs(graph, task_name, record.outputs, verifier):
                continue
            locally_valid[task_name] = record.outputs

        # 按拓扑层传播：仅当全部上游已进入边界时才接纳本节点。
        accepted_names: set[str] = set()
        pending = set(locally_valid)
        while pending:
            newly = {name for name in pending if graph.dependencies_of(name) <= accepted_names}
            if not newly:
                # 剩余节点必有上游未通过局部校验，停止传播。
                break
            accepted_names |= newly
            pending -= newly

        accepted_outputs = {name: locally_valid[name] for name in accepted_names}
        return VerifiedFrontier(
            task_names=frozenset(accepted_names),
            outputs=MappingProxyType(accepted_outputs),
        )
