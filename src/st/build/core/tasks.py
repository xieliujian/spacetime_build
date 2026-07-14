"""任务规格、规划、协议、身份与执行输入输出模型。

本模块定义领域层唯一任务规划契约：``TaskSpec`` 声明依赖与输出，
``BuildTask.plan`` 产出完整不可变 ``TaskPlan``，``execute`` 消费
``ArtifactCollection`` 并返回 tuple 形式的 ``TaskResult.outputs``。
``TaskIdentity`` 只能通过 ``from_plan`` 从完整 ``TaskPlan`` 生成，禁止
``from_spec`` 双路径。协议刻意不包含 SVN、Unity、Jenkins 或上传方法；
外部副作用只允许出现在集成与发布阶段。导入本模块不执行构建，也不访问
外部系统。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from st.build.core.artifacts import LogicalArtifact
from st.build.core.manifest_codec import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """任务声明的不可变规格。

    职责：
        保存任务名、有序上游依赖、无序输出逻辑路径、实现版本与执行属性；
        供 ``TaskPlan`` 引用，并作为执行器校验实际输出路径集合的期望来源。

    参数：
        name: 任务逻辑名，图内唯一标识。
        dependencies: 有序上游任务名元组；保留声明顺序与重复。
        outputs: 无序输出逻辑路径集合，类型为 ``frozenset[str]``。
        implementation_version: 任务实现版本字符串，参与身份摘要。
        execution_attributes: 可稳定编码的执行属性对元组，如是否可缓存/并行。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        无强制校验；非法字段由调用方或上层规划阶段拒绝。

    约束与副作用：
        ``frozen=True, slots=True``；dependencies 保序保重复，outputs 使用
        frozenset。无 I/O，无外部副作用。
    """

    name: str
    dependencies: tuple[str, ...]
    outputs: frozenset[str]
    implementation_version: str
    execution_attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class TaskPlan:
    """应用层规划完成后的完整不可变任务计划。

    职责：
        绑定 ``TaskSpec`` 与解析后的输入摘要、配置摘要；作为 ``BuildPlanner``
        与 ``TaskIdentity.from_plan`` 的唯一输入形态。

    参数：
        spec: 本计划引用的 ``TaskSpec``；规划后不得替换为另一份规格。
        resolved_input_digest: 已解析输入（文件/上游产物）的确定性摘要。
        config_digest: 任务相关配置的确定性摘要。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        无；字段合法性由应用层 ``plan`` 保证。

    约束与副作用：
        ``frozen=True, slots=True``；必须先有完整 ``TaskPlan`` 才能生成身份。
        无 I/O，无外部副作用。
    """

    spec: TaskSpec
    resolved_input_digest: str
    config_digest: str


@dataclass(frozen=True, slots=True)
class BuildContext:
    """任务规划与执行共享的不可变构建上下文。

    职责：
        携带请求摘要、固定 revision、工具链摘要、可选基线与 schema 版本，
        供 ``plan`` / ``execute`` 与后续 ``TaskIdentity`` 计算复用。

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
class ArtifactCollection:
    """按逻辑路径索引的不可变产物集合。

    职责：
        作为 ``BuildTask.execute`` 的输入，提供映射式只读访问；内部以
        ``tuple`` 冻结条目顺序，避免执行期篡改。

    参数：
        通过 ``from_artifacts`` 工厂从 ``LogicalArtifact`` 序列构造；重复路径
        以后出现者覆盖前者（与映射语义一致）。

    返回：
        无；通过 ``[]`` / 迭代 / ``len`` 读取。

    异常：
        访问不存在路径时抛出 ``KeyError``。

    约束与副作用：
        ``frozen=True, slots=True``；构造后不可变。无 I/O。
    """

    _items: tuple[tuple[str, LogicalArtifact], ...]

    @classmethod
    def from_artifacts(
        cls,
        artifacts: Iterable[LogicalArtifact],
    ) -> ArtifactCollection:
        """由产物序列构造不可变集合。

        参数：
            artifacts: ``LogicalArtifact`` 可迭代对象；允许为空。

        返回：
            以 ``logical_path`` 为键的 ``ArtifactCollection``。

        异常：
            无；重复路径时后者覆盖前者。

        约束与副作用：
            纯函数；不修改入参，不访问文件系统。
        """
        mapping: dict[str, LogicalArtifact] = {}
        for artifact in artifacts:
            mapping[artifact.logical_path] = artifact
        return cls(_items=tuple(mapping.items()))

    def __getitem__(self, logical_path: str) -> LogicalArtifact:
        """按逻辑路径取产物。

        参数：
            logical_path: 客户端逻辑路径键。

        返回：
            对应的 ``LogicalArtifact``。

        异常：
            路径不存在时抛出 ``KeyError``。

        约束与副作用：
            只读；无副作用。
        """
        for path, artifact in self._items:
            if path == logical_path:
                return artifact
        raise KeyError(logical_path)

    def __iter__(self) -> Iterator[str]:
        """按构造顺序迭代逻辑路径键。

        返回：
            逻辑路径字符串迭代器。

        异常：
            无。

        约束与副作用：
            只读；无副作用。
        """
        return (path for path, _ in self._items)

    def __len__(self) -> int:
        """返回集合中产物数量。

        返回：
            非负整数长度。

        异常：
            无。

        约束与副作用：
            只读；无副作用。
        """
        return len(self._items)

    def __contains__(self, logical_path: object) -> bool:
        """判断逻辑路径是否存在于集合中。

        参数：
            logical_path: 待查询键；非 ``str`` 时返回 ``False``。

        返回：
            存在则为 ``True``，否则 ``False``。

        异常：
            无。

        约束与副作用：
            只读；无副作用。
        """
        if not isinstance(logical_path, str):
            return False
        return any(path == logical_path for path, _ in self._items)

    def get(
        self,
        logical_path: str,
        default: LogicalArtifact | None = None,
    ) -> LogicalArtifact | None:
        """按路径安全获取产物。

        参数：
            logical_path: 逻辑路径键。
            default: 缺失时返回值，默认为 ``None``。

        返回：
            找到的 ``LogicalArtifact`` 或 ``default``。

        异常：
            无。

        约束与副作用：
            只读；无副作用。
        """
        try:
            return self[logical_path]
        except KeyError:
            return default

    def as_mapping(self) -> Mapping[str, LogicalArtifact]:
        """返回路径到产物的只读映射视图。

        返回：
            ``dict`` 快照，调用方修改不影响本集合。

        异常：
            无。

        约束与副作用：
            每次调用新建 ``dict``；不暴露可变内部状态。
        """
        return dict(self._items)


