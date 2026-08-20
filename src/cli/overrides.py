"""环境变量与命令行配置覆盖的显式解析器。

覆盖器只接收白名单字段，不把完整 ``os.environ`` 或 argparse namespace 传入业务层。
优先级为 CLI > 环境变量 > TOML/已解析 Profile；空环境变量视为未提供。所有类型
转换在边界完成，秘密仅保留 ``SecretRef`` 引用并在诊断快照中脱敏。
"""

from __future__ import annotations

from typing import Mapping

from cli.config import BuildConfig, ConfigError, ConfigSnapshot, _ConfigSource, _config_digest
from configuration.model import SecretRef


class OverrideError(ConfigError):
    """表示环境变量或 CLI 覆盖字段非法、冲突或类型错误。"""


_ENV_FIELDS = {
    "SPACETIME_BUILD_PROFILE": "profile",
    "SPACETIME_BUILD_RESOURCES": "resources",
    "SPACETIME_BUILD_MAX_WORKERS": "max_workers",
    "SPACETIME_UNITY_VERSION": "unity_version",
    "SPACETIME_SOURCE_PROVIDER": "source_provider",
    "SPACETIME_SOURCE_REVISION": "source_revision",
    "SPACETIME_SOURCE_CREDENTIAL": "source_credential",
    "SPACETIME_PUBLISH_TARGET": "publish_target",
    "SPACETIME_PUBLISH_SUBPACKAGE": "publish_subpackage",
    "SPACETIME_PUBLISH_REDIRECT": "publish_redirect",
}
_CLI_FIELDS = {f"build.{field}": field for field in _ENV_FIELDS.values()}


def _parse_bool(value: str, field_name: str) -> bool:
    """解析只接受 true/false 的布尔覆盖。"""
    if value == "true":
        return True
    if value == "false":
        return False
    raise OverrideError(f"{field_name} 必须是 true 或 false")


def _parse_value(field_name: str, value: str) -> object:
    """按显式字段 schema 将覆盖文本转换为安全值。"""
    if not isinstance(value, str) or not value:
        raise OverrideError(f"{field_name} 覆盖值不得为空")
    if field_name in {"resources"}:
        return [item for item in value.split(",") if item]
    if field_name == "max_workers":
        try:
            return int(value, 10)
        except ValueError as exc:
            raise OverrideError("max_workers 必须是十进制整数") from exc
    if field_name in {"publish_subpackage", "publish_redirect"}:
        return _parse_bool(value, field_name)
    if field_name == "source_credential":
        try:
            return SecretRef(value)
        except Exception as exc:
            raise OverrideError("source_credential 必须是合法 SecretRef") from exc
    return value


class ConfigOverrideResolver:
    """把显式环境与 CLI mapping 合并为新的配置快照。"""

    def resolve(
        self,
        snapshot: ConfigSnapshot,
        *,
        environment: Mapping[str, str],
        cli: Mapping[str, str],
    ) -> ConfigSnapshot:
        """按固定优先级应用白名单覆盖。

        参数：
            snapshot: TOML/Profile 解析后的配置快照。
            environment: 调用方筛选后的环境字段映射。
            cli: argparse 已解析的 ``build.<field>`` 到文本映射。

        返回：
            不修改原快照的新 ``ConfigSnapshot``，来源记录为 environment 或 cli。

        异常：
            未知字段、非字符串 mapping、非法布尔/整数/SecretRef 或配置构造失败时
            抛 ``OverrideError``。

        约束与副作用：
            不读取进程环境、不执行外部系统；输入 mapping 不会被修改。
        """
        if not isinstance(snapshot, ConfigSnapshot):
            raise OverrideError("snapshot 必须是 ConfigSnapshot")
        values = (
            dict(snapshot.config.__dict__)
            if hasattr(snapshot.config, "__dict__")
            else {
                field: getattr(snapshot.config, field)
                for field in snapshot.config.__dataclass_fields__
            }
        )
        sources = dict(snapshot.sources)
        for key, raw_value in environment.items():
            if key not in _ENV_FIELDS:
                raise OverrideError(f"未知环境覆盖字段: {key}")
            if not isinstance(raw_value, str):
                raise OverrideError(f"环境覆盖值必须是 str: {key}")
            if raw_value == "":
                continue
            field_name = _ENV_FIELDS[key]
            values[field_name] = _parse_value(field_name, raw_value)
            sources[field_name] = _ConfigSource.ENVIRONMENT
        for key, raw_value in cli.items():
            if key not in _CLI_FIELDS:
                raise OverrideError(f"未知 CLI 覆盖字段: {key}")
            if not isinstance(raw_value, str):
                raise OverrideError(f"CLI 覆盖值必须是 str: {key}")
            field_name = _CLI_FIELDS[key]
            values[field_name] = _parse_value(field_name, raw_value)
            sources[field_name] = _ConfigSource.CLI
        if isinstance(values.get("resources"), list):
            values["resources"] = tuple(values["resources"])
        try:
            config = BuildConfig(**values)
        except ConfigError as exc:
            raise OverrideError(f"覆盖后配置无效: {exc}") from exc
        source_items = tuple(sorted(sources.items(), key=lambda item: item[0].encode("utf-8")))
        return ConfigSnapshot(config, source_items, _config_digest(config))


__all__ = ["ConfigOverrideResolver", "OverrideError"]
