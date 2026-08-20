"""CLI 使用的 TOML Profile 白名单加载器和不可变配置快照。

本模块把面向运行编排的少量配置字段显式映射到 ``BuildConfig``，不动态导入类，
不把任意 TOML 对象传入业务层。支持 ``profiles.<name>.extends`` 的单继承链，所有
Profile 循环、未知键、类型错误和资源路径逃逸都在配置边界失败。秘密只转换为
``configuration.model.SecretRef`` 引用，永远不解析秘密值，也不产生写副作用。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import tomli as tomllib

from configuration.model import SecretRef
from core.errors import ConfigurationError


class ConfigError(ConfigurationError):
    """表示 CLI 配置文件不能安全转换为类型化配置。"""


class _ConfigSource:
    """配置来源的内部稳定标签。"""

    DEFAULT = "default"
    TOML = "toml"
    PROFILE = "profile"
    ENVIRONMENT = "environment"
    CLI = "cli"


_ALLOWED_TOP_LEVEL = frozenset({"build", "toolchain", "source", "publish", "profiles"})
_ALLOWED_BUILD = frozenset({"profile", "resources", "max_workers"})
_ALLOWED_TOOLCHAIN = frozenset({"unity_version"})
_ALLOWED_SOURCE = frozenset({"provider", "revision", "credential"})
_ALLOWED_PUBLISH = frozenset({"target", "subpackage", "redirect"})
_ALLOWED_PROFILE = frozenset(
    {
        "extends",
        "resources",
        "max_workers",
        "unity_version",
        "source_provider",
        "source_revision",
        "source_credential",
        "publish_target",
        "publish_subpackage",
        "publish_redirect",
    }
)
_RESOURCE_TYPES = frozenset(
    {
        "audio",
        "character",
        "config",
        "file",
        "lua",
        "map",
        "particle",
        "scene",
        "shader",
        "shader_variant",
        "texture",
        "ui",
        "video",
    }
)
_PUBLISH_TARGETS = frozenset({"development", "staging", "production"})
_DEFAULTS: dict[str, object] = {
    "profile": "default",
    "resources": ("config",),
    "max_workers": 1,
    "unity_version": "2022.3.62f2",
    "source_provider": "svn",
    "source_revision": "HEAD",
    "source_credential": None,
    "publish_target": "development",
    "publish_subpackage": False,
    "publish_redirect": False,
}


def _mapping(value: object, field_name: str) -> dict[str, object]:
    """把 TOML mapping 转为独立字符串键字典。"""
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_name} 必须是 table")
    result: dict[str, object] = {}
    typed_value = cast(Mapping[object, object], value)
    for key, item in typed_value.items():
        if not isinstance(key, str):
            raise ConfigError(f"{field_name} 含非字符串字段名")
        result[key] = item
    return result


def _strict_mapping(value: object, field_name: str, allowed: frozenset[str]) -> dict[str, object]:
    """校验一个固定字段集合的 TOML table。"""
    data = _mapping(value, field_name)
    unknown = sorted(set(data) - allowed, key=lambda item: item.encode("utf-8"))
    if unknown:
        raise ConfigError(f"{field_name or 'root'} 含未知字段: {', '.join(unknown)}")
    return data


def _text(value: object, field_name: str) -> str:
    """校验非空且不含控制字符的配置文本。"""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} 必须是非空 str")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ConfigError(f"{field_name} 不得包含控制字符")
    return value


def _boolean(value: object, field_name: str) -> bool:
    """校验 TOML 布尔值，不接受整数替代。"""
    if not isinstance(value, bool):
        raise ConfigError(f"{field_name} 必须是 bool")
    return value


def _positive_int(value: object, field_name: str) -> int:
    """校验有限的正工作线程数。"""
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 256:
        raise ConfigError(f"{field_name} 必须是 1 到 256 的整数")
    return value


def _resources(value: object, field_name: str) -> tuple[str, ...]:
    """校验资源类型元组、去重规则和受支持标签。"""
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{field_name} 必须是非空资源数组")
    result: list[str] = []
    seen: set[str] = set()
    for item in cast(list[object], value):
        resource = _text(item, f"{field_name}[]")
        if resource not in _RESOURCE_TYPES:
            raise ConfigError(f"{field_name} 含未知资源类型: {resource}")
        if resource in seen:
            raise ConfigError(f"{field_name} 不得重复资源类型: {resource}")
        seen.add(resource)
        result.append(resource)
    return tuple(result)


def _secret(value: object, field_name: str) -> SecretRef | None:
    """把可选秘密 locator 转换为脱敏 ``SecretRef``。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{field_name} 必须是 secret:// locator")
    try:
        return SecretRef(value)
    except ConfigurationError as exc:
        raise ConfigError(f"{field_name} 不是合法 SecretRef") from exc


