"""构建系统配置层的不可变强类型模型。

本模块定义秘密引用与后续配置模型的纯内存表示。模型不读取配置文件、不检查
工具或路径是否存在，也不访问版本控制、对象存储或其他外部系统。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from core.errors import ConfigurationError

_SECRET_SCHEME = "secret://"
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
_WINDOWS_DRIVE_PREFIX_PATTERN = re.compile(r"^[A-Za-z]:")
_ENTRY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_non_blank_string(value: object, field_name: str) -> str:
    """校验字段为非空且非纯空白字符串。

    参数：
        value: 待检查的任意运行时值。
        field_name: 用于错误定位的字段名。

    返回：
        已通过校验的原始字符串，不做裁剪或规范化。

    异常：
        ``value`` 不是字符串或 ``strip`` 后为空时抛 ``ConfigurationError``。

    约束与副作用：
        只做内存校验；保留合法字符串的大小写和首尾字符，无外部副作用。
    """
    if not isinstance(value, str):
        raise ConfigurationError(f"{field_name} 必须是 str")
    if not value.strip():
        raise ConfigurationError(f"{field_name} 不得为空或纯空白")
    return value


def _validate_path(value: object, field_name: str) -> Path:
    """校验字段在运行时确实是 ``pathlib.Path``。

    参数：
        value: 待检查的任意运行时值。
        field_name: 用于错误定位的字段名。

    返回：
        已通过类型校验的原 ``Path`` 对象。

    异常：
        字段不是 ``Path`` 实例时抛 ``ConfigurationError``。

    约束与副作用：
        不把字符串隐式转换为路径，也不检查路径存在性、绝对性、越界或权限。
    """
    if not isinstance(value, Path):
        raise ConfigurationError(f"{field_name} 必须是 pathlib.Path")
    return value


def _validate_bool(value: object, field_name: str) -> bool:
    """校验字段是精确布尔值。

    参数：
        value: 待检查的任意运行时值。
        field_name: 用于错误定位的字段名。

    返回：
        已通过校验的布尔值。

    异常：
        字段不是 ``bool`` 时抛 ``ConfigurationError``。

    约束与副作用：
        不接受整数 0/1 的隐式布尔语义，无外部副作用。
    """
    if not isinstance(value, bool):
        raise ConfigurationError(f"{field_name} 必须是 bool")
    return value


def _validate_int(value: object, field_name: str) -> int:
    """校验字段是精确整数而不是布尔值。

    参数：
        value: 待检查的任意运行时值。
        field_name: 用于错误定位的字段名。

    返回：
        已通过校验的整数。

    异常：
        字段不是 ``int`` 或实际为 ``bool`` 时抛 ``ConfigurationError``。

    约束与副作用：
        只验证类型，不在此函数规定具体字段的数值范围；无外部副作用。
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigurationError(f"{field_name} 必须是 int")
    return value


def _validate_logical_relative_path(value: object, field_name: str) -> str:
    """校验任务 source/output 使用 ``/`` 分隔的相对逻辑路径。

    参数：
        value: 待检查的运行时值。
        field_name: 用于错误定位的字段名。

    返回：
        已通过校验的原始路径字符串，不做大小写或分隔符转换。

    异常：
        字段非字符串、为空、含反斜杠、POSIX 绝对路径、ASCII 盘符前缀、Unicode
        ``C*`` 类字符，或含空段、``.`` / ``..`` 段时抛 ``ConfigurationError``。

    约束与副作用：
        只做单字段语法校验；不检查文件存在、根路径越界或任务间输出冲突。
    """
    path = _validate_non_blank_string(value, field_name)
    if "\\" in path:
        raise ConfigurationError(f"{field_name} 必须使用 / 分隔，不得包含反斜杠")
    if path.startswith("/") or _WINDOWS_DRIVE_PREFIX_PATTERN.match(path):
        raise ConfigurationError(f"{field_name} 必须是相对逻辑路径")
    if any(unicodedata.category(character).startswith("C") for character in path):
        raise ConfigurationError(f"{field_name} 不得包含 Unicode 控制类字符")
    if any(segment in {"", ".", ".."} for segment in path.split("/")):
        raise ConfigurationError(f"{field_name} 不得包含空段、. 或 .. 段")
    return path


