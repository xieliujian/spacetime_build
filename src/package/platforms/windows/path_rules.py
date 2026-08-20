"""Windows 包体逻辑路径的统一安全规则。

本模块把 portable ZIP、Player workspace layout 和内部 inventory 共同使用的
Windows 路径语义集中到一个纯函数中。逻辑路径只允许正斜杠相对路径，并拒绝
NTFS 保留设备名、alternate data stream 冒号、控制字符以及 Windows 会自动裁剪
的尾随点和空格，避免三个边界对同一文件产生不同解释。
"""

from __future__ import annotations

import re

_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
_INVALID_COMPONENT_CHARACTERS = frozenset('<>:"|?*')
_RESERVED_DEVICE_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


def validate_windows_relative_path(
    value: str,
    *,
    label: str,
    allow_trailing_slash: bool = False,
) -> str:
    """校验并返回一个 Windows 安全的正斜杠相对逻辑路径。

    参数：
        value: 待校验的路径字符串；目录标记是否允许由 ``allow_trailing_slash``
            控制。
        label: 错误信息中的字段名称，帮助调用方保留领域上下文。
        allow_trailing_slash: 是否允许一个或多个目录尾斜杠；尾斜杠不参与组件校验。

    返回：
        原始路径字符串；校验不会修改调用方的目录标记。

    异常：
        类型错误、绝对路径、反斜杠、空路径段、保留设备名、特殊字符或 NTFS
        会裁剪的尾随字符会抛出 ``ValueError``。

    约束与副作用：
        纯内存校验，不访问文件系统，不解析当前操作系统的本地路径。
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} 必须是非空字符串")
    normalized = value.rstrip("/") if allow_trailing_slash else value
    if not normalized:
        raise ValueError(f"{label} 不得只有目录尾斜杠")
    if (
        normalized.startswith("/")
        or _DRIVE_PATTERN.match(normalized) is not None
        or "\\" in normalized
    ):
        raise ValueError(f"{label} 必须是正斜杠相对路径")
    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(f"{label} 含非法路径段")
    for segment in segments:
        if any(char in _INVALID_COMPONENT_CHARACTERS or ord(char) < 0x20 for char in segment):
            raise ValueError(f"{label} 含 Windows 非法字符")
        if segment.endswith((".", " ")):
            raise ValueError(f"{label} 组件不得以点或空格结尾")
        if segment.split(".", 1)[0].casefold() in _RESERVED_DEVICE_NAMES:
            raise ValueError(f"{label} 使用 Windows 保留设备名")
    return value


__all__ = ["validate_windows_relative_path"]