@dataclass(frozen=True, slots=True)
class TaskResult:
    """任务执行结果。

    职责：
        承载执行产出的逻辑产物序列；``outputs`` 使用 ``tuple`` 以便执行器
        检测重复逻辑路径。

    参数：
        outputs: ``tuple[LogicalArtifact, ...]``；允许含重复路径以便上层拒绝。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        无；路径集合与声明 ``TaskSpec.outputs`` 的比较由执行器负责。

    约束与副作用：
        ``frozen=True, slots=True``；不登记产物、不写磁盘。
    """

    outputs: tuple[LogicalArtifact, ...]


def _task_identity_payload(
    plan: TaskPlan,
    context: BuildContext,
    upstream_identities: tuple[TaskIdentity, ...],
) -> dict[str, object]:
    """将 TaskPlan、上下文与上游身份转为可规范编码的字典。

    参数：
        plan: 完整不可变 ``TaskPlan``。
        context: 共享 ``BuildContext``。
        upstream_identities: 有序上游 ``TaskIdentity`` 元组；保留顺序。

    返回：
        供 ``canonical_json_bytes`` 编码的字典；``outputs`` 按 UTF-8 字节序排序。

    异常：
        无；调用方保证领域对象合法。

    约束与副作用：
        纯函数；不缓存；dependencies 与 upstream 保序，仅无序 outputs 排序。
    """
    spec = plan.spec
    return {
        "baseline_id": context.baseline_id,
        "config_digest": plan.config_digest,
        "request_digest": context.request_digest,
        "resolved_input_digest": plan.resolved_input_digest,
        "revision": context.revision,
        "schema_version": context.schema_version,
        "spec": {
            "dependencies": list(spec.dependencies),
            "execution_attributes": [list(pair) for pair in spec.execution_attributes],
            "implementation_version": spec.implementation_version,
            "name": spec.name,
            # 无序集合：按路径 UTF-8 字节序排序，保证身份确定性。
            "outputs": sorted(spec.outputs, key=lambda item: item.encode("utf-8")),
        },
        "toolchain_digest": context.toolchain_digest,
        "upstream_identities": [item.digest for item in upstream_identities],
    }


