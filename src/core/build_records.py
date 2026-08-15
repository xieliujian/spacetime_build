"""构建清单可复现 payload、不可变 BuildManifest 与执行运行态记录的领域模型。

本模块提供不可变的 ``BuildManifestPayload``、``BuildManifest`` 与
``BuildExecutionRecord``：payload 只表达可复现内容；``BuildManifest`` 由工厂
绑定 payload 与由其规范字节计算出的 ``manifest_id``；执行记录单独承载唯一
``build_id``、状态、时间与日志定位。未知执行记录 schema 必须拒绝。公开创建
``BuildManifest`` 必须经 ``BuildManifestFactory``，直接构造抛出 ``TypeError``。
导入本模块不执行构建，也不访问 SVN、Unity、Jenkins 或 CDN。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import cast

from core.artifacts import LogicalArtifact
from core.errors import ArtifactValidationError

# 当前受支持的执行记录 schema；未知版本一律拒绝，避免静默误读旧/新格式。
BUILD_EXECUTION_SCHEMA_VERSION = 1

# 仅 bind_build_manifest 持有；公开调用方无法合法传入该 token。
_BUILD_MANIFEST_FACTORY_TOKEN = object()


class BuildStatus(Enum):
    """构建执行生命周期状态。

    职责：
        区分排队、进行中与终态结果，仅供 ``BuildExecutionRecord`` 使用；不得写入
        ``BuildManifestPayload``。

    参数：
        枚举成员无额外构造参数；值为稳定字符串标签。

    返回：
        无；通过 ``BuildStatus.<NAME>`` 取值。

    异常：
        无；非法名称访问由 ``Enum`` 标准机制报错。

    约束与副作用：
        仅描述运行态；不读写磁盘，无外部副作用。
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BuildManifestPayload:
    """仅含可复现内容的构建清单 payload。

    职责：
        记录 schema、请求摘要、源码 revision、工具链摘要、可选基线 ID、逻辑产物
        元组与任务身份元组；供后续工厂计算 ``manifest_id``。不包含运行状态、
        唯一执行 ID、时间、耗时或日志定位。

    参数：
        schema_version: payload schema 整数版本。
        request_digest: 构建请求的确定性摘要字符串。
        revision: 源码或输入快照 revision 标识。
        toolchain_digest: 工具链配置摘要。
        baseline_id: 可选基线发布标识；全量构建可为 ``None``。
        artifacts: 逻辑产物元组；须为 ``tuple[LogicalArtifact, ...]``。
        task_identities: 任务身份字符串元组；须为 ``tuple[str, ...]``。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        ``artifacts`` 或 ``task_identities`` 类型不合法时，抛出
        ``ArtifactValidationError``。

    约束与副作用：
        ``frozen=True, slots=True``；字段名刻意排除 ``manifest_id`` / ``build_id``
        / 状态与时间字段。不计算 ID，不读写磁盘，无外部副作用。
    """

    schema_version: int
    request_digest: str
    revision: str
    toolchain_digest: str
    baseline_id: str | None
    artifacts: tuple[LogicalArtifact, ...]
    task_identities: tuple[str, ...]

    def __post_init__(self) -> None:
        """构造后校验产物与任务身份元组不变量。

        参数：
            无；读取 ``self.artifacts`` 与 ``self.task_identities``。

        返回：
            ``None``。

        异常：
            ``artifacts`` 非 ``LogicalArtifact`` 元组，或 ``task_identities`` 非
            ``str`` 元组时，抛出 ``ArtifactValidationError``。

        约束与副作用：
            仅内存校验；不计算 manifest ID，不持久化。
        """
        # cast 擦除静态类型，保留防御性运行时校验（非法调用仍可在构造期失败）。
        artifacts = cast(object, self.artifacts)
        if not isinstance(artifacts, tuple):
            raise ArtifactValidationError("artifacts 必须是 tuple[LogicalArtifact, ...]")
        for item in cast(tuple[object, ...], artifacts):
            if not isinstance(item, LogicalArtifact):
                raise ArtifactValidationError("artifacts 的每一项必须是 LogicalArtifact")

        identities = cast(object, self.task_identities)
        if not isinstance(identities, tuple):
            raise ArtifactValidationError("task_identities 必须是 tuple[str, ...]")
        for identity in cast(tuple[object, ...], identities):
            if not isinstance(identity, str):
                raise ArtifactValidationError("task_identities 的每一项必须是 str")


