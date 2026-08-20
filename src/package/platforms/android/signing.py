"""Android 签名请求的安全计划模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from configuration.model import SecretRef
from package.platforms.android.model import AndroidOutputKind, AndroidPackageOptions

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AndroidSecretDelivery(Enum):
    """Android 签名秘密的受控传递方式。"""

    TEMP_FILE = "temp_file"
    ENVIRONMENT = "environment"


@dataclass(frozen=True, slots=True)
class AndroidSigningPlan:
    """不含秘密明文的 Android 签名计划。"""

    output_kind: AndroidOutputKind
    tool: str
    secret_ref: SecretRef
    certificate_fingerprint: str
    delivery: AndroidSecretDelivery


class AndroidSigningPlanner:
    """根据输出类型创建安全签名计划，不解析 SecretRef。"""

    @staticmethod
    def plan(
        options: AndroidPackageOptions,
        secret_ref: SecretRef,
        certificate_fingerprint: str,
        *,
        delivery: AndroidSecretDelivery = AndroidSecretDelivery.TEMP_FILE,
    ) -> AndroidSigningPlan:
        """生成 APK/AAB 差异化签名工具和秘密传递声明。

        参数：
            options: 已校验 Android 包体选项。
            secret_ref: keystore 或签名材料的脱敏引用。
            certificate_fingerprint: 公开 SHA256 证书指纹。
            delivery: 只能使用受控临时文件或环境绑定。

        返回：
            不携带秘密值的 ``AndroidSigningPlan``。

        异常：
            project 输出、弱摘要、引用或传递方式非法时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            纯内存计划；不获取秘密、不创建临时文件、不执行 apksigner/Gradle。
        """
        if not isinstance(options, AndroidPackageOptions):
            raise TypeError("options 必须是 AndroidPackageOptions")
        if options.output_kind is AndroidOutputKind.PROJECT:
            raise ValueError("project 输出不需要签名")
        if not isinstance(secret_ref, SecretRef):
            raise TypeError("secret_ref 必须是 SecretRef")
        if (
            not isinstance(certificate_fingerprint, str)
            or _SHA256_PATTERN.fullmatch(certificate_fingerprint) is None
        ):
            raise ValueError("certificate_fingerprint 必须是 64 位小写 SHA256")
        if not isinstance(delivery, AndroidSecretDelivery):
            raise TypeError("delivery 必须是 AndroidSecretDelivery")
        tool = (
            "apksigner" if options.output_kind is AndroidOutputKind.APK else "gradle-signing-config"
        )
        return AndroidSigningPlan(
            options.output_kind, tool, secret_ref, certificate_fingerprint, delivery
        )