@dataclass(frozen=True, slots=True)
class SecretRef:
    """不会在字符串表示中泄漏内容的秘密引用。

    职责：
        保存 ``secret://`` locator，供后续配置序列化和秘密提供器解析引用。

    参数：
        value: 完整秘密引用 locator；必须使用小写 ``secret://`` scheme，且 scheme
            后必须有不含空白或控制字符的 locator。

    返回：
        无；构造后通过 ``reveal_locator`` 读取引用 locator。

    异常：
        ``value`` 不是字符串、scheme 大小写或内容错误、locator 为空或包含空白/
        控制字符时，抛出 ``ConfigurationError``。

    约束与副作用：
        ``frozen=True, slots=True``；``repr`` 与 ``str`` 始终脱敏。对象不解析、
        获取或缓存秘密值，也不产生 I/O 副作用。
    """

    value: str

    def __post_init__(self) -> None:
        """校验秘密引用 scheme 与 locator 字符边界。

        参数：
            无；读取 ``self.value``。

        返回：
            ``None``。

        异常：
            引用不是字符串、不以精确小写 ``secret://`` 开头、locator 为空或含
            任意 Unicode 空白/控制字符时，抛出 ``ConfigurationError``。

        约束与副作用：
            只校验 locator 文本，不解析 locator 指向的秘密或访问外部服务。
        """
        value_object = cast(object, self.value)
        if not isinstance(value_object, str):
            raise ConfigurationError("SecretRef.value 必须是 str")
        value = value_object
        if not value.startswith(_SECRET_SCHEME):
            raise ConfigurationError("SecretRef.value 必须使用小写 secret:// scheme")

        locator = value[len(_SECRET_SCHEME) :]
        if not locator:
            raise ConfigurationError("SecretRef locator 不得为空")
        if any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in locator
        ):
            raise ConfigurationError("SecretRef locator 不得包含空白或控制字符")

    def reveal_locator(self) -> str:
        """返回完整引用 locator，而不是 locator 指向的秘密值。

        参数：
            无。

        返回：
            构造时保存的 ``secret://...`` 引用 locator，供配置序列化使用。

        异常：
            无。

        约束与副作用：
            不调用秘密服务、不解析秘密内容；仅返回内存中的引用字符串。
        """
        return self.value

    def __repr__(self) -> str:
        """返回不包含引用内容的固定调试表示。

        参数：
            无。

        返回：
            固定字符串 ``SecretRef(<redacted>)``。

        异常：
            无。

        约束与副作用：
            不读取或拼接 ``value``，确保日志与调试输出不会泄漏 locator。
        """
        return "SecretRef(<redacted>)"

    def __str__(self) -> str:
        """返回不包含引用内容的固定用户字符串。

        参数：
            无。

        返回：
            固定字符串 ``SecretRef(<redacted>)``。

        异常：
            无。

        约束与副作用：
            与 ``repr`` 使用相同脱敏结果，不访问任何外部秘密服务。
        """
        return "SecretRef(<redacted>)"


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """项目名称及三类工作路径的不可变配置。

    参数：
        name: 非空项目名。
        source_root: 源码根路径。
        output_root: 构建输出根路径。
        temp_root: 临时工作根路径。

    返回：
        无；构造后通过字段读取配置。

    异常：
        名称无效或任一路径不是 ``Path`` 时抛 ``ConfigurationError``。

    约束与副作用：
        不检查路径存在性、互相包含关系或越界；对象冻结且无 I/O 副作用。
    """

    name: str
    source_root: Path
    output_root: Path
    temp_root: Path

    def __post_init__(self) -> None:
        """校验项目字段的局部类型与非空约束。

        参数：无；读取实例字段。
        返回：``None``。
        异常：字段无效时抛 ``ConfigurationError``。
        约束与副作用：不访问文件系统，也不做跨路径关系校验。
        """
        _validate_non_blank_string(self.name, "ProjectConfig.name")
        _validate_path(self.source_root, "ProjectConfig.source_root")
        _validate_path(self.output_root, "ProjectConfig.output_root")
        _validate_path(self.temp_root, "ProjectConfig.temp_root")


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    """资源构建 Profile 的不可变功能开关。

    参数：
        emit_config_txt: 是否输出配置调试文本。
        compile_lua: 是否编译 Lua。
        encrypt_lua: 是否加密 Lua。

    返回：无；构造后通过字段读取开关。
    异常：任一字段不是 ``bool`` 时抛 ``ConfigurationError``。
    约束与副作用：只保存开关，不检查组合策略或执行工具；对象冻结且无副作用。
    """

    emit_config_txt: bool
    compile_lua: bool
    encrypt_lua: bool

    def __post_init__(self) -> None:
        """校验三个 Profile 开关均为精确布尔值。

        参数：无；读取实例字段。
        返回：``None``。
        异常：任一字段不是 ``bool`` 时抛 ``ConfigurationError``。
        约束与副作用：不推导或修改开关组合，无外部副作用。
        """
        _validate_bool(self.emit_config_txt, "ProfileConfig.emit_config_txt")
        _validate_bool(self.compile_lua, "ProfileConfig.compile_lua")
        _validate_bool(self.encrypt_lua, "ProfileConfig.encrypt_lua")


