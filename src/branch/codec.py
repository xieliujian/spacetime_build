"""BranchPlan 的规范 JSON、内容寻址 ID 和纯文件编解码。

本模块把不可变计划编码为无 BOM、紧凑、UTF-8 的规范 JSON。持久化 payload 同时
保存 ``plan_id``，但 ID 计算永远排除该字段；读取时严格检查 schema、字段集合、
类型和每个嵌套对象，再重建模型并重算 ID。因此文件篡改、陈旧 ID、未知 schema
或重复 JSON 字段都不会被静默接受。本模块不依赖 ObjectStore，只提供 bytes 和
调用方显式指定的本地 Path 读写。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from branch.model import BranchCopy, BranchSource, BranchTarget, PropertyChange
from branch.planner import BranchPlan, BranchPlanError, compute_plan_id, plan_payload_without_id


class BranchPlanCodecError(ValueError):
    """BranchPlan payload 无法通过严格 schema 或 ID 完整性校验时抛出的异常。"""


_SCHEMA_VERSION = 1
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "mapping_version",
        "repository_uuid",
        "source_revision",
        "expected_repository_revision",
        "source_root",
        "target_root",
        "copies",
        "property_changes",
        "plan_id",
    }
)
_COPY_FIELDS = frozenset({"source", "target", "source_path", "target_path"})
_SOURCE_FIELDS = frozenset({"url", "repository_uuid", "revision"})
_TARGET_FIELDS = frozenset({"url", "repository_uuid"})
_PROPERTY_CHANGE_FIELDS = frozenset({"path", "property_name", "old_value", "new_value"})
_EXTERNAL_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _object(value: object, field_name: str) -> dict[str, object]:
    """要求 JSON 值为对象并转换为严格类型视图。"""
    if not isinstance(value, dict):
        raise BranchPlanCodecError(f"{field_name} 必须是 JSON 对象")
    return cast(dict[str, object], value)


def _list(value: object, field_name: str) -> list[object]:
    """要求 JSON 值为数组，拒绝字符串伪装序列。"""
    if not isinstance(value, list):
        raise BranchPlanCodecError(f"{field_name} 必须是 JSON 数组")
    return cast(list[object], value)


def _string(value: object, field_name: str) -> str:
    """要求 JSON 值为非空字符串。"""
    if not isinstance(value, str) or not value.strip():
        raise BranchPlanCodecError(f"{field_name} 必须是非空字符串")
    return value


def _integer(value: object, field_name: str) -> int:
    """要求 JSON 值为严格整数而不是 bool。"""
    if type(value) is not int:
        raise BranchPlanCodecError(f"{field_name} 必须是整数")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    """读取允许 null 的属性值字段。"""
    if value is not None and not isinstance(value, str):
        raise BranchPlanCodecError(f"{field_name} 必须是字符串或 null")
    return value


def _require_fields(data: dict[str, object], fields: frozenset[str], field_name: str) -> None:
    """拒绝对象中的未知字段和缺失字段。"""
    actual = set(data)
    missing = fields - actual
    unknown = actual - fields
    if missing:
        raise BranchPlanCodecError(f"{field_name} 缺少字段: {sorted(missing)[0]}")
    if unknown:
        raise BranchPlanCodecError(f"{field_name} 存在未知字段: {sorted(unknown)[0]}")


def _canonical_bytes(value: object) -> bytes:
    """将 JSON 对象编码为规范 UTF-8 字节。"""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BranchPlanCodecError("计划 payload 无法编码为规范 JSON") from exc


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """作为 json object_pairs_hook 拒绝重复字段，避免签名歧义。"""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BranchPlanCodecError(f"JSON 字段重复: {key}")
        result[key] = value
    return result


class BranchPlanCodec:
    """提供 BranchPlan 的严格 payload、bytes 和 Path 编解码操作。"""

    @staticmethod
    def to_payload(plan: BranchPlan) -> dict[str, object]:
        """把有效计划转成包含 plan_id 的规范 payload。

        参数：
            plan: planner 生成的不可变 BranchPlan。

        返回：
            只含 JSON 基础类型的 payload 字典；调用方修改返回值不会改变 plan。

        异常：
            输入类型错误或 plan_id 与内容重算值不一致时抛 ``BranchPlanCodecError``。

        约束与副作用：
            纯内存操作，不访问 ObjectStore、不读写文件，也不暴露隐含凭据。
        """
        if not isinstance(plan, BranchPlan):
            raise BranchPlanCodecError("plan 必须是 BranchPlan")
        expected = compute_plan_id(plan)
        if plan.plan_id != expected:
            raise BranchPlanCodecError("plan_id 与计划内容不一致")
        for change in plan.property_changes:
            _validate_persistable_property_change(change.property_name, change.old_value)
        payload = plan_payload_without_id(plan)
        payload["plan_id"] = plan.plan_id
        return payload

    @staticmethod
    def from_payload(value: object) -> BranchPlan:
        """严格解析 payload，重建模型并验证内容寻址 plan_id。

        参数：
            value: ``json.loads`` 产生的对象，必须是完整计划 JSON object。

        返回：
            通过 schema、嵌套字段和 ID 校验的不可变 BranchPlan。

        异常：
            未知 schema/字段、类型错误、重复目标、篡改内容或陈旧 ID 抛
            ``BranchPlanCodecError``。

        约束与副作用：
            不信任 payload 中的 plan_id；先构造不带信任的模型，再根据内容重算。
        """
        try:
            data = _object(value, "payload")
            _require_fields(data, _TOP_LEVEL_FIELDS, "payload")
            if _integer(data["schema_version"], "schema_version") != _SCHEMA_VERSION:
                raise BranchPlanCodecError("不支持的 schema_version")
            plan_id = _string(data["plan_id"], "plan_id")
            mapping_version = _string(data["mapping_version"], "mapping_version")
            repository_uuid = _string(data["repository_uuid"], "repository_uuid")
            source_revision = _integer(data["source_revision"], "source_revision")
            expected_revision = _integer(
                data["expected_repository_revision"], "expected_repository_revision"
            )
            source_root = _string(data["source_root"], "source_root")
            target_root = _string(data["target_root"], "target_root")
            copies = tuple(
                _decode_copy(item, index)
                for index, item in enumerate(_list(data["copies"], "copies"))
            )
            changes = tuple(
                _decode_property_change(item, index)
                for index, item in enumerate(_list(data["property_changes"], "property_changes"))
            )
            plan = BranchPlan(
                plan_id=plan_id,
                source_revision=source_revision,
                expected_repository_revision=expected_revision,
                source_root=source_root,
                target_root=target_root,
                copies=copies,
                property_changes=changes,
                repository_uuid=repository_uuid,
                mapping_version=mapping_version,
                schema_version=_SCHEMA_VERSION,
            )
        except BranchPlanCodecError:
            raise
        except (BranchPlanError, TypeError, ValueError) as exc:
            raise BranchPlanCodecError("计划 payload 不符合 BranchPlan 约束") from exc
        expected_id = compute_plan_id(plan)
        if plan.plan_id != expected_id:
            raise BranchPlanCodecError("plan_id 过期或 payload 已篡改")
        return plan

    @staticmethod
    def to_bytes(plan: BranchPlan) -> bytes:
        """将有效计划编码为规范 JSON UTF-8 bytes。"""
        return _canonical_bytes(BranchPlanCodec.to_payload(plan))

    @staticmethod
    def from_bytes(content: bytes) -> BranchPlan:
        """从无 BOM 的 UTF-8 JSON bytes 严格读取 BranchPlan。"""
        if not isinstance(content, bytes):
            raise BranchPlanCodecError("content 必须是 bytes")
        try:
            decoded = json.loads(content.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (UnicodeDecodeError, json.JSONDecodeError, BranchPlanCodecError) as exc:
            raise BranchPlanCodecError("计划 JSON 无法解析") from exc
        return BranchPlanCodec.from_payload(decoded)

    @staticmethod
    def write(plan: BranchPlan, path: Path) -> None:
        """将有效计划写入调用方指定的本地文件。"""
        if not isinstance(path, Path):
            raise BranchPlanCodecError("path 必须是 pathlib.Path")
        path.write_bytes(BranchPlanCodec.to_bytes(plan))

    @staticmethod
    def read(path: Path) -> BranchPlan:
        """从调用方指定的本地文件读取并严格验证 BranchPlan。"""
        if not isinstance(path, Path):
            raise BranchPlanCodecError("path 必须是 pathlib.Path")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise BranchPlanCodecError("计划文件读取失败") from exc
        return BranchPlanCodec.from_bytes(content)


class BranchPlanFactory:
    """提供从不可信 payload 重算 plan_id 的显式工厂入口。"""

    @staticmethod
    def create(payload: object) -> BranchPlan:
        """严格重建 BranchPlan，不接受调用方覆盖计算出的 ID。"""
        return BranchPlanCodec.from_payload(payload)

    @staticmethod
    def from_payload(payload: object) -> BranchPlan:
        """提供 ``create`` 的语义兼容别名。"""
        return BranchPlanCodec.from_payload(payload)


def _decode_copy(value: object, index: int) -> BranchCopy:
    """解码并校验单个复制操作。"""
    data = _object(value, f"copies[{index}]")
    _require_fields(data, _COPY_FIELDS, f"copies[{index}]")
    source_data = _object(data["source"], f"copies[{index}].source")
    target_data = _object(data["target"], f"copies[{index}].target")
    _require_fields(source_data, _SOURCE_FIELDS, f"copies[{index}].source")
    _require_fields(target_data, _TARGET_FIELDS, f"copies[{index}].target")
    return BranchCopy(
        source=BranchSource(
            url=_string(source_data["url"], f"copies[{index}].source.url"),
            repository_uuid=_string(
                source_data["repository_uuid"], f"copies[{index}].source.repository_uuid"
            ),
            revision=_integer(source_data["revision"], f"copies[{index}].source.revision"),
        ),
        target=BranchTarget(
            url=_string(target_data["url"], f"copies[{index}].target.url"),
            repository_uuid=_string(
                target_data["repository_uuid"], f"copies[{index}].target.repository_uuid"
            ),
        ),
        source_path=_string(data["source_path"], f"copies[{index}].source_path"),
        target_path=_string(data["target_path"], f"copies[{index}].target_path"),
    )


def _decode_property_change(value: object, index: int) -> PropertyChange:
    """解码并校验单个属性变化。"""
    data = _object(value, f"property_changes[{index}]")
    _require_fields(data, _PROPERTY_CHANGE_FIELDS, f"property_changes[{index}]")
    property_name = _string(data["property_name"], f"property_changes[{index}].property_name")
    old_value = _optional_string(data["old_value"], f"property_changes[{index}].old_value")
    _validate_persistable_property_change(
        property_name,
        old_value,
        field_name=f"property_changes[{index}].old_value",
    )
    return PropertyChange(
        path=_string(data["path"], f"property_changes[{index}].path"),
        property_name=property_name,
        old_value=old_value,
        new_value=_optional_string(data["new_value"], f"property_changes[{index}].new_value"),
    )


def _validate_persistable_property_change(
    property_name: str,
    old_value: str | None,
    *,
    field_name: str = "property_changes.old_value",
) -> None:
    """拒绝 svn:externals 旧值原文进入持久化计划。"""
    if (
        property_name == "svn:externals"
        and old_value is not None
        and _EXTERNAL_DIGEST_PATTERN.fullmatch(old_value) is None
    ):
        raise BranchPlanCodecError(f"{field_name} 必须是 sha256 摘要，不得保存 externals 原文")


__all__ = [
    "BranchPlanCodec",
    "BranchPlanCodecError",
    "BranchPlanFactory",
]