@dataclass(frozen=True, slots=True)
class BuildManifest:
    """绑定可复现 payload 与内容寻址 ``manifest_id`` 的不可变清单。

    职责：
        作为构建产物的稳定身份载体：``manifest_id`` 必须等于 payload 规范 JSON
        字节的 SHA256，结构上不把 ID 自身编入哈希输入。公开调用方不得直接构造。

    参数：
        manifest_id: 64 位小写十六进制 SHA256；仅由工厂根据 payload 计算写入。
        payload: 可复现 ``BuildManifestPayload``。
        _factory_token: 模块私有工厂令牌；公开调用不得传入合法值。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        直接公开构造（缺少合法 ``_factory_token``）时抛出 ``TypeError``。

    约束与副作用：
        ``frozen=True, slots=True``；创建只允许经工厂；不读写磁盘，无外部副作用。
    """

    manifest_id: str
    payload: BuildManifestPayload
    _factory_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """拒绝非工厂构造，保证 ID 只能由工厂写入。

        参数：
            无；读取 ``self._factory_token``。

        返回：
            ``None``。

        异常：
            ``_factory_token`` 不是模块私有令牌时抛出 ``TypeError``。

        约束与副作用：
            仅内存门禁；不校验 ID 格式（由工厂保证）。
        """
        if self._factory_token is not _BUILD_MANIFEST_FACTORY_TOKEN:
            raise TypeError(
                "BuildManifest 只能通过 BuildManifestFactory.create(payload) 创建，"
                "禁止直接构造或传入自备 manifest_id"
            )


def bind_build_manifest(*, manifest_id: str, payload: BuildManifestPayload) -> BuildManifest:
    """将已计算的 ``manifest_id`` 与 payload 绑定为不可变 ``BuildManifest``。

    参数：
        manifest_id: 由工厂根据 payload 规范字节计算出的 64 位 SHA256。
        payload: 已校验的可复现 ``BuildManifestPayload``。

    返回：
        绑定 ID 与 payload 的不可变 ``BuildManifest``。

    异常：
        无；调用方保证 ID 已正确计算。

    约束与副作用：
        仅供 ``BuildManifestFactory`` 与编解码器使用；公开调用方应通过工厂创建，
        不得自行传入任意 ID。纯内存构造，无 I/O。
    """
    return BuildManifest(
        manifest_id=manifest_id,
        payload=payload,
        _factory_token=_BUILD_MANIFEST_FACTORY_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class BuildExecutionRecord:
    """承载单次构建运行状态的不可变执行记录。

    职责：
        用唯一 ``build_id``、可选 ``manifest_id``、``BuildStatus``、起止时间与可选
        日志定位表达一次构建的运行态；与可复现 payload 职责分离。

    参数：
        schema_version: 必须等于 ``BUILD_EXECUTION_SCHEMA_VERSION``。
        build_id: 本次执行的唯一标识。
        manifest_id: 完成后关联的 manifest ID；进行中可为 ``None``。
        status: ``BuildStatus`` 运行状态。
        started_at: 开始时间（建议时区感知 ``datetime``）。
        finished_at: 结束时间；进行中可为 ``None``；若存在不得早于
            ``started_at``。
        log_locator: 可选日志定位串；无日志时可为 ``None``。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        schema 未知，或 ``finished_at`` 早于 ``started_at`` 时，抛出
        ``ArtifactValidationError``。

    约束与副作用：
        ``frozen=True, slots=True``；不实现持久化，不读写磁盘，无外部副作用。
    """

    schema_version: int
    build_id: str
    manifest_id: str | None
    status: BuildStatus
    started_at: datetime
    finished_at: datetime | None
    log_locator: str | None

    def __post_init__(self) -> None:
        """构造后校验 schema 与起止时间关系。

        参数：
            无；读取实例字段。

        返回：
            ``None``。

        异常：
            ``schema_version`` 不等于受支持常量，或结束时间早于开始时间时，抛出
            ``ArtifactValidationError``。

        约束与副作用：
            仅内存校验；不持久化执行记录。
        """
        # 未知 schema 必须硬失败，防止旧/新格式被静默当成当前契约使用。
        if self.schema_version != BUILD_EXECUTION_SCHEMA_VERSION:
            raise ArtifactValidationError(
                "BuildExecutionRecord schema_version 不受支持: "
                f"{self.schema_version!r}，当前仅支持 "
                f"{BUILD_EXECUTION_SCHEMA_VERSION}"
            )

        finished = self.finished_at
        if finished is not None and finished < self.started_at:
            raise ArtifactValidationError("finished_at 不得早于 started_at")