@dataclass(frozen=True, slots=True)
class TaskIdentity:
    """由完整 TaskPlan 派生的不可变任务身份。

    职责：
        用规范 JSON 与 SHA256 固化当前规划下任务的 expected identity，供
        Planner / Frontier 比较；只允许 ``from_plan``，禁止 ``from_spec``。

    参数：
        digest: 64 位小写十六进制身份摘要。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        ``from_plan`` 在编码失败时可能抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        ``frozen=True, slots=True``；无缓存；不读写磁盘。公共 API 不提供
        ``from_spec``。
    """

    digest: str

    @classmethod
    def from_plan(
        cls,
        plan: TaskPlan,
        context: BuildContext,
        upstream_identities: tuple[TaskIdentity, ...],
    ) -> TaskIdentity:
        """仅从完整 ``TaskPlan`` 与上下文生成任务身份。

        参数：
            plan: 应用层 ``BuildTask.plan`` 产出的完整计划。
            context: 含请求、revision、工具链、基线与 schema 的上下文。
            upstream_identities: 按依赖拓扑排列的上游身份元组。

        返回：
            摘要覆盖 plan.spec、resolved input/config digest、context 字段以及
            有序 upstream digests 的不可变 ``TaskIdentity``。

        异常：
            payload 无法 JSON 编码时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            使用 ``canonical_json_bytes`` 与 SHA256；不引入缓存；不提供
            ``from_spec`` 旁路。
        """
        payload = _task_identity_payload(plan, context, upstream_identities)
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return cls(digest=digest)


@runtime_checkable
class BuildTask(Protocol):
    """资源构建任务的统一协议。

    职责：
        声明任务名；``plan`` 产出完整 ``TaskPlan``；``execute`` 消费上下文与
        上游 ``ArtifactCollection`` 并返回 ``TaskResult``。不得包含 SVN、
        Unity、Jenkins、上传等集成副作用方法。

    参数：
        ``plan`` / ``execute`` 均接收 ``BuildContext``；``execute`` 另接收输入集合。

    返回：
        ``plan`` 返回 ``TaskPlan``；``execute`` 返回 ``TaskResult``。

    异常：
        由具体实现定义；协议本身不规定异常层次。

    约束与副作用：
        ``@runtime_checkable`` 仅检查方法存在性；领域层实现不得在协议方法中
        直接提交版本库、触发 CI 或上传 CDN。
    """

    @property
    def name(self) -> str:
        """返回任务逻辑名。

        返回：
            与 ``TaskSpec.name`` 对齐的非空逻辑名。

        异常：
            由实现定义。

        约束与副作用：
            只读属性；无外部副作用。
        """
        ...

    def plan(self, context: BuildContext) -> TaskPlan:
        """根据上下文解析输入与配置，生成完整不可变 ``TaskPlan``。

        参数：
            context: 共享构建上下文（请求、revision、工具链、基线、schema）。

        返回：
            引用本任务 ``TaskSpec`` 且含 resolved input/config digest 的计划。

        异常：
            由实现定义（如配置非法时）。

        约束与副作用：
            应保持确定性；不得执行 SVN/Unity/Jenkins/上传。
        """
        ...

    def execute(
        self,
        context: BuildContext,
        inputs: ArtifactCollection,
    ) -> TaskResult:
        """执行任务并返回产物结果。

        参数：
            context: 共享构建上下文。
            inputs: 上游逻辑产物集合。

        返回：
            ``outputs`` 为 ``tuple[LogicalArtifact, ...]`` 的 ``TaskResult``。

        异常：
            由实现定义。

        约束与副作用：
            不得在协议方法中提交 SVN、触发 Jenkins 或上传 CDN；产物登记由执行器完成。
        """
        ...