@dataclass(frozen=True, slots=True)
class UnityToolConfig:
    """Unity 可执行文件引用与正超时秒数配置。

    参数：
        executable: Unity 可执行文件路径对象。
        timeout_seconds: 严格大于零的执行超时秒数。

    返回：无；构造后通过字段读取工具配置。
    异常：路径类型错误或超时不是正整数时抛 ``ConfigurationError``。
    约束与副作用：不检查工具存在性、版本或可执行权限；对象冻结且无 I/O。
    """

    executable: Path
    timeout_seconds: int

    def __post_init__(self) -> None:
        """校验 Unity 路径类型和正超时边界。

        参数：无；读取实例字段。
        返回：``None``。
        异常：字段类型错误或超时小于等于零时抛 ``ConfigurationError``。
        约束与副作用：不访问可执行文件，无外部副作用。
        """
        _validate_path(self.executable, "UnityToolConfig.executable")
        timeout_seconds = _validate_int(
            self.timeout_seconds,
            "UnityToolConfig.timeout_seconds",
        )
        if timeout_seconds <= 0:
            raise ConfigurationError("UnityToolConfig.timeout_seconds 必须大于 0")


@dataclass(frozen=True, slots=True)
class VersionControlConfig:
    """版本控制 provider 与凭据引用的不可变配置。

    参数：
        provider: 非空 provider 标识。
        credential: 已校验并脱敏的 ``SecretRef``。

    返回：无；构造后通过字段读取配置。
    异常：provider 为空或 credential 类型错误时抛 ``ConfigurationError``。
    约束与副作用：不验证 provider 是否安装，也不解析凭据；对象冻结且无 I/O。
    """

    provider: str
    credential: SecretRef

    def __post_init__(self) -> None:
        """校验 provider 与秘密引用类型。

        参数：无；读取实例字段。
        返回：``None``。
        异常：字段违反局部约束时抛 ``ConfigurationError``。
        约束与副作用：不连接版本控制系统或秘密服务。
        """
        _validate_non_blank_string(self.provider, "VersionControlConfig.provider")
        credential_object = cast(object, self.credential)
        if not isinstance(credential_object, SecretRef):
            raise ConfigurationError("VersionControlConfig.credential 必须是 SecretRef")


@dataclass(frozen=True, slots=True)
class ObjectStoreConfig:
    """对象存储 provider、稳定目标标识与根路径配置。

    参数：
        provider: 非空 provider 标识。
        destination_id: 不含凭据的非空稳定目标标识。
        root: 对象存储根路径对象。

    返回：无；构造后通过字段读取配置。
    异常：字符串为空或 root 不是 ``Path`` 时抛 ``ConfigurationError``。
    约束与副作用：不连接存储、不检查根目录安全；对象冻结且无 I/O。
    """

    provider: str
    destination_id: str
    root: Path

    def __post_init__(self) -> None:
        """校验对象存储字段的局部类型与非空约束。

        参数：无；读取实例字段。
        返回：``None``。
        异常：字段违反局部约束时抛 ``ConfigurationError``。
        约束与副作用：不检查 provider 能力或文件系统边界。
        """
        _validate_non_blank_string(self.provider, "ObjectStoreConfig.provider")
        _validate_non_blank_string(
            self.destination_id,
            "ObjectStoreConfig.destination_id",
        )
        _validate_path(self.root, "ObjectStoreConfig.root")


