"""独立发布激活记录、状态枚举与已验证 Bundle 门禁。

本模块提供不可变 ``ReleaseActivationRecord`` 与 ``ReleaseActivationStatus``，
将可变激活过程与内容寻址 ``ReleaseBundle`` 身份分离：记录只引用 ``bundle_id``，
不嵌入 Bundle 对象。``verify_release_bundle`` 在远端必要对象哈希全部匹配后签发
``VerifiedReleaseBundle``；``advance_activation`` 按白名单推进状态并返回新快照。
本模块不实现 CDN、CAS 或回滚。导入本模块不执行构建或发布。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum

from st.build.core.errors import PublishError
from st.build.core.manifest_codec import canonical_json_bytes
from st.build.release.bundles import ReleaseBundle
from st.build.release.entries import ReleaseObjectOrigin

# 当前受支持的激活记录 schema；未知版本一律拒绝。
RELEASE_ACTIVATION_SCHEMA_VERSION = 1

# 仅验证器 / 工厂持有；公开调用方无法合法传入该 token。
_VERIFIED_RELEASE_BUNDLE_TOKEN = object()


class ReleaseActivationStatus(Enum):
    """发布激活生命周期状态。

    职责：
        表达从准备到激活成功、失败或冲突的不可变状态标签；供
        ``ReleaseActivationRecord`` 使用，不得写入 ``ReleaseBundle``。

    参数：
        枚举成员无额外构造参数；值为稳定小写字符串标签。

    返回：
        无；通过 ``ReleaseActivationStatus.<NAME>`` 取值。

    异常：
        无；非法名称访问由 ``Enum`` 标准机制报错。

    约束与副作用：
        仅描述激活运行态；不读写磁盘，无 CDN/CAS 副作用。
    """

    PREPARING = "preparing"
    UPLOADING = "uploading"
    VERIFYING = "verifying"
    ACTIVATING = "activating"
    ACTIVE = "active"
    FAILED = "failed"
    CONFLICTED = "conflicted"


# 终态：成功、失败与冲突后不得再推进。
_TERMINAL_STATUSES = frozenset(
    {
        ReleaseActivationStatus.ACTIVE,
        ReleaseActivationStatus.FAILED,
        ReleaseActivationStatus.CONFLICTED,
    }
)

# 声明的合法迁移边；VERIFYING→ACTIVATING 另需 VerifiedReleaseBundle。
_ALLOWED_TRANSITIONS: dict[ReleaseActivationStatus, frozenset[ReleaseActivationStatus]] = {
    ReleaseActivationStatus.PREPARING: frozenset(
        {
            ReleaseActivationStatus.UPLOADING,
            ReleaseActivationStatus.FAILED,
        }
    ),
    ReleaseActivationStatus.UPLOADING: frozenset(
        {
            ReleaseActivationStatus.VERIFYING,
            ReleaseActivationStatus.FAILED,
        }
    ),
    ReleaseActivationStatus.VERIFYING: frozenset(
        {
            ReleaseActivationStatus.ACTIVATING,
            ReleaseActivationStatus.FAILED,
        }
    ),
    ReleaseActivationStatus.ACTIVATING: frozenset(
        {
            ReleaseActivationStatus.ACTIVE,
            ReleaseActivationStatus.CONFLICTED,
            ReleaseActivationStatus.FAILED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class VerifiedReleaseBundle:
    """远端必要对象哈希已全部匹配的已验证 Bundle 凭证。

    职责：
        作为 ``VERIFYING → ACTIVATING`` 迁移的不可伪造证据：公开调用方不得直接
        构造；只能由 ``verify_release_bundle`` 在 Bundle 全部必要对象远端哈希
        匹配后写入。

    参数：
        bundle_id: 已验证的内容寻址 ReleaseBundle ID。
        required_objects_digest: 必要对象集合的确定性摘要。
        verified_objects_digest: 远端校验后的对象摘要；应与必要对象摘要一致。
        _factory_token: 模块私有令牌；公开调用不得传入合法值。

    返回：
        无；本类为不可变凭证载体。

    异常：
        直接公开构造（缺少合法 ``_factory_token``）时抛出 ``TypeError``。

    约束与副作用：
        ``frozen=True, slots=True``；无 I/O；不实现 CDN 回读。
    """

    bundle_id: str
    required_objects_digest: str
    verified_objects_digest: str
    _factory_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """拒绝非验证器构造，防止伪造验证完成。

        参数：
            无；读取 ``self._factory_token``。

        返回：
            ``None``。

        异常：
            ``_factory_token`` 不是模块私有令牌时抛出 ``TypeError``。

        约束与副作用：
            仅内存门禁。
        """
        if self._factory_token is not _VERIFIED_RELEASE_BUNDLE_TOKEN:
            raise TypeError(
                "VerifiedReleaseBundle 只能由 verify_release_bundle 在远端哈希"
                "全部匹配后创建，禁止直接构造或伪造验证完成"
            )


@dataclass(frozen=True, slots=True)
class ReleaseActivationRecord:
    """承载单次发布激活运行状态的不可变记录。

    职责：
        用唯一 ``activation_id``、目标入口、期望代际与状态快照表达激活过程；
        只引用 ``bundle_id``，与可复现 ``ReleaseBundle`` 职责分离。创建或推进
        记录不得改写 Bundle 身份。

    参数：
        schema_version: 必须等于 ``RELEASE_ACTIVATION_SCHEMA_VERSION``。
        activation_id: 本次激活尝试的唯一标识。
        bundle_id: 待激活 ReleaseBundle 的内容寻址 ID。
        target: 发布目标入口名称（多目标各自独立记录状态）。
        expected_generation: CAS 期望的旧入口代际。
        status: ``ReleaseActivationStatus`` 当前状态快照。
        required_objects_digest: Bundle 必要对象集合的确定性摘要。
        verified_objects_digest: 远端校验通过后的对象摘要；未验证时为 ``None``。
        error: 失败原因；非失败状态通常为 ``None``。

    返回：
        无；本类为不可变数据载体。

    异常：
        ``schema_version`` 未知时抛出 ``PublishError``。

    约束与副作用：
        ``frozen=True, slots=True``；状态迁移由 ``advance_activation`` 执行；
        不实现 CDN、CAS 或回滚；无 I/O。
    """

    schema_version: int
    activation_id: str
    bundle_id: str
    target: str
    expected_generation: int
    status: ReleaseActivationStatus
    required_objects_digest: str
    verified_objects_digest: str | None
    error: str | None

    def __post_init__(self) -> None:
        """构造后校验 schema 与基本字段类型。

        参数：
            无；读取实例字段。

        返回：
            ``None``。

        异常：
            ``schema_version`` 不等于受支持常量时抛出 ``PublishError``。

        约束与副作用：
            仅内存校验；不持久化；不修改 ``bundle_id``。
        """
        # 未知 schema 必须硬失败，防止旧/新格式被静默当成当前契约使用。
        if self.schema_version != RELEASE_ACTIVATION_SCHEMA_VERSION:
            raise PublishError(
                "ReleaseActivationRecord schema_version 不受支持: "
                f"{self.schema_version!r}，当前仅支持 "
                f"{RELEASE_ACTIVATION_SCHEMA_VERSION}"
            )


def _required_transfer_sha256s(bundle: ReleaseBundle) -> tuple[str, ...]:
    """收集 Bundle 内本次新上传传输对象的稳定排序 SHA256 元组。

    参数：
        bundle: 已工厂创建的 ``ReleaseBundle``。

    返回：
        ``CURRENT_UPLOAD`` 条目 ``transfer_blob.sha256`` 按字典序去重排序后的元组。

    异常：
        无。

    约束与副作用：
        纯函数；历史对象不纳入必要上传校验集合。
    """
    sha_values: set[str] = set()
    for manifest in bundle.payload.manifests:
        for snapshot_entry in manifest.payload.snapshot.entries:
            entry = snapshot_entry.release_entry
            if entry.object_origin is ReleaseObjectOrigin.CURRENT_UPLOAD:
                sha_values.add(entry.transfer_blob.sha256)
    return tuple(sorted(sha_values))


def _objects_digest(sha256_values: tuple[str, ...]) -> str:
    """对必要对象 SHA256 集合计算确定性摘要。

    参数：
        sha256_values: 已稳定排序的传输对象哈希元组。

    返回：
        规范 JSON 字节的 SHA256 十六进制摘要。

    异常：
        无。

    约束与副作用：
        纯函数；使用 ``canonical_json_bytes`` 保证确定性。
    """
    return hashlib.sha256(canonical_json_bytes(list(sha256_values))).hexdigest()


def verify_release_bundle(
    bundle: ReleaseBundle,
    remote_objects: Mapping[str, str],
) -> VerifiedReleaseBundle:
    """在远端哈希匹配全部必要对象后签发 ``VerifiedReleaseBundle``。

    参数：
        bundle: 待验证的不可变 ``ReleaseBundle``。
        remote_objects: 对象 SHA256 → 远端观测哈希的映射；每个必要对象的值必须
            等于其自身 SHA256。

    返回：
        绑定 ``bundle_id`` 与必要/已验证对象摘要的 ``VerifiedReleaseBundle``。

    异常：
        必要对象缺失或远端哈希不匹配时抛出 ``PublishError``。

    约束与副作用：
        纯函数；不访问真实 CDN；只签发私有令牌凭证，调用方不得伪造。
    """
    required = _required_transfer_sha256s(bundle)
    digest = _objects_digest(required)

    for sha256 in required:
        if sha256 not in remote_objects:
            raise PublishError(f"远端缺少必要对象哈希: {sha256}")
        remote_hash = remote_objects[sha256]
        if remote_hash != sha256:
            raise PublishError(
                f"远端对象哈希与必要对象不匹配: expected={sha256!r}, remote={remote_hash!r}"
            )

    return VerifiedReleaseBundle(
        bundle_id=bundle.bundle_id,
        required_objects_digest=digest,
        verified_objects_digest=digest,
        _factory_token=_VERIFIED_RELEASE_BUNDLE_TOKEN,
    )


def advance_activation(
    record: ReleaseActivationRecord,
    next_status: ReleaseActivationStatus,
    *,
    verification: VerifiedReleaseBundle | None = None,
    error: str | None = None,
) -> ReleaseActivationRecord:
    """按声明白名单推进激活状态，返回新的不可变记录。

    参数：
        record: 当前激活记录快照；本函数不修改该实例。
        next_status: 目标状态。
        verification: ``VERIFYING→ACTIVATING`` 时必填的已验证 Bundle 凭证。
        error: 进入 ``FAILED`` / ``CONFLICTED`` 时必填的非空错误消息。

    返回：
        新的 ``ReleaseActivationRecord``；``bundle_id`` 与身份字段保持不变。

    异常：
        非法迁移、终态再推进、失败缺 error、验证凭证类型/身份不匹配时抛出
        ``PublishError``。

    约束与副作用：
        纯函数；不修改入参；不执行 CDN/CAS/回滚。
    """
    current = record.status
    if current in _TERMINAL_STATUSES:
        raise PublishError(f"终态 {current.value} 不能继续推进到 {next_status.value}")

    allowed = _ALLOWED_TRANSITIONS.get(current, frozenset())
    if next_status not in allowed:
        raise PublishError(f"不允许的激活状态迁移: {current.value} → {next_status.value}")

    # 失败与冲突都必须留下可诊断原因。
    if next_status in {
        ReleaseActivationStatus.FAILED,
        ReleaseActivationStatus.CONFLICTED,
    }:
        if not isinstance(error, str) or error == "":
            raise PublishError(f"进入 {next_status.value} 必须提供非空 error")

    new_verified_digest = record.verified_objects_digest
    if (
        current is ReleaseActivationStatus.VERIFYING
        and next_status is ReleaseActivationStatus.ACTIVATING
    ):
        if not isinstance(verification, VerifiedReleaseBundle):
            raise PublishError(
                "VERIFYING→ACTIVATING 必须提供 VerifiedReleaseBundle，禁止用普通集合伪造验证完成"
            )
        if verification.bundle_id != record.bundle_id:
            raise PublishError(
                "VerifiedReleaseBundle.bundle_id 与激活记录不一致: "
                f"record={record.bundle_id!r}, "
                f"verification={verification.bundle_id!r}"
            )
        if verification.required_objects_digest != record.required_objects_digest:
            raise PublishError("VerifiedReleaseBundle 必要对象摘要与激活记录不一致")
        if verification.verified_objects_digest != verification.required_objects_digest:
            raise PublishError("VerifiedReleaseBundle 已验证摘要必须等于必要对象摘要")
        new_verified_digest = verification.verified_objects_digest
    elif verification is not None:
        raise PublishError("仅 VERIFYING→ACTIVATING 允许传入 verification")

    # replace 生成新快照；bundle_id 等身份字段保持不变。
    return replace(
        record,
        status=next_status,
        verified_objects_digest=new_verified_digest,
        error=error
        if next_status
        in {
            ReleaseActivationStatus.FAILED,
            ReleaseActivationStatus.CONFLICTED,
        }
        else record.error,
    )
