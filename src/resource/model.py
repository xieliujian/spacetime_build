"""资源构建的不可变输入与枚举模型。

本模块锁定正式版本第一期的十二类资源任务、Unity 项目角色和固定输入身份。
``ResourceVariant`` 始终从 ``release.entries`` 导入，避免主/低清语义出现第二个
枚举声明点。模型只做内存校验，不读取源码、不启动工具，也不修改工作区。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.platforms import BuildPlatform
from release.entries import ResourceVariant

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ResourceKind(Enum):
    """正式版本第一期支持的资源任务种类。

    职责：
        为任务注册、输出命名和审计记录提供稳定的资源分类。

    参数：
        无；成员值与任务名保持一致。

    返回：
        无；通过枚举成员访问。

    异常：
        非法名称由 ``Enum`` 标准机制抛出。

    约束与副作用：
        不包含规划中或本期排除的 story、低清、分包和 Redirect 类型。
    """

    CONFIG = "config"
    SHADER_VARIANT = "shader_variant"
    SHADER_BUNDLE = "shader_bundle"
    SCENE = "scene"
    MAP = "map"
    CHARACTER = "character"
    TEXTURE = "texture"
    UI = "ui"
    PARTICLE = "particle"
    AUDIO = "audio"
    VIDEO = "video"
    LUA = "lua"


class ResourceProjectRole(Enum):
    """资源任务使用的 Unity 工程角色。

    职责：
        将资源工程、Shader 工程和配置转换工程的选择纳入类型化操作身份。

    参数：
        无；值为稳定角色标签。

    返回：
        无；通过枚举成员访问。

    异常：
        非法名称由 ``Enum`` 标准机制抛出。

    约束与副作用：
        角色只描述请求，不执行 Unity 或改变源工程。
    """

    RESOURCE = "resource"
    SHADER = "shader"
    CONFIG = "config"


def _validate_identity(value: str, *, field_name: str) -> None:
    """校验快照和规则身份为安全、稳定的单值字符串。

    参数：
        value: 待校验身份。
        field_name: 错误消息中的字段名。

    返回：
        ``None``，表示身份可以进入任务模型。

    异常：
        空值、路径分隔符或控制字符会抛出 ``ValueError``。

    约束与副作用：
        纯函数；不要求身份必须是 SHA256，以兼容外部快照系统的稳定 ID。
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} 必须是非空字符串")
    if any(char in value for char in "/\\\r\n\t"):
        raise ValueError(f"{field_name} 不得包含路径分隔符或控制字符")


def _validate_optional_digest(value: str | None, *, field_name: str) -> None:
    """校验可选内容摘要的格式。

    参数：
        value: 可选摘要。
        field_name: 错误消息中的字段名。

    返回：
        ``None``，表示摘要为空或为小写 SHA256。

    异常：
        非空且不是 64 位小写十六进制字符串时抛出 ``ValueError``。

    约束与副作用：
        纯函数；不访问任何摘要存储。
    """
    if value is not None and _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} 必须是 64 位小写 SHA256 或 None")


@dataclass(frozen=True, slots=True)
class ResourceBuildInput:
    """固定一次资源任务的源码、资源快照和变体身份。

    职责：
        把任务执行所需的固定快照、平台、主/低清变体、规则版本和可选基线绑定为
        不可变输入，防止任务从可变发布目录读取数据。

    参数：
        source_snapshot_id: 已固定源码快照身份。
        resource_snapshot_id: 已固定资源输入快照身份。
        platform: 共用的 ``BuildPlatform``。
        variant: 从 ``release.entries`` 导入的唯一 ``ResourceVariant``。
        rule_version: 资源规则版本。
        baseline_manifest_id: 可选基线 BuildManifest SHA256。

    返回：
        无；通过字段读取固定输入。

    异常：
        字段类型、身份形式或摘要格式非法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        冻结对象；不读取文件、不访问发布目录、不执行外部工具。
    """

    source_snapshot_id: str
    resource_snapshot_id: str
    platform: BuildPlatform
    variant: ResourceVariant
    rule_version: str
    baseline_manifest_id: str | None

    def __post_init__(self) -> None:
        """校验资源输入的类型和身份不变量。

        参数：
            无；读取实例字段。

        返回：
            ``None``，表示输入可用于任务规划。

        异常：
            平台/变体类型错误或身份字段无效时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            仅内存校验，不规范化或修改任何外部状态。
        """
        if not isinstance(self.platform, BuildPlatform):
            raise TypeError("platform 必须是 BuildPlatform")
        if not isinstance(self.variant, ResourceVariant):
            raise TypeError("variant 必须是 release.entries.ResourceVariant")
        _validate_identity(self.source_snapshot_id, field_name="source_snapshot_id")
        _validate_identity(self.resource_snapshot_id, field_name="resource_snapshot_id")
        _validate_identity(self.rule_version, field_name="rule_version")
        _validate_optional_digest(self.baseline_manifest_id, field_name="baseline_manifest_id")