@dataclass(frozen=True, slots=True)
class BuildConfig:
    """CLI 运行编排使用的完整不可变配置。

    参数：
        profile: 选择的 Profile 名称。
        resources: 稳定顺序的资源类型元组。
        max_workers: 规划/执行允许的最大并行度。
        unity_version: 固定 Unity 版本文本。
        source_provider: 源码适配器名称。
        source_revision: 源码 revision；浮动语义由 application preflight 拒绝。
        source_credential: 可选的秘密引用，不保存秘密值。
        publish_target: development、staging 或 production。
        publish_subpackage: 是否生成分包信息。
        publish_redirect: 是否启用 Redirect 规划。

    返回：
        无；构造后字段只读。

    异常：
        非法值由 ``__post_init__`` 抛出 ``ConfigError``。

    约束与副作用：
        不访问文件系统、版本控制或对象存储；只保存可序列化公开配置和 SecretRef。
    """

    profile: str
    resources: tuple[str, ...]
    max_workers: int
    unity_version: str
    source_provider: str
    source_revision: str
    source_credential: SecretRef | None
    publish_target: str
    publish_subpackage: bool
    publish_redirect: bool

    def __post_init__(self) -> None:
        """校验配置字段并冻结资源数组边界。"""
        _text(self.profile, "profile")
        if not isinstance(self.resources, tuple):
            raise ConfigError("resources 必须是 tuple[str, ...]")
        _resources(list(self.resources), "resources")
        _positive_int(self.max_workers, "max_workers")
        _text(self.unity_version, "unity_version")
        _text(self.source_provider, "source_provider")
        _text(self.source_revision, "source_revision")
        if self.source_credential is not None and not isinstance(self.source_credential, SecretRef):
            raise ConfigError("source_credential 必须是 SecretRef 或 None")
        if self.publish_target not in _PUBLISH_TARGETS:
            raise ConfigError(f"publish_target 非法: {self.publish_target}")
        _boolean(self.publish_subpackage, "publish_subpackage")
        _boolean(self.publish_redirect, "publish_redirect")

    def redacted_dict(self) -> dict[str, object]:
        """返回不含秘密 locator 的确定性诊断字典。"""
        return {
            "max_workers": self.max_workers,
            "profile": self.profile,
            "publish_redirect": self.publish_redirect,
            "publish_subpackage": self.publish_subpackage,
            "publish_target": self.publish_target,
            "resources": list(self.resources),
            "source_credential": "SecretRef(<redacted>)"
            if self.source_credential is not None
            else None,
            "source_provider": self.source_provider,
            "source_revision": self.source_revision,
            "unity_version": self.unity_version,
        }


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """配置值、字段来源和确定性摘要的不可变快照。"""

    config: BuildConfig
    sources: tuple[tuple[str, str], ...]
    digest: str

    def __post_init__(self) -> None:
        """校验来源元组和摘要与脱敏配置的一致性。"""
        if not isinstance(self.sources, tuple):
            raise ConfigError("sources 必须是 tuple")
        source_map = dict(self.sources)
        expected_keys = set(self.config.redacted_dict())
        if set(source_map) != expected_keys:
            raise ConfigError("sources 必须恰好覆盖所有配置字段")
        if any(
            source
            not in {
                _ConfigSource.DEFAULT,
                _ConfigSource.TOML,
                _ConfigSource.PROFILE,
                _ConfigSource.ENVIRONMENT,
                _ConfigSource.CLI,
            }
            for source in source_map.values()
        ):
            raise ConfigError("sources 含未知来源")
        expected_digest = _config_digest(self.config)
        if self.digest != expected_digest:
            raise ConfigError("digest 与配置内容不一致")

    def source_of(self, field_name: str) -> str:
        """返回一个最终字段的来源标签。"""
        try:
            return dict(self.sources)[field_name]
        except KeyError as exc:
            raise ConfigError(f"未知配置字段: {field_name}") from exc

    @property
    def redacted_snapshot(self) -> str:
        """返回供日志使用的无秘密 JSON 快照。"""
        return json.dumps(self.config.redacted_dict(), ensure_ascii=False, sort_keys=True)


