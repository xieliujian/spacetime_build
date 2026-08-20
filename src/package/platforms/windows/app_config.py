"""Windows appconfig.json 的白名单模型、结构化变换和原子写入。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
_KNOWN_KEYS = frozenset(
    {"branch", "release_bundle_id", "release_entry", "version_code", "version_name"}
)


def _validate_text(value: object, field_name: str) -> str:
    """校验非空且无控制字符的 appconfig 文本字段。"""
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 必须是非空且无控制字符的字符串")
    return value


def _validate_relative_entry(value: object) -> str:
    """校验资源入口是相对正斜杠路径并拒绝设备名和点段。"""
    entry = _validate_text(value, "release_entry")
    if "\\" in entry or entry.startswith("/") or _DRIVE_PATTERN.match(entry):
        raise ValueError("release_entry 必须是相对正斜杠路径")
    segments = entry.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("release_entry 不得包含空段、. 或 ..")
    return entry


@dataclass(frozen=True, slots=True)
class WindowsAppConfig:
    """绑定 ReleaseBundle 入口、分支和客户端版本的 appconfig 请求。"""

    release_bundle_id: str
    release_entry: str
    branch: str
    version_name: str
    version_code: int

    def __post_init__(self) -> None:
        """校验 bundle SHA256、入口路径、分支文本和正版本号。"""
        if _SHA256_PATTERN.fullmatch(self.release_bundle_id) is None:
            raise ValueError("release_bundle_id 必须是 64 位小写 SHA256")
        _validate_relative_entry(self.release_entry)
        _validate_text(self.branch, "branch")
        _validate_text(self.version_name, "version_name")
        if not isinstance(self.version_code, int) or isinstance(self.version_code, bool):
            raise TypeError("version_code 必须是 int")
        if self.version_code <= 0:
            raise ValueError("version_code 必须是正整数")


class WindowsAppConfigTransformer:
    """使用 JSON parser 对 appconfig 进行白名单、确定性结构变换。"""

    @staticmethod
    def transform(source: bytes, config: WindowsAppConfig) -> bytes:
        """读取现有 JSON、校验字段并返回规范化的完整 appconfig 字节。"""
        if not isinstance(source, bytes):
            raise TypeError("source 必须是 bytes")
        if not isinstance(config, WindowsAppConfig):
            raise TypeError("config 必须是 WindowsAppConfig")
        try:
            document = json.loads(source.decode("utf-8"), object_pairs_hook=_reject_duplicates)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("appconfig JSON 无法解析或含重复键") from exc
        if not isinstance(document, dict):
            raise ValueError("appconfig 根对象必须是 JSON 对象")
        mapping = cast(dict[str, object], document)
        unknown = set(mapping) - _KNOWN_KEYS
        if unknown:
            raise ValueError(f"appconfig 含未知字段: {sorted(unknown)}")
        _validate_existing_fields(mapping)
        result = {
            "branch": config.branch,
            "release_bundle_id": config.release_bundle_id,
            "release_entry": config.release_entry,
            "version_code": config.version_code,
            "version_name": config.version_name,
        }
        return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    @staticmethod
    def write(path: Path, config: WindowsAppConfig) -> None:
        """将 appconfig 规范字节通过同目录临时文件原子写入目标。"""
        if not isinstance(path, Path):
            raise TypeError("path 必须是 Path")
        body = WindowsAppConfigTransformer.transform(b"{}", config)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_bytes(body)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """把 JSON 键值对转为映射并在解析时拒绝重复键。"""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"appconfig 存在重复键: {key}")
        result[key] = value
    return result


def _validate_existing_fields(mapping: dict[str, object]) -> None:
    """校验源 JSON 中已存在的白名单字段类型，避免静默吞掉损坏数据。"""
    for key in ("branch", "release_bundle_id", "release_entry", "version_name"):
        if key in mapping and not isinstance(mapping[key], str):
            raise ValueError(f"appconfig.{key} 必须是字符串")
    if "version_code" in mapping and (
        not isinstance(mapping["version_code"], int) or isinstance(mapping["version_code"], bool)
    ):
        raise ValueError("appconfig.version_code 必须是 int")


__all__ = ["WindowsAppConfig", "WindowsAppConfigTransformer"]
