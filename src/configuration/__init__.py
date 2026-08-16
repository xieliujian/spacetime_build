"""构建系统配置层公共 API。

本包导出不可变强类型配置模型以及无状态读写服务。导入本包不会加载配置文件、
访问秘密服务、检查工具或触发构建与发布副作用。
"""

from configuration.loader import (
    ConfigSnapshot,
    canonical_toml_bytes,
    decode_build_config,
    load_layered_config,
    merge_config_layers,
)
from configuration.model import (
    BuildConfig,
    LoggingConfig,
    ObjectStoreConfig,
    ProfileConfig,
    ProjectConfig,
    PublishLayoutConfig,
    ResolvedBuildConfig,
    SecretRef,
    TaskConfig,
    UnityToolConfig,
    VersionControlConfig,
)
from configuration.service import BuildConfigService
from configuration.validator import ValidationIssue, ValidationReport, validate_build_config

__all__ = [
    "BuildConfig",
    "BuildConfigService",
    "ConfigSnapshot",
    "LoggingConfig",
    "ObjectStoreConfig",
    "ProfileConfig",
    "ProjectConfig",
    "PublishLayoutConfig",
    "ResolvedBuildConfig",
    "SecretRef",
    "TaskConfig",
    "UnityToolConfig",
    "ValidationIssue",
    "ValidationReport",
    "VersionControlConfig",
    "canonical_toml_bytes",
    "decode_build_config",
    "load_layered_config",
    "merge_config_layers",
    "validate_build_config",
]