@dataclass(frozen=True, slots=True)
class PublishLayoutConfig:
    """发布对象根前缀与版本入口 key 的不可变配置。

    参数：
        root_prefix: 非空发布根前缀。
        version_entry_key: 非空客户端版本入口 key。

    返回：无；构造后通过字段读取布局文本。
    异常：任一字段不是非空字符串时抛 ``ConfigurationError``。
    约束与副作用：本阶段不校验模板或路径安全；对象冻结且无外部副作用。
    """

    root_prefix: str
    version_entry_key: str

    def __post_init__(self) -> None:
        """校验发布布局两个字符串字段非空。

        参数：无；读取实例字段。
        返回：``None``。
        异常：任一字段为空或类型错误时抛 ``ConfigurationError``。
        约束与副作用：不解析模板、不检查 key 路径安全，无外部副作用。
        """
        _validate_non_blank_string(self.root_prefix, "PublishLayoutConfig.root_prefix")
        _validate_non_blank_string(
            self.version_entry_key,
            "PublishLayoutConfig.version_entry_key",
        )


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """控制日志级别、回溯、进程输出与保留周期的不可变配置。

    参数：
        console_level: 控制台级别，只允许 DEBUG/INFO/WARNING/ERROR。
        file_level: 文件级别，只允许相同四项。
        show_traceback: 是否显示异常回溯。
        capture_process_output: 是否采集外部进程输出。
        root: 日志根路径对象。
        retain_days: 大于等于零的保留天数。

    返回：无；构造后通过字段读取日志策略。
    异常：字段类型、级别或天数边界无效时抛 ``ConfigurationError``。
    约束与副作用：不初始化日志器、不创建目录；对象冻结且无 I/O。
    """

    console_level: str
    file_level: str
    show_traceback: bool
    capture_process_output: bool
    root: Path
    retain_days: int

    def __post_init__(self) -> None:
        """校验日志级别、布尔开关、根路径与保留天数。

        参数：无；读取实例字段。
        返回：``None``。
        异常：任一字段违反局部约束时抛 ``ConfigurationError``。
        约束与副作用：不配置日志输出或访问日志目录，无外部副作用。
        """
        console_level = _validate_non_blank_string(
            self.console_level,
            "LoggingConfig.console_level",
        )
        file_level = _validate_non_blank_string(
            self.file_level,
            "LoggingConfig.file_level",
        )
        if console_level not in _LOG_LEVELS:
            raise ConfigurationError("LoggingConfig.console_level 不是支持的日志级别")
        if file_level not in _LOG_LEVELS:
            raise ConfigurationError("LoggingConfig.file_level 不是支持的日志级别")
        _validate_bool(self.show_traceback, "LoggingConfig.show_traceback")
        _validate_bool(
            self.capture_process_output,
            "LoggingConfig.capture_process_output",
        )
        _validate_path(self.root, "LoggingConfig.root")
        retain_days = _validate_int(self.retain_days, "LoggingConfig.retain_days")
        if retain_days < 0:
            raise ConfigurationError("LoggingConfig.retain_days 必须大于等于 0")


@dataclass(frozen=True, slots=True)
class TaskConfig:
    """单个资源任务的开关与输入输出逻辑路径配置。

    参数：
        enabled: 是否启用该任务。
        source: 使用 ``/`` 的非空相对逻辑源路径。
        output: 使用 ``/`` 的非空相对逻辑输出路径。

    返回：无；构造后通过字段读取任务配置。
    异常：开关类型或任一路径语法无效时抛 ``ConfigurationError``。
    约束与副作用：不比较不同任务的输出、不检查路径越界；对象冻结且无 I/O。
    """

    enabled: bool
    source: str
    output: str

    def __post_init__(self) -> None:
        """校验任务开关与两个相对逻辑路径。

        参数：无；读取实例字段。
        返回：``None``。
        异常：字段类型或逻辑路径语法无效时抛 ``ConfigurationError``。
        约束与副作用：不执行任务、不访问路径，也不做跨任务冲突校验。
        """
        _validate_bool(self.enabled, "TaskConfig.enabled")
        _validate_logical_relative_path(self.source, "TaskConfig.source")
        _validate_logical_relative_path(self.output, "TaskConfig.output")


