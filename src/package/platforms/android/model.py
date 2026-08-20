"""Android 包体选项和 ABI 模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_APPLICATION_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")


class AndroidAbi(Enum):
    """Android native ABI 稳定枚举。"""

    ARMEABI_V7A = "armeabi-v7a"
    ARM64_V8A = "arm64-v8a"
    X86 = "x86"
    X86_64 = "x86_64"


class AndroidOutputKind(Enum):
    """Android 输出形态。"""

    APK = "apk"
    AAB = "aab"
    PROJECT = "project"


class AndroidBuildType(Enum):
    """Gradle 构建类型。"""

    DEBUG = "debug"
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class AndroidPackageOptions:
    """描述 Android 包体的输出类型、ABI、应用标识和版本。"""

    output_kind: AndroidOutputKind
    abis: tuple[AndroidAbi, ...]
    build_type: AndroidBuildType
    application_id: str
    version_code: int

    def __post_init__(self) -> None:
        """校验枚举、ABI 集合、application ID 和正 Int32 版本号。"""
        if not isinstance(self.output_kind, AndroidOutputKind):
            raise TypeError("output_kind 必须是 AndroidOutputKind")
        if not isinstance(self.build_type, AndroidBuildType):
            raise TypeError("build_type 必须是 AndroidBuildType")
        if not isinstance(self.abis, tuple) or not self.abis:
            raise ValueError("abis 必须是非空 tuple")
        if not all(isinstance(abi, AndroidAbi) for abi in self.abis):
            raise TypeError("abis 的每一项必须是 AndroidAbi")
        normalized = tuple(sorted(set(self.abis), key=lambda abi: abi.value.encode("utf-8")))
        object.__setattr__(self, "abis", normalized)
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
