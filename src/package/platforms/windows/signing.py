"""Windows Authenticode 签名的结构化阶段计划。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from package.platforms.windows.model import (
    HardwareTokenSigningOptions,
    PfxSigningOptions,
    WindowsPackageOptions,
    WindowsProfile,
)


class WindowsSigningStage(Enum):
    """需要独立签名和验证的 Windows 产物阶段。"""

    PAYLOAD = "payload"
    INSTALLER = "installer"


@dataclass(frozen=True, slots=True)
class WindowsSigningPlan:
    """不含秘密明文的 Windows 单阶段签名计划。"""

    stage: WindowsSigningStage
    files: tuple[Path, ...]
    algorithm: str
    signing: PfxSigningOptions | HardwareTokenSigningOptions | None
    timestamp_url: str | None


class WindowsSigningPlanner:
    """从 Windows 包体选项生成可审计的 Authenticode 阶段计划。"""

    @staticmethod
    def plan(
        options: WindowsPackageOptions,
        stage: WindowsSigningStage,
        files: tuple[Path, ...],
        *,
        timestamp_url: str | None = None,
        timestamp_allowlist: tuple[str, ...] = (),
    ) -> WindowsSigningPlan:
        """校验阶段、文件、SHA-256 和 timestamp allowlist 后冻结计划。"""
        if not isinstance(options, WindowsPackageOptions):
            raise TypeError("options 必须是 WindowsPackageOptions")
        if not isinstance(stage, WindowsSigningStage):
            raise TypeError("stage 必须是 WindowsSigningStage")
        if not isinstance(files, tuple) or not files:
            raise ValueError("files 必须是非空 tuple")
        normalized_files: list[Path] = []
        folded_paths: set[str] = set()
        for file_path in files:
            if not isinstance(file_path, Path) or not file_path.is_absolute():
                raise ValueError("签名文件必须是绝对 Path")
            folded = file_path.as_posix().casefold()
            if folded in folded_paths:
                raise ValueError("签名文件不得重复")
            folded_paths.add(folded)
            normalized_files.append(file_path)
        if not isinstance(timestamp_allowlist, tuple):
            raise TypeError("timestamp_allowlist 必须是 tuple")
        if not all(isinstance(value, str) for value in timestamp_allowlist):
            raise TypeError("timestamp_allowlist 每项必须是字符串")
        allowlist = tuple(sorted(set(timestamp_allowlist), key=lambda value: value.encode("utf-8")))
        if len(allowlist) != len(timestamp_allowlist):
            raise ValueError("timestamp_allowlist 不得重复")
        for url in allowlist:
            _validate_timestamp_url(url)
        if timestamp_url is not None:
            _validate_timestamp_url(timestamp_url)
            if timestamp_url not in allowlist:
                raise ValueError("timestamp URL 不在 allowlist 中")
        if options.profile is WindowsProfile.PRODUCTION and options.signing is None:
            raise ValueError("production 签名计划不允许 unsigned")
        return WindowsSigningPlan(
            stage,
            tuple(sorted(normalized_files, key=lambda path: path.as_posix().encode("utf-8"))),
            "sha256",
            options.signing,
            timestamp_url,
        )


def _validate_timestamp_url(value: str) -> None:
    """校验 timestamp 地址使用 HTTPS 且不含用户信息或控制字符。"""
    if not isinstance(value, str) or any(ord(char) < 0x20 for char in value):
        raise ValueError("timestamp URL 必须是无控制字符字符串")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("timestamp URL 必须是无用户信息的 HTTPS URL")


__all__ = ["WindowsSigningPlan", "WindowsSigningPlanner", "WindowsSigningStage"]
