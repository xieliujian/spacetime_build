"""IL2CPP 构建输入的内容寻址缓存键生成器。"""

from __future__ import annotations

import hashlib

from core.manifest_codec import canonical_json_bytes
from services.il2cpp.model import Il2CppBuildRequest


class Il2CppCacheKeyFactory:
    """从不含 request ID 和机器路径的规范输入生成 SHA-256 缓存键。"""

    @staticmethod
    def create(
        request: Il2CppBuildRequest,
        *,
        command_template_version: str,
        environment: tuple[tuple[str, str], ...] = (),
        toolchain_versions: tuple[tuple[str, str], ...] = (),
    ) -> str:
        """计算 IL2CPP 可复现身份，集合输入按 UTF-8 键排序。"""
        if not isinstance(request, Il2CppBuildRequest):
            raise TypeError("request 必须是 Il2CppBuildRequest")
        _validate_text(command_template_version, "command_template_version")
        normalized_environment = _normalize_pairs(environment, "environment")
        normalized_toolchains = _normalize_pairs(toolchain_versions, "toolchain_versions")
        document = {
            "architecture": request.architecture,
            "command_template_version": command_template_version,
            "environment": [list(item) for item in normalized_environment],
            "input_snapshot": {
                "locator": request.input_snapshot.locator,
                "sha256": request.input_snapshot.sha256,
                "size": request.input_snapshot.size,
            },
            "mode": request.mode.value,
            "platform": request.platform.value,
            "protection_policy": request.protection_policy,
            "schema_version": 1,
            "toolchain_digest": request.toolchain_digest,
            "toolchain_versions": [list(item) for item in normalized_toolchains],
            "unity_version": request.unity_version,
        }
        return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _validate_text(value: object, field_name: str) -> str:
    """校验缓存身份文本非空且不含控制字符。"""
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 必须是非空且无控制字符字符串")
    return value


def _normalize_pairs(
    pairs: tuple[tuple[str, str], ...],
    field_name: str,
) -> tuple[tuple[str, str], ...]:
    """校验并稳定排序环境或工具链版本键值集合。"""
    if not isinstance(pairs, tuple):
        raise TypeError(f"{field_name} 必须是 tuple")
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in pairs:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(f"{field_name} 的每一项必须是二元 tuple")
        key, value = item
        _validate_text(key, f"{field_name} key")
        _validate_text(value, f"{field_name} value")
        folded = key.casefold()
        if folded in seen:
            raise ValueError(f"{field_name} 存在重复 key: {key}")
        seen.add(folded)
        normalized.append((key, value))
    return tuple(sorted(normalized, key=lambda item: item[0].encode("utf-8")))


__all__ = ["Il2CppCacheKeyFactory"]
