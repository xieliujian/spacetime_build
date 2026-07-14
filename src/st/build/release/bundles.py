"""协议无关 ReleaseBundle payload 与不可变发布包领域模型。

本模块提供 ``ReleaseBundlePayload`` 与仅能经工厂创建的 ``ReleaseBundle``。
payload 聚合无序 ``ReleaseManifest`` 集合与可选基线 bundle ID：必须恰有一个
``MAIN``、至多一个 ``LOW``，且共享同一 ``file_list_no``。历史低清
``object_version`` 已由 manifest 层校验，本模块不二次拒绝。本模块不生成旧
客户端协议文本，不实现发布器或 CDN。导入本模块不执行构建或发布。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from st.build.core.errors import PublishError
from st.build.release.entries import ResourceVariant
from st.build.release.manifests import ReleaseManifest

# 当前受支持的 ReleaseBundle schema；未知版本在构造与读取时一律拒绝。
RELEASE_BUNDLE_SCHEMA_VERSION = 1

# 仅 bind_release_bundle 持有；公开调用方无法合法传入该 token。
_RELEASE_BUNDLE_FACTORY_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ReleaseBundlePayload:
    """仅含可复现内容的发布包 payload。

    职责：
        记录 schema、无序 manifest 集合与可选基线 bundle ID；供工厂计算
        ``bundle_id``。字段名刻意排除 ID。

    参数：
        schema_version: payload schema 整数版本；未知版本在严格读取时拒绝。
        manifests: 已工厂创建的 ``ReleaseManifest`` 元组（无序集合语义）。
        baseline_bundle_id: 可选基线 bundle 内容寻址 ID；``None`` 表示无基线。

    返回：
        无；本类为不可变数据载体。

    异常：
        缺少/重复 MAIN、多于一个 LOW、FileListNo 不一致或类型非法时抛出
        ``PublishError``。

    约束与副作用：
        ``frozen=True, slots=True``；不计算 ID；无 I/O。不重验 CURRENT_UPLOAD
        哨兵；历史低清 object_version 不被二次拒绝。未知 schema 由读写边界拒绝。
    """

    schema_version: int
    manifests: tuple[ReleaseManifest, ...]
    baseline_bundle_id: str | None

    def __post_init__(self) -> None:
        """构造后校验 main/low 组合与共享 FileListNo。

        参数：
            无；读取实例字段。

        返回：
            ``None``。

        异常：
            任一不变量被违反时抛出 ``PublishError``。

        约束与副作用：
            仅内存校验；不持久化；不重验条目层 object_version 细节。
        """
        schema_version = cast(object, self.schema_version)
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise PublishError("schema_version 必须是 int")
        manifests = cast(object, self.manifests)
        if not isinstance(manifests, tuple):
            raise PublishError("manifests 必须是 tuple[ReleaseManifest, ...]")
        baseline = cast(object, self.baseline_bundle_id)
        if baseline is not None:
            if not isinstance(baseline, str) or baseline == "":
                raise PublishError("baseline_bundle_id 必须是非空 str 或 None")

        if len(cast(tuple[object, ...], manifests)) == 0:
            raise PublishError("ReleaseBundle 至少需要一个 ReleaseManifest")

        main_count = 0
        low_count = 0
        file_list_nos: set[int] = set()
        for manifest in cast(tuple[object, ...], manifests):
            if not isinstance(manifest, ReleaseManifest):
                raise PublishError("manifests 的每一项必须是 ReleaseManifest")
            variant = manifest.payload.variant
            if variant is ResourceVariant.MAIN:
                main_count += 1
            elif variant is ResourceVariant.LOW:
                low_count += 1
            else:
                raise PublishError(f"未知 ResourceVariant: {variant!r}")
            file_list_nos.add(manifest.payload.file_list_no)

        # 发布包激活语义：主清必选；低清可选且至多一份。
        if main_count != 1:
            raise PublishError(
                f"ReleaseBundle 必须恰有一个 MAIN ReleaseManifest，实际 MAIN 数量为 {main_count}"
            )
        if low_count > 1:
            raise PublishError(
                f"ReleaseBundle 至多允许一个 LOW ReleaseManifest，实际 LOW 数量为 {low_count}"
            )
        if len(file_list_nos) != 1:
            raise PublishError(
                "ReleaseBundle 内所有 ReleaseManifest 必须共享同一 "
                f"file_list_no，实际为 {sorted(file_list_nos)!r}"
            )


def bind_release_bundle(*, bundle_id: str, payload: ReleaseBundlePayload) -> ReleaseBundle:
    """将已计算的 ``bundle_id`` 与 payload 绑定为不可变 ``ReleaseBundle``。

    参数：
        bundle_id: 由工厂根据 payload 规范字节计算出的 64 位 SHA256。
        payload: 已校验的可复现 ``ReleaseBundlePayload``。

    返回：
        绑定 ID 与 payload 的不可变 ``ReleaseBundle``。

    异常：
        无；调用方保证 ID 已正确计算。

    约束与副作用：
        仅供 ``ReleaseBundleFactory`` 与编解码器使用；公开调用方应通过工厂创建，
        不得自行传入任意 ID。纯内存构造，无 I/O。
    """
    return ReleaseBundle(
        bundle_id=bundle_id,
        payload=payload,
        _factory_token=_RELEASE_BUNDLE_FACTORY_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class ReleaseBundle:
    """绑定可复现 payload 与内容寻址 ``bundle_id`` 的不可变发布包。

    职责：
        作为主/低清联合发布身份载体：``bundle_id`` 必须等于 payload 规范 JSON
        字节的 SHA256。公开调用方不得直接构造。

    参数：
        bundle_id: 64 位小写十六进制 SHA256；仅由工厂写入。
        payload: 可复现 ``ReleaseBundlePayload``。
        _factory_token: 模块私有工厂令牌。

    返回：
        无；本类为不可变数据载体。

    异常：
        直接公开构造时抛出 ``TypeError``。

    约束与副作用：
        ``frozen=True, slots=True``；创建只允许经工厂；无 I/O；不含发布器逻辑。
    """

    bundle_id: str
    payload: ReleaseBundlePayload
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
            仅内存门禁。
        """
        if self._factory_token is not _RELEASE_BUNDLE_FACTORY_TOKEN:
            raise TypeError(
                "ReleaseBundle 只能通过 ReleaseBundleFactory.create(payload) "
                "创建，禁止直接构造或传入自备 bundle_id"
            )