@dataclass(frozen=True, slots=True)
class BuildConfig:
    """完整构建配置快照的不可变聚合根。

    参数：
        schema_version: 固定为精确整数 1。
        project: 项目路径配置。
        profiles: 非空 ``(名称, ProfileConfig)`` 元组，名称唯一并按 UTF-8 排序。
        unity: Unity 工具配置。
        version_control: 版本控制配置。
        object_store: 对象存储配置。
        publish_layout: 发布布局配置。
        tasks: 非空 ``(名称, TaskConfig)`` 元组，名称唯一并按 UTF-8 排序。
        logging: 日志配置。

    返回：无；构造后通过字段读取已冻结且确定性排序的配置快照。
    异常：schema、嵌套类型、集合形状、名称或重复项无效时抛
        ``ConfigurationError``。
    约束与副作用：不检查工具存在、路径越界、模板安全或输出冲突；无 I/O。
    """

    schema_version: int
    project: ProjectConfig
    profiles: tuple[tuple[str, ProfileConfig], ...]
    unity: UnityToolConfig
    version_control: VersionControlConfig
    object_store: ObjectStoreConfig
    publish_layout: PublishLayoutConfig
    tasks: tuple[tuple[str, TaskConfig], ...]
    logging: LoggingConfig

    def __post_init__(self) -> None:
        """校验聚合结构并确定性规范化 Profile 与任务顺序。

        参数：无；读取全部实例字段。
        返回：``None``。
        异常：任一局部结构、名称、schema 或嵌套模型类型无效时抛
            ``ConfigurationError``。
        约束与副作用：仅用 ``object.__setattr__`` 写回排序后的新元组；不修改输入
            元组，不执行 Task 3 的跨字段或外部环境校验。
        """
        schema_version = _validate_int(self.schema_version, "BuildConfig.schema_version")
        if schema_version != 1:
            raise ConfigurationError("BuildConfig.schema_version 必须等于 1")
        if not isinstance(cast(object, self.project), ProjectConfig):
            raise ConfigurationError("BuildConfig.project 必须是 ProjectConfig")
        if not isinstance(cast(object, self.unity), UnityToolConfig):
            raise ConfigurationError("BuildConfig.unity 必须是 UnityToolConfig")
        if not isinstance(cast(object, self.version_control), VersionControlConfig):
            raise ConfigurationError("BuildConfig.version_control 必须是 VersionControlConfig")
        if not isinstance(cast(object, self.object_store), ObjectStoreConfig):
            raise ConfigurationError("BuildConfig.object_store 必须是 ObjectStoreConfig")
        if not isinstance(cast(object, self.publish_layout), PublishLayoutConfig):
            raise ConfigurationError("BuildConfig.publish_layout 必须是 PublishLayoutConfig")
        if not isinstance(cast(object, self.logging), LoggingConfig):
            raise ConfigurationError("BuildConfig.logging 必须是 LoggingConfig")

        profiles_object = cast(object, self.profiles)
        if not isinstance(profiles_object, tuple) or not profiles_object:
            raise ConfigurationError("BuildConfig.profiles 必须是非空 tuple")
        normalized_profiles: list[tuple[str, ProfileConfig]] = []
        profile_names: set[str] = set()
        for entry_object in cast(tuple[object, ...], profiles_object):
            if not isinstance(entry_object, tuple):
                raise ConfigurationError("BuildConfig.profiles 每项必须是长度为 2 的 tuple")
            entry = cast(tuple[object, ...], entry_object)
            if len(entry) != 2:
                raise ConfigurationError("BuildConfig.profiles 每项必须是长度为 2 的 tuple")
            name = _validate_non_blank_string(entry[0], "BuildConfig.profiles.name")
            if _ENTRY_NAME_PATTERN.fullmatch(name) is None:
                raise ConfigurationError("BuildConfig.profiles 名称格式无效")
            if name in profile_names:
                raise ConfigurationError(f"BuildConfig.profiles 存在重复名称: {name}")
            profile = entry[1]
            if not isinstance(profile, ProfileConfig):
                raise ConfigurationError("BuildConfig.profiles 值必须是 ProfileConfig")
            profile_names.add(name)
            normalized_profiles.append((name, profile))

        tasks_object = cast(object, self.tasks)
        if not isinstance(tasks_object, tuple) or not tasks_object:
            raise ConfigurationError("BuildConfig.tasks 必须是非空 tuple")
        normalized_tasks: list[tuple[str, TaskConfig]] = []
        task_names: set[str] = set()
        for entry_object in cast(tuple[object, ...], tasks_object):
            if not isinstance(entry_object, tuple):
                raise ConfigurationError("BuildConfig.tasks 每项必须是长度为 2 的 tuple")
            entry = cast(tuple[object, ...], entry_object)
            if len(entry) != 2:
                raise ConfigurationError("BuildConfig.tasks 每项必须是长度为 2 的 tuple")
            name = _validate_non_blank_string(entry[0], "BuildConfig.tasks.name")
            if _ENTRY_NAME_PATTERN.fullmatch(name) is None:
                raise ConfigurationError("BuildConfig.tasks 名称格式无效")
            if name in task_names:
                raise ConfigurationError(f"BuildConfig.tasks 存在重复名称: {name}")
            task = entry[1]
            if not isinstance(task, TaskConfig):
                raise ConfigurationError("BuildConfig.tasks 值必须是 TaskConfig")
            task_names.add(name)
            normalized_tasks.append((name, task))

        object.__setattr__(
            self,
            "profiles",
            tuple(sorted(normalized_profiles, key=lambda item: item[0].encode("utf-8"))),
        )
        object.__setattr__(
            self,
            "tasks",
            tuple(sorted(normalized_tasks, key=lambda item: item[0].encode("utf-8"))),
        )


