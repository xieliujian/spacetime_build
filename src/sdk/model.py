"""SDK descriptor 和结构化操作的不可变领域模型。

本模块把渠道扩展表达为白名单数据，不接受模块名、脚本、shell 命令或任意覆盖指令。
descriptor 只保存公开版本、平台、payload 摘要、逻辑输出、SecretRef 名称和验证规则；
真实秘密解析及平台应用由后续 SDK adapter 在最短租约内完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re

from core.artifacts import BlobRef
from core.platforms import BuildPlatform

_IDENTITY_PATTERN = re.compile(r"^[^\x00-\x1f\r\n]+$")


class SdkStage(Enum):
    """SDK hook 允许运行的构建阶段。"""

    PRE_BUILD = "pre_build"
    POST_BUILD = "post_build"


class SdkOperationKind(Enum):
    """SDK 允许的结构化变换种类。"""

    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    SET_PROPERTY = "set_property"


def _validate_text(value: object, field_name: str) -> str:
    """校验 SDK 公开文本非空且不含控制字符。"""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str")
    if not value or _IDENTITY_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} 必须是非空且无控制字符文本")
    return value


def _validate_path(value: object, field_name: str) -> str:
    """校验 SDK 逻辑路径为安全相对正斜杠路径。"""
    value = _validate_text(value, field_name)
    if value.startswith("/") or "\\" in value:
        raise ValueError(f"{field_name} 必须是相对正斜杠路径")
    if any(not part or part in {".", ".."} for part in value.split("/")):
        raise ValueError(f"{field_name} 含非法路径段")
    return value


@dataclass(frozen=True, slots=True)
class SdkOperation:
    """描述一个固定白名单的 SDK 结构化操作。

    参数：
        kind: 写入、删除或属性设置操作。
        target: workspace 内的相对逻辑目标。
        value: write/set 操作的结构化文本值；delete 必须为 ``None``。
        conflict_key: 规划阶段用于检测独占资源冲突的稳定键。

    返回：
        一个不可变操作声明。

    异常：
        kind、路径、值或冲突键非法时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        不执行任何操作，不解析值为脚本；``value`` 仅作为固定文本交给平台白名单
        applier，不能表达命令。
    """

    kind: SdkOperationKind
    target: str
    value: str | None
    conflict_key: str

    def __post_init__(self) -> None:
        """校验操作种类、目标路径、值和独占冲突键。"""
        if not isinstance(self.kind, SdkOperationKind):
            raw_kind = self.kind
            if isinstance(raw_kind, str) and raw_kind.casefold() in {"command", "script", "module"}:
                raise ValueError(f"不允许 command/script/module 操作: {raw_kind}")
            raise TypeError("kind 必须是 SdkOperationKind")
        _validate_path(self.target, "target")
        _validate_text(self.conflict_key, "conflict_key")
        if self.kind is SdkOperationKind.DELETE_FILE:
            if self.value is not None:
                raise ValueError("delete_file 的 value 必须为 None")
        elif not isinstance(self.value, str):
            raise ValueError(f"{self.kind.value} 必须提供 value")
        else:
            _validate_text(self.value, "value")


@dataclass(frozen=True, slots=True)
class SdkDescriptor:
    """描述一个平台 SDK 的锁定输入、输出和结构化 hook 操作。

    参数：
        sdk_id: SDK 稳定标识。
        version: SDK 版本。
        platform: 目标平台。
        stage: hook 阶段。
        inputs: 已锁定 payload Blob 集合。
        outputs: SDK 声明拥有的逻辑输出路径。
        operations: 白名单结构化操作集合。
        secret_refs: 只记录引用名，不保存秘密值；repr 中隐藏该字段。
        validation_rules: 后验证规则标识。

    返回：
        不可变、可排序和可用于计划身份的 descriptor。

    异常：
        字段类型错误、重复输出/冲突键、非法路径或输入 Blob 错误时抛出
        ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        构造不读取 payload、不解析 SecretRef、不访问工程目录。
    """

    sdk_id: str
    version: str
    platform: BuildPlatform
    stage: SdkStage
    inputs: tuple[BlobRef, ...]
    outputs: tuple[str, ...]
    operations: tuple[SdkOperation, ...]
    secret_refs: tuple[str, ...] = field(repr=False)
    validation_rules: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验 descriptor 并按 UTF-8 顺序规范化集合。"""
        _validate_text(self.sdk_id, "sdk_id")
        _validate_text(self.version, "version")
        if not isinstance(self.platform, BuildPlatform):
            raise TypeError("platform 必须是 BuildPlatform")
        if not isinstance(self.stage, SdkStage):
            raise TypeError("stage 必须是 SdkStage")
        if not isinstance(self.inputs, tuple) or not all(
            isinstance(item, BlobRef) for item in self.inputs
        ):
            raise TypeError("inputs 必须是 tuple[BlobRef, ...]")
        outputs = _normalize_unique_paths(self.outputs, "outputs")
        if not isinstance(self.operations, tuple) or not all(
            isinstance(item, SdkOperation) for item in self.operations
        ):
            raise TypeError("operations 必须是 tuple[SdkOperation, ...]")
        operations = tuple(sorted(self.operations, key=lambda item: item.target.encode("utf-8")))
        conflict_keys = [item.conflict_key.casefold() for item in operations]
        if len(set(conflict_keys)) != len(conflict_keys):
            raise ValueError("operations 存在重复 conflict_key")
        if not isinstance(self.secret_refs, tuple) or not all(
            isinstance(item, str) and item for item in self.secret_refs
        ):
            raise TypeError("secret_refs 必须是字符串 tuple")
        secrets = tuple(sorted(set(self.secret_refs), key=lambda item: item.encode("utf-8")))
        if len(secrets) != len(self.secret_refs):
            raise ValueError("secret_refs 不得重复")
        if not isinstance(self.validation_rules, tuple) or not all(
            isinstance(item, str) and item for item in self.validation_rules
        ):
            raise TypeError("validation_rules 必须是字符串 tuple")
        rules = tuple(sorted(set(self.validation_rules), key=lambda item: item.encode("utf-8")))
        if len(rules) != len(self.validation_rules):
            raise ValueError("validation_rules 不得重复")
        if not isinstance(self.depends_on, tuple) or not all(
            isinstance(item, str) and item for item in self.depends_on
        ):
            raise TypeError("depends_on 必须是字符串 tuple")
        dependencies = tuple(sorted(set(self.depends_on), key=lambda item: item.encode("utf-8")))
        if len(dependencies) != len(self.depends_on):
            raise ValueError("depends_on 不得重复")
        if self.sdk_id in dependencies:
            raise ValueError("depends_on 不得包含自身")
        object.__setattr__(self, "outputs", outputs)
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "secret_refs", secrets)
        object.__setattr__(self, "validation_rules", rules)
        object.__setattr__(self, "depends_on", dependencies)


def _normalize_unique_paths(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    """校验、去重检查并稳定排序路径集合。"""
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} 必须是 tuple")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _validate_path(value, field_name)
        folded = path.casefold()
        if folded in seen:
            raise ValueError(f"{field_name} 存在重复路径: {path}")
        seen.add(folded)
        result.append(path)
    return tuple(sorted(result, key=lambda item: item.encode("utf-8")))


__all__ = ["SdkDescriptor", "SdkOperation", "SdkOperationKind", "SdkStage"]
