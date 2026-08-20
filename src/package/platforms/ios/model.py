"""iOS Xcode 导出选项、导出目标和秘密引用映射模型。

本模块定义 iOS 打包流程的纯内存输入契约。模型不访问 Xcode、证书、provisioning
profile 或秘密提供器，只保存 ``SecretRef`` 引用；真正的 profile 读取、签名和 IPA
导出由后续平台适配器负责。所有集合在构造时归一化，以保证计划和日志摘要具有确定性。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import cast

from configuration.model import SecretRef

_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")


class IosExportMethod(Enum):
    """Xcode ``ExportOptions.plist`` 支持的标准 iOS 导出方法。

    ``value`` 直接对应 Xcode 导出选项中的公开字符串。加固、重签和其他非标准方式
    不在此基础模型中表达，避免把后续扩展能力误当作基础流水线能力。
    """

    DEVELOPMENT = "development"
    AD_HOC = "ad-hoc"
    APP_STORE = "app-store"
    ENTERPRISE = "enterprise"


class IosExportTarget(Enum):
    """iOS 包体签名目标类别。

    目标类别用于索引 profile、证书和私钥引用。它与 Xcode 导出方法分开建模，因而
    同一目标集合可以在不同配置下生成不同的导出计划，而不能依赖输出文件名猜测目标。
    """

    DEVELOPMENT = "development"
    DEV = "development"
    DISTRIBUTION = "distribution"
    IN_HOUSE = "in-house"


@dataclass(frozen=True, slots=True)
class IosPackageOptions:
    """描述一次 iOS Xcode 导出的完整、不可变配置。

    参数：
        bundle_id: 应用的点分 bundle identifier。
        configuration: Xcode 构建配置，例如 ``Debug`` 或 ``Release``。
        export_method: Xcode 标准导出方法。
        export_targets: 需要独立产出的签名目标集合。
        team_reference: Apple Developer Team ID 或受控配置中的公开团队引用。
        profile_refs: 每个目标对应的 provisioning profile ``SecretRef``。
        certificate_refs: 每个目标对应的签名证书 ``SecretRef``。
        private_key_refs: 每个目标对应的私钥 ``SecretRef``。
        project_only: 是否只导出 Xcode 工程；为真时可省略三类签名材料。

    返回：
        一个字段不可重新赋值、集合已确定性归一化的配置对象。

    异常：
        字段类型错误、文本为空或含控制字符、目标重复、秘密引用类型错误，或非
        project-only 配置缺少目标映射时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        非 project-only 配置要求三类引用分别对每个目标恰好映射一次。project-only
        配置不解析或需要签名材料，但仍拒绝不完整的非空映射；构造过程不执行 I/O。
    """

    bundle_id: str
    configuration: str
    export_method: IosExportMethod
    export_targets: tuple[IosExportTarget, ...]
    team_reference: str
    profile_refs: tuple[tuple[IosExportTarget, SecretRef], ...]
    certificate_refs: tuple[tuple[IosExportTarget, SecretRef], ...]
    private_key_refs: tuple[tuple[IosExportTarget, SecretRef], ...]
    project_only: bool

    def __post_init__(self) -> None:
        """校验并归一化 iOS 导出选项的类型、文本和目标映射。

        参数：
            无；读取构造参数对应的实例字段。

        返回：
            ``None``；目标集合和三类秘密映射会被替换为稳定排序后的 tuple。

        异常：
            输入类型不匹配时抛出 ``TypeError``；非法文本、空集合、重复目标或映射
            不完整时抛出 ``ValueError``。

        约束与副作用：
            只进行内存校验和不可变对象字段归一化，不读取秘密、不检查证书文件存在性。
        """
        _validate_text(self.bundle_id, "bundle_id")
        if _BUNDLE_ID_PATTERN.fullmatch(self.bundle_id) is None:
            raise ValueError("bundle_id 不是合法的点分标识")
        _validate_text(self.configuration, "configuration")
        _validate_text(self.team_reference, "team_reference")
        if not isinstance(self.export_method, IosExportMethod):
            raise TypeError("export_method 必须是 IosExportMethod")
        if not isinstance(self.project_only, bool):
            raise TypeError("project_only 必须是 bool")

        targets = _normalize_targets(self.export_targets)
        profile_refs = _normalize_refs(self.profile_refs, "profile_refs")
        certificate_refs = _normalize_refs(self.certificate_refs, "certificate_refs")
        private_key_refs = _normalize_refs(self.private_key_refs, "private_key_refs")

        for field_name, references in (
            ("profile_refs", profile_refs),
            ("certificate_refs", certificate_refs),
            ("private_key_refs", private_key_refs),
        ):
            if references and set(target for target, _ in references) != set(targets):
                raise ValueError(f"{field_name} 必须为每个 export target 提供恰好一份引用")
            if not self.project_only and not references:
                raise ValueError(f"非 project-only 配置必须提供 {field_name}")

        object.__setattr__(self, "export_targets", targets)
        object.__setattr__(self, "profile_refs", profile_refs)
        object.__setattr__(self, "certificate_refs", certificate_refs)
        object.__setattr__(self, "private_key_refs", private_key_refs)


def _validate_text(value: object, field_name: str) -> None:
    """校验模型中的公开文本字段非空且不含空白或控制字符。

    参数：
        value: 待校验对象。
        field_name: 用于构造错误信息的字段名。

    返回：
        ``None``；输入满足非空文本约束时正常返回。

    异常：
        输入不是字符串或为空时抛出 ``TypeError``；包含空白或控制字符时抛出
        ``ValueError``。

    约束与副作用：
        只检查内存中的文本，不修改输入对象。
    """
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} 不得为空或首尾含空白")
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError(f"{field_name} 不得包含空白或控制字符")


def _normalize_targets(
    targets: object,
) -> tuple[IosExportTarget, ...]:
    """校验并稳定排序导出目标集合。

    参数：
        targets: 期望为非空 ``tuple[IosExportTarget, ...]`` 的输入集合。

    返回：
        按枚举公开值 UTF-8 字节序排序并去重的目标 tuple。

    异常：
        输入不是 tuple、为空或包含非 ``IosExportTarget`` 项时抛出 ``TypeError`` 或
        ``ValueError``。

    约束与副作用：
        去重保证集合语义和确定性顺序；不改变调用者持有的原 tuple。
    """
    if not isinstance(targets, tuple):
        raise TypeError("export_targets 必须是 tuple")
    if not targets:
        raise ValueError("export_targets 必须是非空 tuple")
    typed_targets: list[IosExportTarget] = []
    for target in cast(tuple[object, ...], targets):
        if not isinstance(target, IosExportTarget):
            raise TypeError("export_targets 的每一项必须是 IosExportTarget")
        typed_targets.append(target)
    return tuple(sorted(set(typed_targets), key=lambda target: target.value.encode("utf-8")))


def _normalize_refs(
    references: object,
    field_name: str,
) -> tuple[tuple[IosExportTarget, SecretRef], ...]:
    """校验并稳定排序一个目标到 SecretRef 的映射。

    参数：
        references: 目标和 ``SecretRef`` 组成的二元组 tuple。
        field_name: 用于构造错误信息的字段名。

    返回：
        按目标公开值 UTF-8 字节序排序的不可变映射项 tuple。

    异常：
        外层或映射项类型错误时抛出 ``TypeError``；目标重复时抛出 ``ValueError``。

    约束与副作用：
        只保存秘密引用对象，不读取引用内容；重复目标会被拒绝而不会静默覆盖。
    """
    if not isinstance(references, tuple):
        raise TypeError(f"{field_name} 必须是 tuple")
    normalized: list[tuple[IosExportTarget, SecretRef]] = []
    seen: set[IosExportTarget] = set()
    for item in cast(tuple[object, ...], references):
        if not isinstance(item, tuple):
            raise TypeError(f"{field_name} 的每一项必须是二元 tuple")
        pair = cast(tuple[object, ...], item)
        if len(pair) != 2:
            raise TypeError(f"{field_name} 的每一项必须是二元 tuple")
        target, secret_ref = pair
        if not isinstance(target, IosExportTarget):
            raise TypeError(f"{field_name} 的目标必须是 IosExportTarget")
        if not isinstance(secret_ref, SecretRef):
            raise TypeError(f"{field_name} 的引用必须是 SecretRef")
        if target in seen:
            raise ValueError(f"{field_name} 不得包含重复目标")
        seen.add(target)
        normalized.append((target, secret_ref))
    return tuple(sorted(normalized, key=lambda item: item[0].value.encode("utf-8")))
