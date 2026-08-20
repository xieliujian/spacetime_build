"""分支映射 TOML schema 与确定性规范化。

本模块只接受调用方提供的 TOML 文件或已解析 mapping，将 Source/Target 前缀映射
转换成不可变 ``BranchConfig``。解析采用标准库 ``tomllib``，在进入领域层前拒绝
未知字段、重复源前缀、非法路径段和不支持的未匹配策略；不会读取 SVN、解析凭据
或执行任何外部副作用。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from branch.model import BranchValidationError
from core.errors import ConfigurationError


class _TomlParser(Protocol):
    """描述 Python 3.11 ``tomllib`` 与 Python 3.10 ``tomli`` 的共同接口。"""

    TOMLDecodeError: type[Exception]

    def loads(self, value: str) -> Mapping[str, object]:
        """解析 UTF-8 TOML 字符串。"""
        ...


try:
    _toml = cast(_TomlParser, import_module("tomllib"))
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility path
    _toml = cast(_TomlParser, import_module("tomli"))


class BranchConfigError(ConfigurationError, BranchValidationError):
    """branch 配置无法通过严格 schema 或规范化校验时抛出的异常。

    职责：
        区分配置输入错误与模型运行时错误，同时保持 ``ValueError`` 兼容捕获语义。

    参数：
        继承 ``BranchValidationError`` 的标准异常参数。

    返回：
        无；本类只作为异常类型使用。

    异常：
        自身即异常，不在构造阶段读取第二份配置或访问外部系统。

    约束与副作用：
        消息只包含字段路径、规则名称和公开前缀，不包含秘密；无 I/O 副作用。
    """


_TOP_LEVEL_FIELDS = ("schema_version", "source", "target", "mappings", "externals")
_EXTERNAL_FIELDS = ("allowlist", "unmatched_policy")
_MAPPING_FIELDS = ("name", "source", "target")
_PREFIX_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+\Z")


def _strict_mapping(value: object, path: str, fields: tuple[str, ...]) -> Mapping[str, object]:
    """校验固定字段 mapping 的键类型和未知键。

    参数：
        value: 待校验的运行时值。
        path: 当前 TOML 字段路径。
        fields: 当前层允许的字段名。

    返回：
        只读视角的字符串键 mapping。

    异常：
        非 mapping、非字符串键或未知字段会抛 ``BranchConfigError``。

    约束与副作用：
        不修改原 mapping，也不访问文件系统。
    """
    if not isinstance(value, Mapping):
        raise BranchConfigError(f"{path} 必须是 mapping")
    raw = cast(Mapping[object, object], value)
    if any(not isinstance(key, str) for key in raw):
        raise BranchConfigError(f"{path} 包含非字符串字段名")
    if fields:
        unknown = sorted(
            (key for key in raw if isinstance(key, str) and key not in fields),
            key=lambda item: item.encode("utf-8"),
        )
        if unknown:
            raise BranchConfigError(f"{path}.{unknown[0]} 是未知字段")
    return cast(Mapping[str, object], raw)


def _required_schema_version(value: object) -> int:
    """读取并验证唯一支持的 branch schema 版本。

    参数：
        value: 顶层 ``schema_version`` 值。

    返回：
        整数版本 ``1``。

    异常：
        非严格整数或不是 1 时抛 ``BranchConfigError``。

    约束与副作用：
        不对未知版本做降级解析，避免未来字段被静默忽略。
    """
    if type(value) is not int or value != 1:
        raise BranchConfigError("schema_version 必须是 1")
    return value


def _normalize_prefix(value: object, field_name: str) -> str:
    """规范化并校验 SVN URL 或仓库逻辑前缀。

    参数：
        value: TOML 中的前缀字符串。
        field_name: 错误消息中的字段路径。

    返回：
        统一使用 ``/``、去掉末尾斜杠的前缀。

    异常：
        空值、反斜杠、绝对本地路径、控制字符、点段和 ``..`` 段会抛异常。

    约束与副作用：
        只规范字符串表示，不调用 ``Path.resolve``，因此不会逃逸到本机文件系统。
    """
    if not isinstance(value, str) or not value.strip():
        raise BranchConfigError(f"{field_name} 必须是非空字符串")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise BranchConfigError(f"{field_name} 不得包含空白或控制字符")
    if "\\" in value:
        raise BranchConfigError(f"{field_name} 必须使用 / 分隔")
    if value.startswith("/") and not value.startswith("//") and not value.startswith("^/"):
        raise BranchConfigError(f"{field_name} 不得是本地绝对路径")
    if re.match(r"^[A-Za-z]:", value):
        raise BranchConfigError(f"{field_name} 不得是盘符路径")
    normalized = value.rstrip("/")
    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments[1:] if normalized.startswith("^/")):
        raise BranchConfigError(f"{field_name} 不得包含点段或路径逃逸")
    if any(segment in {".", ".."} for segment in segments):
        raise BranchConfigError(f"{field_name} 不得包含点段或路径逃逸")
    if not normalized:
        raise BranchConfigError(f"{field_name} 不得为空")
    return normalized


def _normalize_name(value: object, field_name: str) -> str:
    """校验映射名称并保留可读的 custom 分层名称。

    参数：
        value: 映射名称。
        field_name: 错误消息中的字段路径。

    返回：
        原始名称字符串。

    异常：
        名称为空或包含未允许字符时抛 ``BranchConfigError``。

    约束与副作用：
        名称只用于规则选择和诊断，不参与 URL 拼接。
    """
    if not isinstance(value, str) or _PREFIX_NAME_PATTERN.fullmatch(value) is None:
        raise BranchConfigError(f"{field_name} 名称格式无效")
    return value


def _prefix_matches(prefix: str, value: str) -> bool:
    """判断 URL/逻辑路径是否命中完整前缀边界。

    参数：
        prefix: 已规范化映射源前缀。
        value: 待匹配的完整 URL 或路径。

    返回：
        完整相等或后续以 ``/`` 开始时返回 ``True``。

    异常：
        无；调用方负责传入已校验字符串。

    约束与副作用：
        纯函数，不把 ``trunk/a`` 误匹配成 ``trunk/ab``。
    """
    return value == prefix or value.startswith(prefix + "/")


@dataclass(frozen=True, slots=True)
class MappingRule:
    """一条不可变 Source/Target URL 前缀映射规则。

    参数：
        name: 稳定规则名称，例如 ``project`` 或 ``custom.tools``。
        source_prefix: 源 URL/逻辑路径前缀。
        target_prefix: 目标 URL/逻辑路径前缀。

    返回：
        无；构造后字段不可变。

    异常：
        名称或前缀不安全时抛 ``BranchConfigError``。

    约束与副作用：
        本类不检查规则之间的重复和闭环；集合级检查由 ``BranchConfig`` 或
        externals 重写器完成，便于纯规则单元测试。
    """

    name: str
    source_prefix: str
    target_prefix: str

    def __post_init__(self) -> None:
        """校验规则名称和两个前缀。"""
        _normalize_name(self.name, "MappingRule.name")
        _normalize_prefix(self.source_prefix, "MappingRule.source_prefix")
        _normalize_prefix(self.target_prefix, "MappingRule.target_prefix")

    @property
    def source(self) -> str:
        """返回 Source 语义下的源前缀别名。"""
        return self.source_prefix

    @property
    def target(self) -> str:
        """返回 Target 语义下的目标前缀别名。"""
        return self.target_prefix


@dataclass(frozen=True, slots=True)
class BranchConfig:
    """解析完成的不可变分支映射配置。

    参数：
        schema_version: 已验证的 schema 版本。
        mappings: 按 UTF-8 名称稳定排序的规则元组。
        allowlist: externals 重写后的目标前缀白名单。
        unmatched_policy: 未匹配 external 的 ``preserve`` 或 ``fail`` 策略。

    返回：
        无；通过 ``resolve`` 查询规则，通过属性读取规范化配置。

    异常：
        规则重复、同长度源前缀歧义或策略非法时抛 ``BranchConfigError``。

    约束与副作用：
        不读取 SVN、不检查目标存在性；映射顺序和 allowlist 顺序均确定。
    """

    schema_version: int
    mappings: tuple[MappingRule, ...]
    allowlist: tuple[str, ...] = ()
    unmatched_policy: str = "preserve"

    def __post_init__(self) -> None:
        """校验并规范规则、allowlist 和未匹配策略。"""
        if self.schema_version != 1:
            raise BranchConfigError("BranchConfig.schema_version 必须是 1")
        rules = tuple(self.mappings)
        if not rules:
            raise BranchConfigError("mappings 不得为空")
        if not all(isinstance(rule, MappingRule) for rule in rules):
            raise BranchConfigError("mappings 必须只包含 MappingRule")
        names: set[str] = set()
        sources: set[str] = set()
        for rule in rules:
            if rule.name in names:
                raise BranchConfigError(f"mappings 存在重复名称: {rule.name}")
            if rule.source_prefix in sources:
                raise BranchConfigError(
                    f"mappings 存在重复或歧义 source 前缀: {rule.source_prefix}"
                )
            names.add(rule.name)
            sources.add(rule.source_prefix)
        normalized_rules = tuple(
            sorted(
                rules,
                key=lambda item: (item.name.encode("utf-8"), item.source_prefix.encode("utf-8")),
            )
        )
        object.__setattr__(self, "mappings", normalized_rules)
        if self.unmatched_policy not in {"preserve", "fail"}:
            raise BranchConfigError("unmatched_policy 只能是 preserve 或 fail")
        normalized_allowlist = tuple(
            sorted(
                {_normalize_prefix(item, "externals.allowlist") for item in self.allowlist},
                key=lambda item: item.encode("utf-8"),
            )
        )
        object.__setattr__(self, "allowlist", normalized_allowlist)

    @property
    def allowed_repositories(self) -> tuple[str, ...]:
        """返回 allowlist 的兼容性别名。"""
        return self.allowlist

    @property
    def source_mappings(self) -> tuple[MappingRule, ...]:
        """返回所有 Source/Target 映射规则。"""
        return self.mappings

    def resolve(self, value: str) -> MappingRule | None:
        """按最长合法前缀解析一个 external URL。

        参数：
            value: 已解析的 external URL 或仓库相对路径。

        返回：
            命中的最长 ``MappingRule``；没有命中时返回 ``None``。

        异常：
            ``value`` 不是字符串时抛 ``BranchConfigError``。

        约束与副作用：
            规则已拒绝相同 source 前缀，因此最长匹配没有歧义；不修改配置。
        """
        if not isinstance(value, str):
            raise BranchConfigError("resolve.value 必须是字符串")
        candidates = [rule for rule in self.mappings if _prefix_matches(rule.source_prefix, value)]
        if not candidates:
            return None
        return max(candidates, key=lambda item: len(item.source_prefix.encode("utf-8")))


def _mapping_rules_from_source_target(
    source_value: object,
    target_value: object,
) -> list[MappingRule]:
    """把成对 Source/Target 表转换为规则列表。

    参数：
        source_value: 顶层 ``source`` 表。
        target_value: 顶层 ``target`` 表。

    返回：
        未排序的 ``MappingRule`` 列表，后续由 ``BranchConfig`` 规范排序。

    异常：
        两表结构、名称集合或字段值不匹配时抛 ``BranchConfigError``。

    约束与副作用：
        只消费 TOML 内存值，不生成文件或访问 SVN。
    """
    source = _strict_mapping(source_value, "source", ("project", "resource", "custom"))
    target = _strict_mapping(target_value, "target", ("project", "resource", "custom"))

    def extract(value: Mapping[str, object], path: str) -> dict[str, object]:
        """提取静态和 custom 前缀并构造规范名称。"""
        result: dict[str, object] = {}
        for name in ("project", "resource"):
            if name in value:
                result[name] = value[name]
        if "custom" in value:
            custom = _strict_mapping(value["custom"], f"{path}.custom", ())
            for custom_name, prefix in custom.items():
                result[f"custom.{_normalize_name(custom_name, f'{path}.custom')}"] = prefix
        return result

    source_entries = extract(source, "source")
    target_entries = extract(target, "target")
    if set(source_entries) != set(target_entries):
        raise BranchConfigError("source 与 target 的映射名称必须完全一致")
    return [
        MappingRule(
            name=name,
            source_prefix=_normalize_prefix(source_entries[name], f"source.{name}"),
            target_prefix=_normalize_prefix(target_entries[name], f"target.{name}"),
        )
        for name in sorted(source_entries, key=lambda item: item.encode("utf-8"))
    ]


def _mapping_rules_from_array(value: object) -> list[MappingRule]:
    """解析显式 ``mappings`` 数组，供重复规则测试和扩展 schema 使用。

    参数：
        value: TOML array of tables 或等价序列。

    返回：
        顺序保持但尚未集合规范化的规则列表。

    异常：
        非序列、元素未知字段或字段类型错误时抛 ``BranchConfigError``。

    约束与副作用：
        不接受字符串作为序列，避免把输入逐字符解析成规则。
    """
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise BranchConfigError("mappings 必须是 TOML array of tables")
    rules: list[MappingRule] = []
    items = cast(Sequence[object], value)
    for index, item in enumerate(items):
        data = _strict_mapping(item, f"mappings[{index}]", _MAPPING_FIELDS)
        rules.append(
            MappingRule(
                name=_normalize_name(data["name"], f"mappings[{index}].name"),
                source_prefix=_normalize_prefix(data["source"], f"mappings[{index}].source"),
                target_prefix=_normalize_prefix(data["target"], f"mappings[{index}].target"),
            )
        )
    return rules


def normalize_branch_config(data: object) -> BranchConfig:
    """严格校验并规范化一个已解析的 branch TOML mapping。

    参数：
        data: ``tomllib`` 解析结果；必须是字符串键 mapping。

    返回：
        ``BranchConfig``，其中规则、allowlist 和顺序均已确定性规范化。

    异常：
        未知字段、缺失 source/target、重复映射、路径逃逸或策略错误抛
        ``BranchConfigError``。

    约束与副作用：
        纯内存转换；不会读取或写入文件，也不会调用任何 SourceProvider。
    """
    root = _strict_mapping(data, "config", _TOP_LEVEL_FIELDS)
    if "schema_version" not in root:
        raise BranchConfigError("schema_version 是必填字段")
    schema_version = _required_schema_version(root["schema_version"])
    has_tables = "source" in root or "target" in root
    has_array = "mappings" in root
    if has_tables and ("source" not in root or "target" not in root):
        raise BranchConfigError("source 与 target 必须同时提供")
    if has_tables and has_array:
        raise BranchConfigError("source/target 表与 mappings 数组不能同时提供")
    if not has_tables and not has_array:
        raise BranchConfigError("必须提供 source/target 表或 mappings 数组")

    rules = (
        _mapping_rules_from_source_target(root["source"], root["target"])
        if has_tables
        else _mapping_rules_from_array(root["mappings"])
    )
    external_data: Mapping[str, object] = {}
    if "externals" in root:
        external_data = _strict_mapping(root["externals"], "externals", _EXTERNAL_FIELDS)
    allowlist_value = external_data.get("allowlist", ())
    if isinstance(allowlist_value, (str, bytes, bytearray)) or not isinstance(
        allowlist_value, Sequence
    ):
        raise BranchConfigError("externals.allowlist 必须是字符串数组")
    allowlist_items = cast(Sequence[object], allowlist_value)
    allowlist = tuple(_normalize_prefix(item, "externals.allowlist") for item in allowlist_items)
    unmatched_policy = external_data.get("unmatched_policy", "preserve")
    if not isinstance(unmatched_policy, str):
        raise BranchConfigError("externals.unmatched_policy 必须是字符串")
    try:
        return BranchConfig(
            schema_version=schema_version,
            mappings=tuple(rules),
            allowlist=allowlist,
            unmatched_policy=unmatched_policy,
        )
    except BranchConfigError:
        raise
    except BranchValidationError as exc:
        raise BranchConfigError(str(exc)) from exc


def parse_branch_config(data: object) -> BranchConfig:
    """解析 ``tomllib`` 数据的公开别名。

    参数：
        data: 标准库 TOML 解码得到的 mapping。

    返回：
        与 ``normalize_branch_config`` 相同的不可变配置。

    异常：
        配置不符合严格 schema 时抛 ``BranchConfigError``。

    约束与副作用：
        仅做纯内存操作，不执行构建或版本控制调用。
    """
    return normalize_branch_config(data)


def load_branch_config(path: Path) -> BranchConfig:
    """从 UTF-8 TOML 文件读取并规范化 branch 配置。

    参数：
        path: 配置文件的 ``pathlib.Path``；必须是显式普通文件路径对象。

    返回：
        通过严格 schema 校验的 ``BranchConfig``。

    异常：
        路径类型、读取、UTF-8、TOML 语法或字段校验失败时抛
        ``BranchConfigError``；底层异常保留为 cause。

    约束与副作用：
        只读取调用方指定文件，不创建目录、不写回配置、不连接外部系统。
    """
    if not isinstance(path, Path):
        raise BranchConfigError("path 必须是 pathlib.Path")
    try:
        content = path.read_bytes()
        decoded = content.decode("utf-8")
        data = _toml.loads(decoded)
    except (OSError, UnicodeError, _toml.TOMLDecodeError) as exc:
        raise BranchConfigError(f"branch TOML 加载失败: {path}") from exc
    return normalize_branch_config(data)


def load_config(path: Path) -> BranchConfig:
    """提供简短的 branch 配置加载兼容别名。"""
    return load_branch_config(path)
