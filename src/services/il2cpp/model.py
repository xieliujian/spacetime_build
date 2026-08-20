"""IL2CPP 构建请求、执行计划和结果的不可变领域模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from observability.redaction import redact_text

_IDENTITY_PATTERN = re.compile(r"^[^\x00-\x1f\r\n]+$")
_CAS_LOCATOR_PATTERN = re.compile(r"^(?:blobs/[0-9a-f]{64}|sha256:[0-9a-f]{64})$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class Il2CppExecutionMode(Enum):
    """IL2CPP 在本地工具链或受控 CI 节点上的执行模式。"""

    LOCAL = "local"
    REMOTE = "remote"


class Il2CppStatus(Enum):
    """IL2CPP 服务运行记录的稳定状态。"""

    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _validate_identity(value: object, field_name: str) -> str:
    """校验公开身份文本非空且不含控制字符。"""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str")
    if not value or _IDENTITY_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} 必须是非空且无控制字符的字符串")
    return value


@dataclass(frozen=True, slots=True)
class Il2CppBuildRequest:
    """描述一次可复现 IL2CPP 输入和执行模式，不携带机器路径或秘密。"""

    request_id: str
    platform: BuildPlatform
    architecture: str
    input_snapshot: BlobRef
    unity_version: str
    toolchain_digest: str
    mode: Il2CppExecutionMode
    protection_policy: str | None

    def __post_init__(self) -> None:
        """校验 IL2CPP 输入身份、平台、Blob 和可选保护策略。"""
        _validate_identity(self.request_id, "request_id")
        if not isinstance(self.platform, BuildPlatform):
            raise TypeError("platform 必须是 BuildPlatform")
        _validate_identity(self.architecture, "architecture")
        if not isinstance(self.input_snapshot, BlobRef):
            raise TypeError("input_snapshot 必须是 BlobRef")
        _validate_identity(self.unity_version, "unity_version")
        _validate_identity(self.toolchain_digest, "toolchain_digest")
        if not isinstance(self.mode, Il2CppExecutionMode):
            raise TypeError("mode 必须是 Il2CppExecutionMode")
        if self.protection_policy is not None:
            _validate_identity(self.protection_policy, "protection_policy")


@dataclass(frozen=True, slots=True)
class Il2CppExecutionPlan:
    """绑定请求、隔离 workspace、输出 locator 和内容寻址缓存键的计划。"""

    request: Il2CppBuildRequest
    workspace: Path
    output_locator: str
    cache_key: str

    def __post_init__(self) -> None:
        """校验计划路径、请求类型、输出 locator 和缓存键。"""
        if not isinstance(self.request, Il2CppBuildRequest):
            raise TypeError("request 必须是 Il2CppBuildRequest")
        if not isinstance(self.workspace, Path) or not self.workspace.is_absolute():
            raise ValueError("workspace 必须是绝对 Path")
        if (
            not isinstance(self.output_locator, str)
            or _CAS_LOCATOR_PATTERN.fullmatch(self.output_locator) is None
        ):
            raise ValueError("output_locator 必须是完整内容寻址 locator")
        if not isinstance(self.cache_key, str) or _SHA256_PATTERN.fullmatch(self.cache_key) is None:
            raise ValueError("cache_key 必须是 64 位小写 SHA256")


@dataclass(frozen=True, slots=True)
class Il2CppBuildResult:
    """记录 IL2CPP 状态、可选输出 Blob 和已脱敏诊断。"""

    request_id: str
    status: Il2CppStatus
    output_snapshot: BlobRef | None
    diagnostic: str

    def __post_init__(self) -> None:
        """校验结果状态与输出/诊断组合，并统一脱敏诊断文本。"""
        _validate_identity(self.request_id, "request_id")
        if not isinstance(self.status, Il2CppStatus):
            raise TypeError("status 必须是 Il2CppStatus")
        if self.output_snapshot is not None and not isinstance(self.output_snapshot, BlobRef):
            raise TypeError("output_snapshot 必须是 BlobRef 或 None")
        if not isinstance(self.diagnostic, str):
            raise TypeError("diagnostic 必须是 str")
        diagnostic = redact_text(self.diagnostic)
        object.__setattr__(self, "diagnostic", diagnostic)
        if self.status is Il2CppStatus.SUCCEEDED:
            if self.output_snapshot is None:
                raise ValueError("SUCCEEDED 结果必须提供 output_snapshot")
            return
        if self.output_snapshot is not None:
            raise ValueError("非 SUCCEEDED 结果不得携带 output_snapshot")
        if self.status in {Il2CppStatus.FAILED, Il2CppStatus.CANCELLED} and not diagnostic.strip():
            raise ValueError("失败或取消结果必须提供非空 diagnostic")


__all__ = [
    "Il2CppBuildRequest",
    "Il2CppBuildResult",
    "Il2CppExecutionMode",
    "Il2CppExecutionPlan",
    "Il2CppStatus",
]
