"""Release activation record 和验证凭证的严格本地 JSON codec。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from core.manifest_codec import canonical_json_bytes
from core.errors import PublishError
from release.activation import (
    ReleaseActivationRecord,
    ReleaseActivationStatus,
    VerifiedReleaseBundle,
    bind_verified_release_bundle,
)


def _write(document: dict[str, object], path: Path) -> None:
    """原子写入规范 JSON。"""
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("path 必须是绝对 Path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(canonical_json_bytes(document))
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_verified_release_bundle(verification: VerifiedReleaseBundle, path: Path) -> None:
    """写入不含秘密的已验证 Bundle 凭证。"""
    if not isinstance(verification, VerifiedReleaseBundle):
        raise TypeError("verification 必须是 VerifiedReleaseBundle")
    _write(
        {
            "bundle_id": verification.bundle_id,
            "required_objects_digest": verification.required_objects_digest,
            "schema_version": 1,
            "verified_objects_digest": verification.verified_objects_digest,
        },
        path,
    )


def _mapping(value: object, field_name: str) -> dict[str, object]:
    """读取 JSON object。"""
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 object")
    return cast(dict[str, object], value)


def _text(value: object, field_name: str) -> str:
    """读取非空文本。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def _integer(value: object, field_name: str) -> int:
    """读取非布尔整数。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是 int")
    return value


def read_verified_release_bundle(path: Path) -> VerifiedReleaseBundle:
    """读取验证凭证并通过私有 binder 重新绑定身份。"""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"验证凭证 JSON 无法读取: {path}") from exc
    root = _mapping(document, "验证凭证")
    if _integer(root.get("schema_version"), "schema_version") != 1:
        raise ValueError("不支持的验证凭证 schema_version")
    return bind_verified_release_bundle(
        bundle_id=_text(root.get("bundle_id"), "bundle_id"),
        required_objects_digest=_text(
            root.get("required_objects_digest"), "required_objects_digest"
        ),
        verified_objects_digest=_text(
            root.get("verified_objects_digest"), "verified_objects_digest"
        ),
    )


def write_release_activation_record(record: ReleaseActivationRecord, path: Path) -> None:
    """写入激活记录快照。"""
    if not isinstance(record, ReleaseActivationRecord):
        raise TypeError("record 必须是 ReleaseActivationRecord")
    _write(
        {
            "activation_id": record.activation_id,
            "bundle_id": record.bundle_id,
            "error": record.error,
            "expected_generation": record.expected_generation,
            "required_objects_digest": record.required_objects_digest,
            "schema_version": record.schema_version,
            "status": record.status.value,
            "target": record.target,
            "verified_objects_digest": record.verified_objects_digest,
        },
        path,
    )


def read_release_activation_record(path: Path) -> ReleaseActivationRecord:
    """读取并严格构造激活记录。"""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"激活记录 JSON 无法读取: {path}") from exc
    root = _mapping(document, "激活记录")
    try:
        status = ReleaseActivationStatus(_text(root.get("status"), "status"))
    except ValueError as exc:
        raise ValueError("激活记录 status 非法") from exc
    try:
        return ReleaseActivationRecord(
            _integer(root.get("schema_version"), "schema_version"),
            _text(root.get("activation_id"), "activation_id"),
            _text(root.get("bundle_id"), "bundle_id"),
            _text(root.get("target"), "target"),
            _integer(root.get("expected_generation"), "expected_generation"),
            status,
            _text(root.get("required_objects_digest"), "required_objects_digest"),
            root.get("verified_objects_digest")
            if root.get("verified_objects_digest") is None
            else _text(root.get("verified_objects_digest"), "verified_objects_digest"),
            root.get("error") if root.get("error") is None else _text(root.get("error"), "error"),
        )
    except (PublishError, TypeError, ValueError) as exc:
        raise ValueError("激活记录字段非法") from exc


__all__ = [
    "read_release_activation_record",
    "read_verified_release_bundle",
    "write_release_activation_record",
    "write_verified_release_bundle",
]
