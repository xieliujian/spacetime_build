"""SDK descriptor 的严格 TOML catalog 加载器。

catalog 只接受固定字段并立即构造 ``SdkDescriptor``；未知字段、可执行字段、坏摘要和
坏枚举在进入业务层前失败。模块导入不读取默认文件、不扫描目录、不启用未知渠道。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import tomli

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from sdk.model import SdkDescriptor, SdkOperation, SdkOperationKind, SdkStage

_DESCRIPTOR_KEYS = {
    "sdk_id",
    "version",
    "platform",
    "stage",
    "inputs",
    "outputs",
    "secret_refs",
    "validation_rules",
    "operations",
}
_OPTIONAL_DESCRIPTOR_KEYS = {"depends_on"}
_OPERATION_KEYS = {"kind", "target", "value", "conflict_key"}
_BLOB_KEYS = {"locator", "sha256", "size"}


@dataclass(frozen=True, slots=True)
class SdkCatalog:
    """包含已严格解析并排序的 SDK descriptor 集合。"""

    descriptors: tuple[SdkDescriptor, ...]

    def __post_init__(self) -> None:
        """校验 catalog descriptor 类型、唯一性和确定性顺序。"""
        if not isinstance(self.descriptors, tuple) or not all(
            isinstance(item, SdkDescriptor) for item in self.descriptors
        ):
            raise TypeError("descriptors 必须是 tuple[SdkDescriptor, ...]")
        ordered = tuple(
            sorted(
                self.descriptors,
                key=lambda item: (item.sdk_id.encode("utf-8"), item.version.encode("utf-8")),
            )
        )
        if ordered != self.descriptors:
            raise ValueError("descriptors 必须按 sdk_id/version 排序")
        identities = [(item.sdk_id, item.version, item.platform) for item in self.descriptors]
        if len(set(identities)) != len(identities):
            raise ValueError("catalog 不得包含重复 descriptor")

    @staticmethod
    def from_toml(payload: bytes) -> "SdkCatalog":
        """从 TOML bytes 加载严格 catalog，不执行其中任何字段。

        参数：
            payload: UTF-8/TOML 编码的 catalog bytes。

        返回：
            解析后的 ``SdkCatalog``。

        异常：
            TOML 语法、根字段、descriptor 字段、Blob 摘要、操作字段或枚举非法时抛出
            ``ValueError``；payload 类型错误时抛出 ``TypeError``。

        约束与副作用：
            只在内存中解析；不会导入 module、运行 script/command 或读取文件。
        """
        if not isinstance(payload, bytes):
            raise TypeError("payload 必须是 bytes")
        try:
            document = tomli.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, tomli.TOMLDecodeError, ValueError) as exc:
            raise ValueError("SDK catalog TOML 无法解析") from exc
        root = cast(dict[str, object], document)
        if set(root) != {"descriptors"} or not isinstance(root["descriptors"], list):
            raise ValueError("SDK catalog 根 schema 无效")
        raw_descriptors = cast(list[object], root["descriptors"])
        descriptors = tuple(_descriptor(_mapping(item, "descriptor")) for item in raw_descriptors)
        descriptors = tuple(
            sorted(
                descriptors,
                key=lambda item: (item.sdk_id.encode("utf-8"), item.version.encode("utf-8")),
            )
        )
        return SdkCatalog(descriptors)


def _descriptor(document: dict[str, object]) -> SdkDescriptor:
    """从严格字段字典构造一个 SDK descriptor。"""
    if not _DESCRIPTOR_KEYS.issubset(document) or set(document) - (
        _DESCRIPTOR_KEYS | _OPTIONAL_DESCRIPTOR_KEYS
    ):
        raise ValueError("SDK descriptor 字段不符合 schema")
    raw_inputs = _list(document["inputs"], "inputs")
    inputs: list[BlobRef] = []
    for raw in raw_inputs:
        item = _mapping(raw, "input")
        if set(item) != _BLOB_KEYS:
            raise ValueError("SDK input Blob 字段不符合 schema")
        inputs.append(
            BlobRef(_string(item["locator"]), _string(item["sha256"]), _integer(item["size"]))
        )
    operations: list[SdkOperation] = []
    for raw in _list(document["operations"], "operations"):
        item = _mapping(raw, "operation")
        if set(item) != _OPERATION_KEYS:
            raise ValueError("SDK operation 字段不符合 schema")
        try:
            kind = SdkOperationKind(_string(item["kind"]))
        except ValueError as exc:
            raise ValueError("SDK operation kind 不支持") from exc
        operations.append(
            SdkOperation(
                kind,
                _string(item["target"]),
                _optional_string(item["value"]),
                _string(item["conflict_key"]),
            )
        )
    try:
        platform = BuildPlatform(_string(document["platform"]))
        stage = SdkStage(_string(document["stage"]))
    except ValueError as exc:
        raise ValueError("SDK platform/stage 不支持") from exc
    return SdkDescriptor(
        _string(document["sdk_id"]),
        _string(document["version"]),
        platform,
        stage,
        tuple(inputs),
        tuple(_string(item) for item in _list(document["outputs"], "outputs")),
        tuple(operations),
        tuple(_string(item) for item in _list(document["secret_refs"], "secret_refs")),
        tuple(_string(item) for item in _list(document["validation_rules"], "validation_rules")),
        tuple(_string(item) for item in _list(document.get("depends_on", []), "depends_on")),
    )


def _mapping(value: object, field_name: str) -> dict[str, object]:
    """读取 TOML table，并拒绝非字符串键。"""
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 table")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{field_name} key 必须是 str")
    return cast(dict[str, object], raw)


def _list(value: object, field_name: str) -> list[object]:
    """读取 TOML array 并显式转换元素为 object。"""
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是 array")
    return cast(list[object], value)


def _string(value: object) -> str:
    """读取 TOML 非空字符串。"""
    if not isinstance(value, str) or not value:
        raise ValueError("SDK 字段必须是非空字符串")
    return value


def _optional_string(value: object) -> str | None:
    """读取可选 TOML 字符串。"""
    if value is None:
        return None
    return _string(value)


def _integer(value: object) -> int:
    """读取 TOML 非布尔整数。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("SDK size 必须是整数")
    return value


__all__ = ["SdkCatalog"]
