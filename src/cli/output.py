"""CLI 成功/失败结果的稳定人类与 JSON 输出。

输出层只接收已经由 application 处理的摘要对象；它会递归转换常见 dataclass、Enum
和 mapping，并统一使用 observability 的文本脱敏函数。输出不包含 traceback、秘密
原文或可执行对象表示。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from enum import Enum
from typing import cast

from observability.redaction import redact_text

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "api_key",
    "authorization",
    "cookie",
)


def _safe_value(value: object) -> object:
    """将结果转换为可 JSON 编码且不包含原始秘密的值。"""
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _safe_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        typed_value = cast(Mapping[object, object], value)
        for key, item in typed_value.items():
            key_text = str(key)
            if any(part in key_text.casefold() for part in _SENSITIVE_KEY_PARTS):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _safe_value(item)
        return result
    if isinstance(value, (tuple, list, set, frozenset)):
        sequence = cast(tuple[object, ...] | list[object] | set[object] | frozenset[object], value)
        return [_safe_value(item) for item in sequence]
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(type(value).__name__)


def render_success(value: object, *, json_mode: bool) -> str:
    """渲染成功摘要，不包含调试 traceback。"""
    safe = _safe_value(value)
    if json_mode:
        return json.dumps(safe, ensure_ascii=False, sort_keys=True)
    if isinstance(safe, dict):
        safe_dict = cast(dict[str, object], safe)
        return "\n".join(f"{key}: {safe_dict[key]}" for key in sorted(safe_dict))
    return str(safe)


def render_error(
    error: BaseException,
    *,
    code: int,
    json_mode: bool,
    run_id: str | None = None,
    log_locator: str | None = None,
) -> str:
    """渲染稳定错误对象并脱敏错误消息。

    参数：
        error: 原始异常；只读取类型名和脱敏字符串。
        code: 已映射的公开退出码。
        json_mode: 是否输出单行 JSON。
        run_id、log_locator: 可选公开追踪字段。

    返回：
        不含 traceback 和秘密的 JSON 或人类文本。

    异常与副作用：
        不重新抛出错误，不写日志或文件。
    """
    payload = {
        "code": code,
        "error_type": type(error).__name__,
        "log_locator": log_locator,
        "message": redact_text(str(error)),
        "run_id": run_id,
    }
    if json_mode:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return f"[{code}] {payload['error_type']}: {payload['message']}"


__all__ = ["render_error", "render_success"]
