"""Windows 客户端包体选项、签名材料和安全边界模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from configuration.model import SecretRef

_THUMBPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class WindowsArchitecture(Enum):
    """Windows Player 支持的目标架构。"""

    X86 = "x86"
    X86_64 = "x86_64"


class WindowsOutputKind(Enum):
    """Windows 包体输出形态。"""

    PORTABLE = "portable"
    INSTALLER = "installer"


class WindowsInstallScope(Enum):
    """安装器的安装范围。"""

    USER = "user"
    MACHINE = "machine"


class WindowsRuntimePolicy(Enum):
    """Windows 运行库的部署策略。"""

    BUNDLED = "bundled"
    SYSTEM = "system"


class WindowsProfile(Enum):
    """区分可允许 unsigned 的测试 profile 与生产 profile。"""

    TEST = "test"
    PRODUCTION = "production"


def _validate_thumbprint(value: str) -> None:
    """校验公开 SHA-256 证书指纹，不读取签名材料。"""
    if not isinstance(value, str) or _THUMBPRINT_PATTERN.fullmatch(value) is None:
        raise ValueError("certificate_thumbprint 必须是 64 位小写 SHA256")


def _validate_text(value: str, field_name: str) -> None:
    """校验公开标识非空且不含控制字符。"""
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 必须是非空且无控制字符的字符串")


@dataclass(frozen=True, slots=True)
class PfxSigningOptions:
    """Windows PFX 签名所需的 SecretRef 和公开证书摘要。"""

    pfx_ref: SecretRef
    password_ref: SecretRef
    certificate_thumbprint: str

    def __post_init__(self) -> None:
        """校验 PFX、密码引用和不含秘密的证书指纹。"""
        if not isinstance(self.pfx_ref, SecretRef):
            raise TypeError("pfx_ref 必须是 SecretRef")
        if not isinstance(self.password_ref, SecretRef):
            raise TypeError("password_ref 必须是 SecretRef")
        _validate_thumbprint(self.certificate_thumbprint)


@dataclass(frozen=True, slots=True)
class HardwareTokenSigningOptions:
    """Windows 硬件令牌签名所需的 provider、设备和 PIN 引用。"""

    provider_name: str
    device_selector: str
    certificate_thumbprint: str
    pin_ref: SecretRef

    def __post_init__(self) -> None:
        """校验硬件令牌公开选择器、证书指纹和 PIN 引用。"""
        _validate_text(self.provider_name, "provider_name")
        _validate_text(self.device_selector, "device_selector")
        _validate_thumbprint(self.certificate_thumbprint)
        if not isinstance(self.pin_ref, SecretRef):
            raise TypeError("pin_ref 必须是 SecretRef")


@dataclass(frozen=True, slots=True)
class WindowsPackageOptions:
    """描述 Windows 包体架构、输出、运行库、签名和安装策略。"""

    architecture: WindowsArchitecture
    outputs: tuple[WindowsOutputKind, ...]
    install_scope: WindowsInstallScope
    runtime_policy: WindowsRuntimePolicy
    signing: PfxSigningOptions | HardwareTokenSigningOptions | None
    profile: WindowsProfile

    def __post_init__(self) -> None:
        """校验枚举、输出集合和 unsigned 只允许测试 profile 的不变量。"""
        for field_name, enum_type in (
            ("architecture", WindowsArchitecture),
            ("install_scope", WindowsInstallScope),
            ("runtime_policy", WindowsRuntimePolicy),
            ("profile", WindowsProfile),
        ):
            if not isinstance(getattr(self, field_name), enum_type):
                raise TypeError(f"{field_name} 类型非法")
        if not isinstance(self.outputs, tuple) or not self.outputs:
            raise ValueError("outputs 必须是非空 tuple")
        if not all(isinstance(item, WindowsOutputKind) for item in self.outputs):
            raise TypeError("outputs 的每一项必须是 WindowsOutputKind")
        normalized = tuple(sorted(set(self.outputs), key=lambda item: item.value.encode("utf-8")))
        object.__setattr__(self, "outputs", normalized)
        if self.signing is not None and not isinstance(
            self.signing, (PfxSigningOptions, HardwareTokenSigningOptions)
        ):
            raise TypeError("signing 类型非法")
        if self.profile is WindowsProfile.PRODUCTION and self.signing is None:
            raise ValueError("production profile 不允许 unsigned 包体")


__all__ = [
    "HardwareTokenSigningOptions",
    "PfxSigningOptions",
    "WindowsArchitecture",
    "WindowsInstallScope",
    "WindowsOutputKind",
    "WindowsPackageOptions",
    "WindowsProfile",
    "WindowsRuntimePolicy",
]
