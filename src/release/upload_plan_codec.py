"""UploadPlan 的严格、确定性本地 JSON codec。

独立 upload/activate/publish 命令需要在进程之间传递同一份上传计划。本模块把计划
中的对象字节以标准 Base64 保存，并在读取时重算每个 Blob 和 ``plan_id``；文件中的
身份字段不能覆盖重算结果。codec 只读写调用方指定的普通文件，不访问对象存储或
执行上传。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
from typing import cast

from core.artifacts import BlobRef
from core.manifest_codec import canonical_json_bytes
from release.upload_plan import UploadItem, UploadPhase, UploadPlan, UploadPlanFactory

UPLOAD_PLAN_SCHEMA_VERSION = 1


def _item_document(item: UploadItem) -> dict[str, object]:
    """将上传项编码为包含内容的规范对象。"""
    return {
        "content_base64": base64.b64encode(item.content).decode("ascii"),
        "key": item.key,
        "phase": item.phase.value,
        "sha256": item.blob.sha256,
        "size": item.blob.size,
    }


def _document(plan: UploadPlan) -> dict[str, object]:
    """构造不含额外运行态的规范 UploadPlan 文档。"""
    return {
        "bundle_id": plan.bundle_id,
        "expected_generation": plan.expected_generation,
        "objects": [_item_document(item) for item in plan.objects],
        "plan_id": plan.plan_id,
        "schema_version": UPLOAD_PLAN_SCHEMA_VERSION,
        "version_entry": _item_document(plan.version_entry),
    }


def write_upload_plan(plan: UploadPlan, path: Path) -> None:
    """原子写入一份确定性 UploadPlan JSON。"""
    if not isinstance(plan, UploadPlan):
        raise TypeError("plan 必须是 UploadPlan")
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("path 必须是绝对 Path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(canonical_json_bytes(_document(plan)))
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _required_mapping(value: object, field_name: str) -> dict[str, object]:
    """读取 JSON object。"""
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 object")
    return cast(dict[str, object], value)


def _required_string(value: object, field_name: str) -> str:
    """读取非空字符串字段。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def _required_int(value: object, field_name: str) -> int:
    """读取非布尔整数。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是 int")
    return value


def _read_item(value: object, field_name: str) -> UploadItem:
    """严格读取一个包含内容身份的 UploadItem。"""
    raw = _required_mapping(value, field_name)
    try:
        content = base64.b64decode(
            _required_string(raw.get("content_base64"), f"{field_name}.content_base64"),
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{field_name}.content_base64 不是合法 Base64") from exc
    phase_text = _required_string(raw.get("phase"), f"{field_name}.phase")
    try:
        phase = UploadPhase(phase_text)
    except ValueError as exc:
        raise ValueError(f"{field_name}.phase 非法") from exc
    digest = _required_string(raw.get("sha256"), f"{field_name}.sha256")
    size = _required_int(raw.get("size"), f"{field_name}.size")
    return UploadItem(
        _required_string(raw.get("key"), f"{field_name}.key"),
        BlobRef(f"blobs/{digest}", digest, size),
        content,
        phase,
    )


def read_upload_plan(path: Path) -> UploadPlan:
    """读取 UploadPlan，重算 Blob 身份和 plan_id 后返回可信计划。"""
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("path 必须是绝对 Path")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"UploadPlan JSON 无法读取: {path}") from exc
    root = _required_mapping(document, "UploadPlan 根对象")
    if _required_int(root.get("schema_version"), "schema_version") != UPLOAD_PLAN_SCHEMA_VERSION:
        raise ValueError("不支持的 UploadPlan schema_version")
    raw_objects = root.get("objects")
    if not isinstance(raw_objects, list):
        raise ValueError("objects 必须是数组")
    objects = tuple(_read_item(item, "objects[]") for item in raw_objects)
    version_entry = _read_item(root.get("version_entry"), "version_entry")
    resources = tuple(item for item in objects if item.phase is UploadPhase.RESOURCE)
    protocols = tuple(item for item in objects if item.phase is UploadPhase.PROTOCOL)
    if len(resources) + len(protocols) != len(objects):
        raise ValueError("objects 不得包含 version_entry 阶段")
    plan = UploadPlanFactory.create(
        _required_string(root.get("bundle_id"), "bundle_id"),
        resources,
        protocols,
        version_entry,
        _required_int(root.get("expected_generation"), "expected_generation"),
    )
    if plan.plan_id != _required_string(root.get("plan_id"), "plan_id"):
        raise ValueError("UploadPlan.plan_id 与内容重算值不一致")
    return plan


__all__ = ["UPLOAD_PLAN_SCHEMA_VERSION", "read_upload_plan", "write_upload_plan"]