@dataclass(frozen=True, slots=True)
class ResolvedBuildConfig:
    """绑定完整配置与一个已选 Profile 的不可变解析结果。

    参数：
        config: 已校验的完整 ``BuildConfig``。
        profile_name: 必须存在于 ``config.profiles`` 的非空名称。
        profile: 必须与该名称对应值相等的 ``ProfileConfig``。

    返回：无；构造后通过字段读取完整配置与已解析 Profile。
    异常：嵌套类型、名称存在性或 Profile 值匹配失败时抛
        ``ConfigurationError``。
    约束与副作用：按值比较 Profile，不要求对象身份相同；不重新加载或合并配置。
    """

    config: BuildConfig
    profile_name: str
    profile: ProfileConfig

    def __post_init__(self) -> None:
        """校验 Profile 名称存在且绑定值与完整配置一致。

        参数：无；读取三个实例字段。
        返回：``None``。
        异常：字段类型错误、名称不存在或 Profile 值不相等时抛
            ``ConfigurationError``。
        约束与副作用：只遍历冻结元组并按值比较，不修改原配置或访问外部系统。
        """
        if not isinstance(cast(object, self.config), BuildConfig):
            raise ConfigurationError("ResolvedBuildConfig.config 必须是 BuildConfig")
        profile_name = _validate_non_blank_string(
            self.profile_name,
            "ResolvedBuildConfig.profile_name",
        )
        if not isinstance(cast(object, self.profile), ProfileConfig):
            raise ConfigurationError("ResolvedBuildConfig.profile 必须是 ProfileConfig")

        matched_profile: ProfileConfig | None = None
        for name, profile in self.config.profiles:
            if name == profile_name:
                matched_profile = profile
                break
        if matched_profile is None:
            raise ConfigurationError(f"ResolvedBuildConfig.profile_name 不存在: {profile_name}")
        if matched_profile != self.profile:
            raise ConfigurationError("ResolvedBuildConfig.profile 与配置中的值不一致")
