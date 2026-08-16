"""分层 TOML 配置的严格加载、领域解码与确定性快照。

本模块只读取调用方显式传入的三个普通文件，将 TOML 白名单 schema 转换为
不可变 ``BuildConfig``，并使用 ``tomli_w`` 生成稳定规范字节。除读取配置源外，
不会检查工具存在性、创建目录、解析秘密或触发构建与发布副作用。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TypeVar, cast

import tomli
import tomli_w

from configuration.model import (
    BuildConfig,
    LoggingConfig,
    ObjectStoreConfig,
    ProfileConfig,
    ProjectConfig,
    PublishLayoutConfig,
    SecretRef,
    TaskConfig,
    UnityToolConfig,
    VersionControlConfig,
)
from configuration.validator import validate_build_config
from core.errors import ConfigurationError

_TOP_LEVEL_FIELDS = (
    "schema_version",
    "project",
    "profile",
    "tools",
    "version_control",
    "object_store",
    "publish",
    "tasks",
    "logging",
)
_PROJECT_FIELDS = ("name", "source_root", "output_root", "temp_root")
_PROFILE_FIELDS = ("emit_config_txt", "compile_lua", "encrypt_lua")
_TOOLS_FIELDS = ("unity",)
_UNITY_FIELDS = ("executable", "timeout_seconds")
_VERSION_CONTROL_FIELDS = ("provider", "credential")
_OBJECT_STORE_FIELDS = ("provider", "destination_id", "root")
_PUBLISH_FIELDS = ("layout",)
_PUBLISH_LAYOUT_FIELDS = ("root_prefix", "version_entry_key")
_TASK_FIELDS = ("enabled", "source", "output")
_LOGGING_FIELDS = (
    "console_level",
    "file_level",
    "show_traceback",
    "capture_process_output",
    "root",
    "retain_days",
)
_ENTRY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_CONFIG_NESTING = 32

_ModelT = TypeVar("_ModelT")


def _join_path(parent: str, child: str) -> str:
    """连接配置字段路径，同时避免顶层路径出现前导点。

    参数：
        parent: 父路径；顶层使用空字符串。
        child: 当前字段名。

    返回：
        供错误消息使用的点分路径。

    约束与副作用：
        不校验字段名内容，不修改任何输入。
    """
    return f"{parent}.{child}" if parent else child


def _strict_mapping(
    value: object,
    path: str,
    fields: tuple[str, ...],
) -> Mapping[str, object]:
    """校验固定 schema 映射的类型、未知字段和缺失字段。

    参数：
        value: 待校验运行时值。
        path: 当前映射的点分字段路径；顶层为空字符串。
        fields: 按设计顺序排列的完整必填字段集合。

    返回：
        只读视角的字符串键映射。

    异常：
        非 mapping、非字符串键、未知键或缺少任一必填字段时抛
        ``ConfigurationError``，消息包含完整字段路径。
    """
    display_path = path or "config"
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{display_path} 必须是 mapping")

    raw_mapping = cast(Mapping[object, object], value)
    for key in raw_mapping:
        if not isinstance(key, str):
            raise ConfigurationError(f"{display_path} 包含非字符串字段名")
    mapping = cast(Mapping[str, object], raw_mapping)
    unknown_fields = sorted(
        (key for key in mapping if key not in fields),
        key=lambda item: item.encode("utf-8"),
    )
    if unknown_fields:
        raise ConfigurationError(f"{_join_path(path, unknown_fields[0])} 是未知字段")
    for field in fields:
        if field not in mapping:
            raise ConfigurationError(f"{_join_path(path, field)} 是必填字段")
    return mapping


def _dynamic_mapping(value: object, path: str) -> Mapping[str, object]:
    """校验 profile/tasks 这类名称动态但值结构固定的非空映射。

    参数：
        value: 待校验运行时值。
        path: 动态表的顶层字段路径。

    返回：
        可按名称遍历的字符串键映射。

    异常：
        非 mapping、空 mapping 或存在非字符串名称时抛 ``ConfigurationError``。
    """
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{path} 必须是 mapping")
    raw_mapping = cast(Mapping[object, object], value)
    if not raw_mapping:
        raise ConfigurationError(f"{path} 不得为空")
    for key in raw_mapping:
        if not isinstance(key, str):
            raise ConfigurationError(f"{path} 包含非字符串名称")
    return cast(Mapping[str, object], raw_mapping)


def _expect_string(value: object, path: str) -> str:
    """严格读取字符串字段，不执行隐式字符串化。

    参数：
        value: 原始字段值。
        path: 错误定位路径。

    返回：
        原始字符串。

    异常：
        值不是 ``str`` 时抛 ``ConfigurationError``。
    """
    if not isinstance(value, str):
        raise ConfigurationError(f"{path} 必须是 str")
    return value


def _expect_bool(value: object, path: str) -> bool:
    """严格读取布尔字段，拒绝整数 0/1。

    参数：
        value: 原始字段值。
        path: 错误定位路径。

    返回：
        原始布尔值。

    异常：
        值的精确运行时类型不是 ``bool`` 时抛 ``ConfigurationError``。
    """
    if type(value) is not bool:
        raise ConfigurationError(f"{path} 必须是 bool")
    return value


def _expect_int(value: object, path: str) -> int:
    """严格读取整数，明确拒绝 ``bool`` 子类语义。

    参数：
        value: 原始字段值。
        path: 错误定位路径。

    返回：
        原始整数。

    异常：
        值的精确运行时类型不是 ``int`` 时抛 ``ConfigurationError``。
    """
    if type(value) is not int:
        raise ConfigurationError(f"{path} 必须是 int")
    return value


def _expect_path(value: object, path: str) -> Path:
    """从严格字符串字段显式构造 ``Path``。

    参数：
        value: TOML 中的路径字符串。
        path: 错误定位路径。

    返回：
        未访问文件系统的 ``Path`` 对象。

    异常：
        值不是字符串时抛 ``ConfigurationError``。

    约束与副作用：
        只做表示转换；路径安全由纯 validator 汇总检查。
    """
    return Path(_expect_string(value, path))


def _expect_project_path(value: object, field_name: str) -> Path:
    """在 ``Path`` 规范化前拒绝 Project 原始字符串中的点段。

    参数：
        value: TOML 中尚未转换的 Project 路径值。
        field_name: ProjectConfig 路径字段名。

    返回：
        原始字符串通过 ``/`` 与反斜杠双分隔检查后构造的 ``Path``。

    异常：
        类型错误或任一原始分段为 ``.`` / ``..`` 时抛 ``ConfigurationError``，
        消息以精确路径 ``config.project.<field>`` 定位。

    约束与副作用：
        不在这里拒绝绝对路径；该规则由 validator 汇总，不访问文件系统。
    """
    path = f"config.project.{field_name}"
    raw_path = _expect_string(value, path)
    if any(segment in {".", ".."} for segment in re.split(r"[\\/]", raw_path)):
        raise ConfigurationError(f"{path}: 不得包含 . 或 .. 原始路径段")
    return Path(raw_path)


def _construct_model(
    path: str,
    factory: Callable[[], _ModelT],
) -> _ModelT:
    """在调用方已知的配置边界构造领域模型并保留 cause。

    参数：
        path: 调用方明确提供的完整 TOML 表或字段路径。
        factory: 无参数领域模型构造函数。

    返回：
        构造成功的强类型模型。

    异常：
        领域模型拒绝局部语义时抛新的 ``ConfigurationError``；原异常保留为
        ``__cause__``。

    约束与副作用：
        不解析底层异常消息来猜测路径；路径只能来自当前显式解码边界。
    """
    try:
        return factory()
    except ConfigurationError as exc:
        raise ConfigurationError(f"{path}: {exc}") from exc


def _validate_dynamic_name(name: str, path: str) -> None:
    """在动态 profile/task 条目边界校验名称并显式包装 cause。

    参数：
        name: TOML 动态表中的原始条目名。
        path: ``config.profile.<name>`` 或 ``config.tasks.<name>`` 精确路径。

    返回：
        ``None``。

    异常：
        名称不符合小写条目标识规则时抛 ``ConfigurationError``，并保留一个
        原始 ``ConfigurationError`` 作为 cause。
    """
    try:
        if _ENTRY_NAME_PATTERN.fullmatch(name) is None:
            raise ConfigurationError("动态条目名称格式无效")
    except ConfigurationError as exc:
        raise ConfigurationError(f"{path}: {exc}") from exc


def _decode_project(value: object) -> ProjectConfig:
    """解码 ``project`` 固定表为 ``ProjectConfig``。

    参数：
        value: 原始 project 表。

    返回：
        路径已显式转换的不可变项目配置。
    """
    data = _strict_mapping(value, "project", _PROJECT_FIELDS)
    name = _expect_string(data["name"], "config.project.name")
    source_root = _expect_project_path(data["source_root"], "source_root")
    output_root = _expect_project_path(data["output_root"], "output_root")
    temp_root = _expect_project_path(data["temp_root"], "temp_root")
    return _construct_model(
        "config.project",
        lambda: ProjectConfig(
            name=name,
            source_root=source_root,
            output_root=output_root,
            temp_root=temp_root,
        ),
    )


def _decode_profiles(value: object) -> tuple[tuple[str, ProfileConfig], ...]:
    """解码 ``profile.<name>`` 动态表并按 UTF-8 名称字节排序。

    参数：
        value: 原始 profile 动态映射。

    返回：
        名称与不可变 Profile 模型组成的稳定元组。
    """
    profiles = _dynamic_mapping(value, "profile")
    decoded: list[tuple[str, ProfileConfig]] = []
    for name in sorted(profiles, key=lambda item: item.encode("utf-8")):
        path = f"config.profile.{name}"
        _validate_dynamic_name(name, path)
        data = _strict_mapping(profiles[name], path, _PROFILE_FIELDS)
        decoded.append(
            (
                name,
                _construct_model(
                    path,
                    lambda: ProfileConfig(
                        emit_config_txt=_expect_bool(
                            data["emit_config_txt"],
                            f"{path}.emit_config_txt",
                        ),
                        compile_lua=_expect_bool(data["compile_lua"], f"{path}.compile_lua"),
                        encrypt_lua=_expect_bool(data["encrypt_lua"], f"{path}.encrypt_lua"),
                    ),
                ),
            )
        )
    return tuple(decoded)


def _decode_unity(value: object) -> UnityToolConfig:
    """解码 ``tools`` 及其唯一 ``unity`` 子表。

    参数：
        value: 原始 tools 表。

    返回：
        Unity 可执行路径和超时配置。
    """
    tools = _strict_mapping(value, "tools", _TOOLS_FIELDS)
    data = _strict_mapping(tools["unity"], "tools.unity", _UNITY_FIELDS)
    return _construct_model(
        "config.tools.unity",
        lambda: UnityToolConfig(
            executable=_expect_path(data["executable"], "tools.unity.executable"),
            timeout_seconds=_expect_int(
                data["timeout_seconds"],
                "tools.unity.timeout_seconds",
            ),
        ),
    )


def _decode_version_control(value: object) -> VersionControlConfig:
    """解码版本控制 provider 与 SecretRef locator。

    参数：
        value: 原始 version_control 表。

    返回：
        不含秘密值的不可变版本控制配置。
    """
    data = _strict_mapping(value, "version_control", _VERSION_CONTROL_FIELDS)
    credential_value = _expect_string(
        data["credential"],
        "version_control.credential",
    )
    credential = _construct_model(
        "config.version_control.credential",
        lambda: SecretRef(credential_value),
    )
    return _construct_model(
        "config.version_control",
        lambda: VersionControlConfig(
            provider=_expect_string(data["provider"], "version_control.provider"),
            credential=credential,
        ),
    )


def _decode_object_store(value: object) -> ObjectStoreConfig:
    """解码对象存储 provider、稳定目标 ID 和逻辑根路径。

    参数：
        value: 原始 object_store 表。

    返回：
        不访问实际存储的不可变对象存储配置。
    """
    data = _strict_mapping(value, "object_store", _OBJECT_STORE_FIELDS)
    return _construct_model(
        "config.object_store",
        lambda: ObjectStoreConfig(
            provider=_expect_string(data["provider"], "object_store.provider"),
            destination_id=_expect_string(
                data["destination_id"],
                "object_store.destination_id",
            ),
            root=_expect_path(data["root"], "object_store.root"),
        ),
    )


def _decode_publish_layout(value: object) -> PublishLayoutConfig:
    """解码 ``publish.layout`` 固定发布布局表。

    参数：
        value: 原始 publish 表。

    返回：
        不解析模板的不可变发布布局配置。
    """
    publish = _strict_mapping(value, "publish", _PUBLISH_FIELDS)
    data = _strict_mapping(
        publish["layout"],
        "publish.layout",
        _PUBLISH_LAYOUT_FIELDS,
    )
    return _construct_model(
        "config.publish.layout",
        lambda: PublishLayoutConfig(
            root_prefix=_expect_string(
                data["root_prefix"],
                "publish.layout.root_prefix",
            ),
            version_entry_key=_expect_string(
                data["version_entry_key"],
                "publish.layout.version_entry_key",
            ),
        ),
    )


def _decode_tasks(value: object) -> tuple[tuple[str, TaskConfig], ...]:
    """解码 ``tasks.<name>`` 动态表并按 UTF-8 名称字节排序。

    参数：
        value: 原始 tasks 动态映射。

    返回：
        名称与不可变任务配置组成的稳定元组。
    """
    tasks = _dynamic_mapping(value, "tasks")
    decoded: list[tuple[str, TaskConfig]] = []
    for name in sorted(tasks, key=lambda item: item.encode("utf-8")):
        path = f"config.tasks.{name}"
        _validate_dynamic_name(name, path)
        data = _strict_mapping(tasks[name], path, _TASK_FIELDS)
        decoded.append(
            (
                name,
                _construct_model(
                    path,
                    lambda: TaskConfig(
                        enabled=_expect_bool(data["enabled"], f"{path}.enabled"),
                        source=_expect_string(data["source"], f"{path}.source"),
                        output=_expect_string(data["output"], f"{path}.output"),
                    ),
                ),
            )
        )
    return tuple(decoded)


def _decode_logging(value: object) -> LoggingConfig:
    """解码日志级别、开关、根路径与保留周期。

    参数：
        value: 原始 logging 表。

    返回：
        不初始化日志器的不可变日志配置。
    """
    data = _strict_mapping(value, "logging", _LOGGING_FIELDS)
    return _construct_model(
        "config.logging",
        lambda: LoggingConfig(
            console_level=_expect_string(data["console_level"], "logging.console_level"),
            file_level=_expect_string(data["file_level"], "logging.file_level"),
            show_traceback=_expect_bool(data["show_traceback"], "logging.show_traceback"),
            capture_process_output=_expect_bool(
                data["capture_process_output"],
                "logging.capture_process_output",
            ),
            root=_expect_path(data["root"], "logging.root"),
            retain_days=_expect_int(data["retain_days"], "logging.retain_days"),
        ),
    )


def _validate_config_nesting(
    mapping: Mapping[object, object],
    depth: int,
    layer_name: str,
) -> None:
    """在进入下一层 mapping 前执行固定深度上限检查。

    参数：
        mapping: 当前待遍历配置映射。
        depth: 当前 mapping 相对层根的嵌套深度；层根为 0。
        layer_name: base/profile/environment 语义名，用于错误定位。

    返回：
        ``None``。

    异常：
        下一层深度超过 ``MAX_CONFIG_NESTING`` 时抛 ``ConfigurationError``。

    约束与副作用：
        检查发生在递归调用前，最大调用深度受 32 限制；不复制或修改输入。
    """
    for value in mapping.values():
        if not isinstance(value, Mapping):
            continue
        child_depth = depth + 1
        if child_depth > MAX_CONFIG_NESTING:
            raise ConfigurationError(
                f"{layer_name} 配置嵌套超过 MAX_CONFIG_NESTING={MAX_CONFIG_NESTING}"
            )
        _validate_config_nesting(
            cast(Mapping[object, object], value),
            child_depth,
            layer_name,
        )


def merge_config_layers(
    base: Mapping[str, object],
    profile: Mapping[str, object],
    environment: Mapping[str, object],
) -> dict[str, object]:
    """按 base、profile、environment 固定顺序递归合并配置层。

    参数：
        base: 最低优先级完整基础配置。
        profile: 中优先级 Profile 覆盖。
        environment: 最高优先级环境覆盖。

    返回：
        与三个输入均不共享可变子对象的新字典。

    异常：
        同一路径发生 mapping/非 mapping 冲突，或两个非 mapping 值的精确运行时
        类型不同时抛 ``ConfigurationError``；``bool`` 与 ``int`` 明确视为不同。

    约束与副作用：
        mapping 递归合并；list 只允许被 list 整体替换。不修改输入且无 I/O。
    """
    for layer_name, typed_layer in (
        ("base", base),
        ("profile", profile),
        ("environment", environment),
    ):
        layer_object = cast(object, typed_layer)
        if not isinstance(layer_object, Mapping):
            raise ConfigurationError(f"{layer_name} 必须是 mapping")
        _validate_config_nesting(
            cast(Mapping[object, object], layer_object),
            0,
            layer_name,
        )

    def merge_into(
        current: dict[str, object],
        override: Mapping[str, object],
        parent_path: str,
    ) -> None:
        """把单层递归合入独立结果，并在最接近冲突处失败。

        参数：
            current: 仅由深拷贝值组成的当前结果。
            override: 当前更高优先级覆盖层。
            parent_path: 用于错误定位的父字段路径。

        约束与副作用：
            只修改 ``current``；不修改或复用 ``override`` 中的可变对象。
        """
        raw_override = cast(Mapping[object, object], cast(object, override))
        for key_object, incoming in raw_override.items():
            if not isinstance(key_object, str):
                raise ConfigurationError("配置层包含非字符串字段名")
            key = key_object
            path = _join_path(parent_path, key)
            if key not in current:
                current[key] = deepcopy(incoming)
                continue
            existing = current[key]
            existing_is_mapping = isinstance(existing, Mapping)
            incoming_is_mapping = isinstance(incoming, Mapping)
            if existing_is_mapping != incoming_is_mapping:
                raise ConfigurationError(f"{path} 的 mapping 与非 mapping 类型冲突")
            if existing_is_mapping:
                existing_mapping = cast(Mapping[str, object], existing)
                child = {
                    child_key: deepcopy(value) for child_key, value in existing_mapping.items()
                }
                merge_into(child, cast(Mapping[str, object], incoming), path)
                current[key] = child
                continue
            if type(existing) is not type(incoming):
                raise ConfigurationError(
                    f"{path} 的运行时类型冲突: {type(existing).__name__} != "
                    f"{type(incoming).__name__}"
                )
            current[key] = deepcopy(incoming)

    result: dict[str, object] = {}
    for layer in (base, profile, environment):
        merge_into(result, layer, "")
    return result


def decode_build_config(data: Mapping[str, object]) -> BuildConfig:
    """把严格白名单原始配置显式解码为 ``BuildConfig``。

    参数：
        data: TOML 或测试调用方提供的原始映射。

    返回：
        Path 与 SecretRef 均已显式构造的不可变完整配置。

    异常：
        任意未知字段、缺失字段、错误类型或模型局部语义错误均抛
        ``ConfigurationError`` 并包含 TOML 字段路径；底层模型错误保留为 cause。

    约束与副作用：
        只允许固定 schema，不动态导入或任意构造对象，不执行纯 validator 的
        跨字段检查，也不访问文件系统。
    """
    root = _strict_mapping(data, "", _TOP_LEVEL_FIELDS)
    schema_version = _expect_int(root["schema_version"], "schema_version")
    project = _decode_project(root["project"])
    profiles = _decode_profiles(root["profile"])
    unity = _decode_unity(root["tools"])
    version_control = _decode_version_control(root["version_control"])
    object_store = _decode_object_store(root["object_store"])
    publish_layout = _decode_publish_layout(root["publish"])
    tasks = _decode_tasks(root["tasks"])
    logging = _decode_logging(root["logging"])
    return _construct_model(
        "config",
        lambda: BuildConfig(
            schema_version=schema_version,
            project=project,
            profiles=profiles,
            unity=unity,
            version_control=version_control,
            object_store=object_store,
            publish_layout=publish_layout,
            tasks=tasks,
            logging=logging,
        ),
    )


def canonical_toml_bytes(config: BuildConfig) -> bytes:
    """使用 ``tomli_w`` 生成字段顺序固定的规范 TOML 字节。

    参数：
        config: 已构造的完整强类型配置。

    返回：
        UTF-8 无 BOM、仅 LF 且末尾恰一个换行的 TOML 字节。

    异常：
        ``config`` 运行时类型错误时抛 ``ConfigurationError``；writer 的异常不吞掉。

    约束与副作用：
        顶层与静态字段按 schema/模型顺序，动态 profile/task 名称按 UTF-8 字节
        排序，数组顺序保持不变；Path 使用 ``as_posix``，SecretRef 输出 locator。
    """
    if not isinstance(cast(object, config), BuildConfig):
        raise ConfigurationError("config 必须是 BuildConfig")

    profiles: dict[str, object] = {}
    for name, profile in sorted(config.profiles, key=lambda item: item[0].encode("utf-8")):
        profiles[name] = {
            "emit_config_txt": profile.emit_config_txt,
            "compile_lua": profile.compile_lua,
            "encrypt_lua": profile.encrypt_lua,
        }
    tasks: dict[str, object] = {}
    for name, task in sorted(config.tasks, key=lambda item: item[0].encode("utf-8")):
        tasks[name] = {
            "enabled": task.enabled,
            "source": task.source,
            "output": task.output,
        }

    document: dict[str, object] = {
        "schema_version": config.schema_version,
        "project": {
            "name": config.project.name,
            "source_root": config.project.source_root.as_posix(),
            "output_root": config.project.output_root.as_posix(),
            "temp_root": config.project.temp_root.as_posix(),
        },
        "profile": profiles,
        "tools": {
            "unity": {
                "executable": config.unity.executable.as_posix(),
                "timeout_seconds": config.unity.timeout_seconds,
            }
        },
        "version_control": {
            "provider": config.version_control.provider,
            "credential": config.version_control.credential.reveal_locator(),
        },
        "object_store": {
            "provider": config.object_store.provider,
            "destination_id": config.object_store.destination_id,
            "root": config.object_store.root.as_posix(),
        },
        "publish": {
            "layout": {
                "root_prefix": config.publish_layout.root_prefix,
                "version_entry_key": config.publish_layout.version_entry_key,
            }
        },
        "tasks": tasks,
        "logging": {
            "console_level": config.logging.console_level,
            "file_level": config.logging.file_level,
            "show_traceback": config.logging.show_traceback,
            "capture_process_output": config.logging.capture_process_output,
            "root": config.logging.root.as_posix(),
            "retain_days": config.logging.retain_days,
        },
    }
    text = tomli_w.dumps(document)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    return normalized.encode("utf-8")


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """完整配置、规范字节、摘要与三层来源的不可变快照。

    参数：
        config: 已解码并通过纯校验的构建配置。
        canonical_bytes: ``canonical_toml_bytes`` 产生的规范字节。
        digest: 规范字节 SHA-256 的 64 位小写十六进制字符串。
        sources: 按 base、profile、environment 语义排列的三个 ``Path``。

    异常：
        类型、来源数量、config 规范字节或摘要不匹配时抛 ``ConfigurationError``。

    约束与副作用：
        对象冻结且使用 slots；不重排 sources、不重新读取文件或解析秘密。
    """

    config: BuildConfig
    canonical_bytes: bytes
    digest: str
    sources: tuple[Path, Path, Path]

    def __post_init__(self) -> None:
        """验证快照结构以及摘要与规范字节的一致性。

        返回：
            ``None``。

        异常：
            任一字段类型、sources 形状或 digest 值错误时抛
            ``ConfigurationError``。

        约束与副作用：
            从 config 重算规范 TOML 后再计算 canonical_bytes 的 SHA-256；不访问 sources。
        """
        if not isinstance(cast(object, self.config), BuildConfig):
            raise ConfigurationError("ConfigSnapshot.config 必须是 BuildConfig")
        if not isinstance(cast(object, self.canonical_bytes), bytes):
            raise ConfigurationError("ConfigSnapshot.canonical_bytes 必须是 bytes")
        if not isinstance(cast(object, self.digest), str):
            raise ConfigurationError("ConfigSnapshot.digest 必须是 str")
        sources_object = cast(object, self.sources)
        if not isinstance(sources_object, tuple):
            raise ConfigurationError("ConfigSnapshot.sources 必须是三个 Path 组成的 tuple")
        sources_tuple = cast(tuple[object, ...], sources_object)
        if len(sources_tuple) != 3:
            raise ConfigurationError("ConfigSnapshot.sources 必须是三个 Path 组成的 tuple")
        if any(not isinstance(source, Path) for source in sources_tuple):
            raise ConfigurationError("ConfigSnapshot.sources 必须是三个 Path 组成的 tuple")
        expected_bytes = canonical_toml_bytes(self.config)
        if self.canonical_bytes != expected_bytes:
            raise ConfigurationError("ConfigSnapshot.canonical_bytes 与 config 规范 TOML 不一致")
        expected_digest = sha256(self.canonical_bytes).hexdigest()
        if self.digest != expected_digest:
            raise ConfigurationError("ConfigSnapshot.digest 与 canonical_bytes SHA-256 不一致")


def _validate_source_path(path: object, argument_name: str) -> Path:
    """验证加载参数是指向现有普通文件的 ``Path``。

    参数：
        path: 调用方提供的路径参数。
        argument_name: API 参数名，用于稳定错误定位。

    返回：
        原始 ``Path``，不解析或重排路径。

    异常：
        非 Path、不存在或不是普通文件时抛 ``ConfigurationError``。

    约束与副作用：
        只读取文件类型元数据，不创建、修改或删除路径。
    """
    if not isinstance(path, Path):
        raise ConfigurationError(f"{argument_name} 必须是 pathlib.Path")
    if not path.is_file():
        raise ConfigurationError(f"{argument_name} 必须指向现有普通文件: {path}")
    return path


def _load_toml_file(path: Path, argument_name: str) -> dict[str, object]:
    """读取单个二进制 TOML 文件并保留解析或 I/O cause。

    参数：
        path: 已验证的普通文件路径。
        argument_name: base/profile/environment 参数语义名。

    返回：
        tomli 解析出的新字典。

    异常：
        TOML 语法或文件读取失败时抛 ``ConfigurationError``，原异常保留为 cause。
    """
    try:
        with path.open("rb") as stream:
            return cast(dict[str, object], tomli.load(stream))
    except (OSError, UnicodeDecodeError, tomli.TOMLDecodeError) as exc:
        raise ConfigurationError(f"{argument_name} 加载失败: {path}: {exc}") from exc


def load_layered_config(
    base_path: Path,
    profile_path: Path,
    environment_path: Path,
) -> ConfigSnapshot:
    """读取、合并、解码并纯校验三个固定语义的 TOML 配置层。

    参数：
        base_path: 最低优先级基础配置普通文件。
        profile_path: 中优先级 Profile 覆盖普通文件。
        environment_path: 最高优先级环境覆盖普通文件。

    返回：
        配置、规范字节、小写 SHA-256 与原始三路径组成的不可变快照。

    异常：
        路径、TOML、合并、schema、模型或纯校验失败时抛
        ``ConfigurationError``；纯校验会在一条异常中稳定汇总全部 issue。

    约束与副作用：
        只读取三个配置文件；不写文件、不检查工具存在性、不解析秘密或访问外部系统。
    """
    sources = (
        _validate_source_path(base_path, "base_path"),
        _validate_source_path(profile_path, "profile_path"),
        _validate_source_path(environment_path, "environment_path"),
    )
    layers = (
        _load_toml_file(sources[0], "base_path"),
        _load_toml_file(sources[1], "profile_path"),
        _load_toml_file(sources[2], "environment_path"),
    )
    merged = merge_config_layers(layers[0], layers[1], layers[2])
    config = decode_build_config(merged)
    report = validate_build_config(config)
    if not report.is_valid:
        details = "\n".join(f"- {issue.path}: {issue.message}" for issue in report.issues)
        raise ConfigurationError(f"配置校验失败:\n{details}")
    canonical_bytes = canonical_toml_bytes(config)
    digest = sha256(canonical_bytes).hexdigest()
    return ConfigSnapshot(config, canonical_bytes, digest, sources)