def _config_digest(config: BuildConfig) -> str:
    """计算脱敏配置的稳定 SHA256。"""
    content = json.dumps(
        config.redacted_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parse_profile_tables(raw: dict[str, object]) -> dict[str, dict[str, object]]:
    """读取并严格校验 ``profiles.<name>`` 动态表。"""
    if "profiles" not in raw:
        return {}
    tables = _mapping(raw["profiles"], "profiles")
    result: dict[str, dict[str, object]] = {}
    for name, value in tables.items():
        _text(name, "profile name")
        result[name] = _strict_mapping(value, f"profiles.{name}", _ALLOWED_PROFILE)
    return result


def _profile_values(
    tables: dict[str, dict[str, object]],
    name: str,
) -> dict[str, object]:
    """沿单继承链合并 Profile，并检测缺失父级和循环。"""
    visiting: set[str] = set()
    completed: dict[str, dict[str, object]] = {}

    def visit(profile_name: str) -> dict[str, object]:
        """递归解析一个 Profile；深度由显式访问集合限制。"""
        if profile_name in completed:
            return dict(completed[profile_name])
        if profile_name in visiting:
            raise ConfigError(f"Profile 继承存在循环: {profile_name}")
        table = tables.get(profile_name)
        if table is None:
            raise ConfigError(f"Profile 不存在: {profile_name}")
        visiting.add(profile_name)
        parent_name = table.get("extends")
        merged: dict[str, object] = {}
        if parent_name is not None:
            parent = _text(parent_name, f"profiles.{profile_name}.extends")
            merged.update(visit(parent))
        merged.update({key: value for key, value in table.items() if key != "extends"})
        visiting.remove(profile_name)
        completed[profile_name] = dict(merged)
        return merged

    return visit(name)


def _field_value(
    data: dict[str, object],
    key: str,
    default: object,
    converter: object,
) -> tuple[object, str]:
    """从一层 mapping 取值并记录该层来源。"""
    if key not in data:
        return default, _ConfigSource.DEFAULT
    if converter == "text":
        return _text(data[key], key), _ConfigSource.TOML
    if converter == "resources":
        return _resources(data[key], key), _ConfigSource.TOML
    if converter == "int":
        return _positive_int(data[key], key), _ConfigSource.TOML
    if converter == "bool":
        return _boolean(data[key], key), _ConfigSource.TOML
    if converter == "secret":
        return _secret(data[key], key), _ConfigSource.TOML
    raise ConfigError(f"未知配置转换器: {converter}")


class BuildConfigLoader:
    """加载单个 TOML 文件并解析选定 Profile。"""

    def load(self, path: Path, *, profile: str | None = None) -> ConfigSnapshot:
        """读取 TOML、合并 Profile 并返回脱敏配置快照。

        参数：
            path: 已存在的普通 TOML 文件路径。
            profile: 可选显式 Profile；为空时使用 ``build.profile`` 或 default。

        返回：
            通过白名单解码的 ``ConfigSnapshot``。

        异常：
            路径、TOML 编码、未知字段、继承循环或值类型非法时抛 ``ConfigError``。

        约束与副作用：
            只读一个配置文件；不读取环境变量、不创建目录、不加载适配器。
        """
        if not isinstance(path, Path) or not path.is_file():
            raise ConfigError(f"配置路径必须是现有普通文件: {path}")
        try:
            with path.open("rb") as stream:
                raw = cast(dict[str, object], tomllib.load(stream))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"配置文件加载失败: {path}") from exc
        root = _strict_mapping(raw, "", _ALLOWED_TOP_LEVEL)
        build = _strict_mapping(root.get("build", {}), "build", _ALLOWED_BUILD)
        toolchain = _strict_mapping(root.get("toolchain", {}), "toolchain", _ALLOWED_TOOLCHAIN)
        source = _strict_mapping(root.get("source", {}), "source", _ALLOWED_SOURCE)
        publish = _strict_mapping(root.get("publish", {}), "publish", _ALLOWED_PUBLISH)
        tables = _parse_profile_tables(root)
        selected = profile or cast(str | None, build.get("profile")) or "default"
        selected = _text(selected, "profile")
        inherited = _profile_values(tables, selected) if selected in tables else {}
        if tables and selected not in tables:
            raise ConfigError(f"Profile 不存在: {selected}")

        values: dict[str, object] = dict(_DEFAULTS)
        sources: dict[str, str] = {key: _ConfigSource.DEFAULT for key in values}
        values["profile"] = selected
        sources["profile"] = (
            _ConfigSource.PROFILE
            if selected in tables
            else (
                _ConfigSource.TOML
                if "profile" in build or profile is not None
                else _ConfigSource.DEFAULT
            )
        )

        sections: tuple[tuple[dict[str, object], str], ...] = (
            (build, _ConfigSource.TOML),
            (toolchain, _ConfigSource.TOML),
            (source, _ConfigSource.TOML),
            (publish, _ConfigSource.TOML),
        )
        direct_map = {
            "resources": ("resources", "resources"),
            "max_workers": ("max_workers", "int"),
            "unity_version": ("unity_version", "text"),
            "source_provider": ("provider", "text"),
            "source_revision": ("revision", "text"),
            "source_credential": ("credential", "secret"),
            "publish_target": ("target", "text"),
            "publish_subpackage": ("subpackage", "bool"),
            "publish_redirect": ("redirect", "bool"),
        }
        converters = {
            "resources": "resources",
            "max_workers": "int",
            "unity_version": "text",
            "source_provider": "text",
            "source_revision": "text",
            "source_credential": "secret",
            "publish_target": "text",
            "publish_subpackage": "bool",
            "publish_redirect": "bool",
        }
        for field_name, (toml_key, _ignored) in direct_map.items():
            converter = converters[field_name]
            for section, section_source in sections:
                if toml_key in section:
                    value, _ = _field_value(section, toml_key, values[field_name], converter)
                    values[field_name] = value
                    sources[field_name] = section_source
        profile_map = {
            "resources": "resources",
            "max_workers": "max_workers",
            "unity_version": "unity_version",
            "source_provider": "source_provider",
            "source_revision": "source_revision",
            "source_credential": "source_credential",
            "publish_target": "publish_target",
            "publish_subpackage": "publish_subpackage",
            "publish_redirect": "publish_redirect",
        }
        for field_name, profile_key in profile_map.items():
            if profile_key not in inherited:
                continue
            converter = converters[field_name]
            value = inherited[profile_key]
            if converter == "resources":
                value = _resources(value, f"profiles.{selected}.{profile_key}")
            elif converter == "int":
                value = _positive_int(value, f"profiles.{selected}.{profile_key}")
            elif converter == "bool":
                value = _boolean(value, f"profiles.{selected}.{profile_key}")
            elif converter == "secret":
                value = _secret(value, f"profiles.{selected}.{profile_key}")
            else:
                value = _text(value, f"profiles.{selected}.{profile_key}")
            values[field_name] = value
            sources[field_name] = _ConfigSource.PROFILE
        config = BuildConfig(
            profile=selected,
            resources=cast(tuple[str, ...], values["resources"]),
            max_workers=cast(int, values["max_workers"]),
            unity_version=cast(str, values["unity_version"]),
            source_provider=cast(str, values["source_provider"]),
            source_revision=cast(str, values["source_revision"]),
            source_credential=cast(SecretRef | None, values["source_credential"]),
            publish_target=cast(str, values["publish_target"]),
            publish_subpackage=cast(bool, values["publish_subpackage"]),
            publish_redirect=cast(bool, values["publish_redirect"]),
        )
        source_items = tuple(sorted(sources.items(), key=lambda item: item[0].encode("utf-8")))
        return ConfigSnapshot(config, source_items, _config_digest(config))


def make_snapshot(config: BuildConfig, sources: Mapping[str, str] | None = None) -> ConfigSnapshot:
    """为测试替身或 composition root 创建已校验配置快照。"""
    source_map = dict(sources or {key: _ConfigSource.DEFAULT for key in config.redacted_dict()})
    items = tuple(sorted(source_map.items(), key=lambda item: item[0].encode("utf-8")))
    return ConfigSnapshot(config, items, _config_digest(config))


__all__ = ["BuildConfig", "BuildConfigLoader", "ConfigError", "ConfigSnapshot", "make_snapshot"]
