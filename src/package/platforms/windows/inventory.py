"""Windows 内部文件 inventory 的模型、收集器和确定性 JSON codec。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from package.platforms.windows.path_rules import validate_windows_relative_path

_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _validate_path(value: str) -> str:
    """校验 inventory 逻辑路径为安全相对正斜杠路径。"""
    return validate_windows_relative_path(value, label="inventory logical_path")


def _validate_version(value: str) -> str:
    """校验 inventory 版本摘要为非空无控制字符文本。"""
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError("inventory version 必须是非空且无控制字符字符串")
    return value


@dataclass(frozen=True, slots=True)
class WindowsInventoryEntry:
    """记录一个文件的逻辑路径、大小、SHA256 和包版本。"""

    logical_path: str
    size: int
    sha256: str
    version: str

    def __post_init__(self) -> None:
        """校验路径、非负大小、小写 SHA256 和版本摘要。"""
        _validate_path(self.logical_path)
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise ValueError("inventory size 必须是非负整数")
        if not isinstance(self.sha256, str) or _SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise ValueError("inventory sha256 必须是 64 位小写 SHA256")
        _validate_version(self.version)


@dataclass(frozen=True, slots=True)
class WindowsInventory:
    """版本化的 Windows 内部文件清单，不表示旧客户端六字段协议。"""

    package_version: str
    entries: tuple[WindowsInventoryEntry, ...]

    def __post_init__(self) -> None:
        """校验版本、非空条目集合并按 UTF-8 路径稳定排序。"""
        _validate_version(self.package_version)
        if not isinstance(self.entries, tuple):
            raise TypeError("entries 必须是 tuple")
        normalized = tuple(sorted(self.entries, key=lambda item: item.logical_path.encode("utf-8")))
        if not all(isinstance(item, WindowsInventoryEntry) for item in normalized):
            raise TypeError("entries 的每一项必须是 WindowsInventoryEntry")
        folded_paths: set[str] = set()
        for item in normalized:
            folded = item.logical_path.casefold()
            if folded in folded_paths:
                raise ValueError(f"inventory 存在大小写折叠重复路径: {item.logical_path}")
            folded_paths.add(folded)
        object.__setattr__(self, "entries", normalized)


class WindowsInventoryCollector:
    """从固定 payload 根收集普通文件摘要，不跟随符号链接。"""

    @staticmethod
    def collect(
        root: Path,
        package_version: str,
        *,
        excluded_directories: tuple[str, ...] = (),
    ) -> WindowsInventory:
        """按逻辑路径顺序计算文件大小和 SHA256，并剪枝显式排除目录。"""
        if not isinstance(root, Path) or not root.is_absolute() or not root.is_dir():
            raise ValueError("inventory root 必须是已存在的绝对目录")
        _validate_version(package_version)
        if not isinstance(excluded_directories, tuple):
            raise TypeError("excluded_directories 必须是 tuple")
        excluded = {_validate_path(item).casefold() for item in excluded_directories}
        entries: list[WindowsInventoryEntry] = []
        for candidate in sorted(
            root.rglob("*"), key=lambda path: path.relative_to(root).as_posix().encode("utf-8")
        ):
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                raise ValueError(f"inventory 不允许符号链接: {relative}")
            if candidate.is_dir():
                continue
            if _is_excluded(relative, excluded):
                continue
            if not candidate.is_file():
                raise ValueError(f"inventory 只支持普通文件: {relative}")
            content = candidate.read_bytes()
            entries.append(
                WindowsInventoryEntry(
                    relative,
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                    package_version,
                )
            )
        return WindowsInventory(package_version, tuple(entries))


class WindowsInventoryCodec:
    """编码、写入、读取和重建 Windows inventory。"""

    @staticmethod
    def from_entries(
        package_version: str,
        entries: tuple[WindowsInventoryEntry, ...],
    ) -> WindowsInventory:
        """从已计算条目创建经过重复路径检查的 inventory。"""
        return WindowsInventory(package_version, entries)

    @staticmethod
    def encode(inventory: WindowsInventory) -> bytes:
        """将 inventory 编码为固定 schema、排序字段和无空白 JSON。"""
        if not isinstance(inventory, WindowsInventory):
            raise TypeError("inventory 必须是 WindowsInventory")
        document = {
            "entries": [
                {
                    "logical_path": item.logical_path,
                    "sha256": item.sha256,
                    "size": item.size,
                    "version": item.version,
                }
                for item in inventory.entries
            ],
            "package_version": inventory.package_version,
            "schema_version": _SCHEMA_VERSION,
        }
        return json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def write(inventory: WindowsInventory, path: Path) -> None:
        """以同目录临时文件加 replace 原子写入 inventory。"""
        if not isinstance(path, Path):
            raise TypeError("path 必须是 Path")
        body = WindowsInventoryCodec.encode(inventory)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_bytes(body)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def read(path: Path) -> WindowsInventory:
        """读取 JSON 并重新构造 inventory，拒绝未知字段、重复键和陈旧结构。"""
        if not isinstance(path, Path):
            raise TypeError("path 必须是 Path")
        try:
            document = json.loads(
                path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicates
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("inventory JSON 无法解析") from exc
        if not isinstance(document, dict):
            raise ValueError("inventory 根对象必须是对象")
        data = cast(dict[str, object], document)
        if set(data) != {"entries", "package_version", "schema_version"}:
            raise ValueError("inventory 根字段不符合 schema")
        if data["schema_version"] != _SCHEMA_VERSION:
            raise ValueError("inventory schema_version 不支持")
        raw_entries = data["entries"]
        if not isinstance(raw_entries, list):
            raise ValueError("inventory.entries 必须是列表")
        entries: list[WindowsInventoryEntry] = []
        for raw in cast(list[object], raw_entries):
            if not isinstance(raw, dict):
                raise ValueError("inventory entry 字段不符合 schema")
            raw_entry = cast(dict[str, object], raw)
            if set(raw_entry) != {
                "logical_path",
                "sha256",
                "size",
                "version",
            }:
                raise ValueError("inventory entry 字段不符合 schema")
            entries.append(
                WindowsInventoryEntry(
                    _require_string(raw_entry["logical_path"], "logical_path"),
                    _require_int(raw_entry["size"], "size"),
                    _require_string(raw_entry["sha256"], "sha256"),
                    _require_string(raw_entry["version"], "version"),
                )
            )
        return WindowsInventory(
            _require_string(data["package_version"], "package_version"), tuple(entries)
        )


def _is_excluded(relative: str, excluded: set[str]) -> bool:
    """判断文件是否位于显式排除目录之下。"""
    parts = relative.split("/")[:-1]
    for index in range(1, len(parts) + 1):
        if "/".join(parts[:index]).casefold() in excluded:
            return True
    return False


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """在 JSON 解析阶段拒绝重复键。"""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"inventory JSON 存在重复键: {key}")
        result[key] = value
    return result


def _require_string(value: object, field_name: str) -> str:
    """读取 JSON 非空字符串字段。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"inventory {field_name} 必须是非空字符串")
    return value


def _require_int(value: object, field_name: str) -> int:
    """读取 JSON 非布尔整数字段。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"inventory {field_name} 必须是 int")
    return value


__all__ = [
    "WindowsInventory",
    "WindowsInventoryCodec",
    "WindowsInventoryCollector",
    "WindowsInventoryEntry",
]
