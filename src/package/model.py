"""客户端包体请求、产物和运行记录模型。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum

from core.artifacts import BlobRef
from core.manifest_codec import canonical_json_bytes
from core.platforms import BuildPlatform

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_APPLICATION_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")


class PackageStatus(Enum):
    """包体执行记录的阶段状态。"""

    PLANNED = "planned"
    PREPARING = "preparing"
    EXPORTING = "exporting"
    BUILDING = "building"
    SIGNING = "signing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class PackageRequest:
    """描述一次可复现客户端包体请求。"""

    platform: BuildPlatform
    source_revision: str
    release_bundle_id: str
    unity_version: str
    application_id: str
    version_name: str
    version_code: int
    profile: str
    request_id: str = field(init=False)

    def __post_init__(self) -> None:
        """校验固定输入并根据请求 payload 计算确定性身份。"""
        if not isinstance(self.platform, BuildPlatform):
            raise TypeError("platform 必须是 BuildPlatform")
        for name in ("source_revision", "unity_version", "version_name", "profile"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or any(char in value for char in "\r\n"):
                raise ValueError(f"{name} 必须是非空无换行字符串")
        if self.source_revision.casefold() in {"head", "latest", "tip"}:
            raise ValueError("source_revision 必须固定，不能使用浮动 revision")
        if (
            not isinstance(self.release_bundle_id, str)
            or _SHA256_PATTERN.fullmatch(self.release_bundle_id) is None
        ):
            raise ValueError("release_bundle_id 必须是 64 位小写 SHA256")
        if (
            not isinstance(self.application_id, str)
            or _APPLICATION_ID_PATTERN.fullmatch(self.application_id) is None
        ):
            raise ValueError("application_id 不是合法的点分标识")
        if (
            not isinstance(self.version_code, int)
            or isinstance(self.version_code, bool)
            or not 1 <= self.version_code <= 2**31 - 1
        ):
            raise ValueError("version_code 必须是正 Int32")
        payload = {
            "application_id": self.application_id,
            "platform": self.platform.value,
            "profile": self.profile,
            "release_bundle_id": self.release_bundle_id,
            "source_revision": self.source_revision,
            "unity_version": self.unity_version,
            "version_code": self.version_code,
            "version_name": self.version_name,
        }
        object.__setattr__(
            self, "request_id", hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        )


@dataclass(frozen=True, slots=True)
class PackageArtifact:
    """描述一个已提交到 CAS 的包体产物。"""

    logical_path: str
    blob: BlobRef
    kind: str

    def __post_init__(self) -> None:
        """校验包体产物路径、Blob 和类型标签。"""
        if (
            not isinstance(self.logical_path, str)
            or not self.logical_path
            or "\\" in self.logical_path
        ):
            raise ValueError("logical_path 必须是非空正斜杠路径")
        if not isinstance(self.blob, BlobRef):
            raise TypeError("blob 必须是 BlobRef")
        if (
            not isinstance(self.kind, str)
            or not self.kind
            or any(char in self.kind for char in "\r\n")
        ):
            raise ValueError("kind 必须是非空无换行字符串")


@dataclass(frozen=True, slots=True)
class PackageExecutionRecord:
    """记录包体执行运行态，与确定性 PackageManifest 分离。"""

    execution_id: str
    package_request_id: str
    status: PackageStatus
    error: str | None

    def __post_init__(self) -> None:
        """校验运行记录身份、状态和错误语义。"""
        if not isinstance(self.execution_id, str) or not self.execution_id:
            raise ValueError("execution_id 必须是非空字符串")
        if _SHA256_PATTERN.fullmatch(self.package_request_id) is None:
            raise ValueError("package_request_id 必须是 64 位小写 SHA256")
        if not isinstance(self.status, PackageStatus):
            raise TypeError("status 必须是 PackageStatus")
        if self.error is not None and (not isinstance(self.error, str) or not self.error):
            raise ValueError("error 必须是非空字符串或 None")
