"""验证不可变构建配置模型的字段边界、确定性与秘密引用脱敏。

本模块按 Task 2 的测试清单逐步覆盖配置模型，不访问真实工具、版本控制、
对象存储或文件系统内容。所有失败场景仅验证内存中的配置边界。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from configuration import (
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
from core.errors import ConfigurationError


def _valid_build_config(
    profiles: tuple[tuple[str, ProfileConfig], ...] | None = None,
    tasks: tuple[tuple[str, TaskConfig], ...] | None = None,
) -> BuildConfig:
    """构造测试用最小合法 ``BuildConfig``。

    参数：
        profiles: 可选 Profile 名称与模型元组；``None`` 时使用单一 release 项。
        tasks: 可选任务名称与模型元组；``None`` 时使用单一 config 项。

    返回：
        嵌套模型、schema 和集合均合法的不可变构建配置。

    异常：
        调用方传入的覆盖值违反契约时，由 ``BuildConfig`` 抛
        ``ConfigurationError``。

    约束与副作用：
        只创建内存模型；路径不要求存在，不访问外部系统。
    """
    selected_profiles = profiles
    if selected_profiles is None:
        selected_profiles = (("release", ProfileConfig(False, True, True)),)
    selected_tasks = tasks
    if selected_tasks is None:
        selected_tasks = (("config", TaskConfig(True, "config", "config")),)
    return BuildConfig(
        schema_version=1,
        project=ProjectConfig("se", Path("source"), Path("output"), Path("temp")),
        profiles=selected_profiles,
        unity=UnityToolConfig(Path("Unity.exe"), 60),
        version_control=VersionControlConfig("svn", SecretRef("secret://credential")),
        object_store=ObjectStoreConfig("filesystem", "local", Path("cdn")),
        publish_layout=PublishLayoutConfig("{branch}/data", "version/{branch}"),
        tasks=selected_tasks,
        logging=LoggingConfig("INFO", "DEBUG", True, True, Path("logs"), 7),
    )


def test_secret_ref_is_immutable_and_always_redacted() -> None:
    """验证合法秘密引用可读取 locator，但任何字符串表示都不会泄漏引用。

    参数：
        无。

    返回：
        无返回值；通过断言验证固定脱敏文本、引用读取与冻结语义。

    异常：
        若 ``SecretRef`` 缺失、泄漏原引用或允许字段赋值，由导入或断言失败暴露。

    约束与副作用：
        ``reveal_locator`` 只返回引用 locator，不解析秘密值；测试无 I/O 副作用。
    """
    reference = "secret://version-control/build-user"
    secret_ref = SecretRef(reference)

    assert secret_ref.reveal_locator() == reference
    assert repr(secret_ref) == "SecretRef(<redacted>)"
    assert str(secret_ref) == "SecretRef(<redacted>)"
    assert reference not in repr(secret_ref)
    assert reference not in str(secret_ref)

    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        secret_ref.value = "secret://other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    (
        "",
        "   ",
        "SECRET://locator",
        "secret://",
        "secret://   ",
        "secret://with space",
        "secret://with\tcontrol",
        "secret://line\nbreak",
        " secret://locator",
        "plain-text",
        None,
        1,
    ),
)
def test_secret_ref_rejects_invalid_locator(value: object) -> None:
    """验证秘密引用严格使用小写 scheme，且 locator 非空并不含空白控制字符。

    参数：
        value: 参数化提供的空值、错误 scheme、非法 locator 或非字符串对象。

    返回：
        无返回值；所有输入均应抛出统一配置异常。

    异常：
        预期 ``SecretRef`` 对每个无效输入抛 ``ConfigurationError``；未抛或抛出
        其他异常类型时测试失败。

    约束与副作用：
        只验证引用语法，不访问 locator 对应的秘密，也不产生 I/O 副作用。
    """
    with pytest.raises(ConfigurationError):
        SecretRef(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("format_character", ("\u202e", "\u200b", "\ufeff"))
def test_secret_ref_rejects_unicode_other_characters(format_character: str) -> None:
    """验证秘密引用拒绝可隐藏或重排 locator 内容的 Unicode ``Cf`` 字符。

    参数：
        format_character: 参数化提供的双向重排、零宽空格或零宽不换行空格字符。

    返回：
        无返回值；每个含 Unicode 格式控制字符的 locator 均应构造失败。

    异常：
        预期 ``SecretRef`` 抛出 ``ConfigurationError``；未抛或异常类型错误时
        测试失败。

    约束与副作用：
        只验证引用字符串，不解析 locator 指向的秘密，也不产生 I/O 副作用。
    """
    with pytest.raises(ConfigurationError):
        SecretRef(f"secret://prefix{format_character}suffix")


@pytest.mark.parametrize("locator", ("secret://ascii/path", "secret://配置/引用"))
def test_secret_ref_accepts_normal_ascii_and_chinese_locators(locator: str) -> None:
    """验证扩大控制字符拒绝范围时仍接受普通 ASCII 与中文 locator。

    参数：
        locator: 参数化提供的不含空白或 Unicode 控制类字符的合法引用。

    返回：
        无返回值；通过引用往返断言验证合法文本未被误拒绝。

    异常：
        合法 locator 被拒绝或返回值变化时测试失败。

    约束与副作用：
        只验证引用语法与文本保持，不访问秘密服务或产生 I/O 副作用。
    """
    assert SecretRef(locator).reveal_locator() == locator


def test_simple_models_accept_valid_values_and_are_immutable() -> None:
    """验证七类简单配置保存强类型值，允许不存在路径，并在构造后保持冻结。

    参数：
        无。

    返回：
        无返回值；通过字段与赋值断言验证合法构造和不可变性。

    异常：
        任一模型缺失、错误拒绝不存在路径或允许字段赋值时测试失败。

    约束与副作用：
        路径只作为 ``Path`` 值保存，不检查存在性；测试不创建这些路径。
    """
    source_root = Path("Z:/not-required/source")
    output_root = Path("Z:/not-required/output")
    temp_root = Path("Z:/not-required/temp")
    project = ProjectConfig("se", source_root, output_root, temp_root)
    profile = ProfileConfig(True, True, False)
    unity = UnityToolConfig(Path("Z:/not-required/Unity.exe"), 60)
    credential = SecretRef("secret://version-control/build-user")
    version_control = VersionControlConfig("svn", credential)
    object_store = ObjectStoreConfig("filesystem", "local-development", Path("Z:/cdn"))
    publish_layout = PublishLayoutConfig("{branch}/data", "version/{branch}")
    logging = LoggingConfig("INFO", "DEBUG", True, False, Path("Z:/logs"), 7)

    assert project.source_root is source_root
    assert profile.encrypt_lua is False
    assert unity.timeout_seconds == 60
    assert version_control.credential is credential
    assert object_store.destination_id == "local-development"
    assert publish_layout.root_prefix == "{branch}/data"
    assert logging.retain_days == 7

    frozen_fields = (
        (project, "name"),
        (profile, "compile_lua"),
        (unity, "timeout_seconds"),
        (version_control, "provider"),
        (object_store, "destination_id"),
        (publish_layout, "root_prefix"),
        (logging, "retain_days"),
    )
    for instance, field_name in frozen_fields:
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            setattr(instance, field_name, object())


def test_simple_models_reject_blank_strings_and_wrong_nested_types() -> None:
    """验证所有简单模型字符串字段拒绝空白，嵌套 SecretRef 类型也严格校验。

    参数：
        无。

    返回：
        无返回值；每个无效构造均应抛统一配置异常。

    异常：
        预期 ``ConfigurationError``；未抛或泄漏其他异常类型时测试失败。

    约束与副作用：
        只检查字段自身，不验证 provider 是否为已安装适配器，无外部副作用。
    """
    path = Path("relative")
    credential = SecretRef("secret://credential")

    with pytest.raises(ConfigurationError):
        ProjectConfig(" ", path, path, path)
    with pytest.raises(ConfigurationError):
        ProjectConfig(1, path, path, path)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        VersionControlConfig("", credential)
    with pytest.raises(ConfigurationError):
        VersionControlConfig("svn", "secret://credential")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        ObjectStoreConfig(" ", "destination", path)
    with pytest.raises(ConfigurationError):
        ObjectStoreConfig("filesystem", "", path)
    with pytest.raises(ConfigurationError):
        PublishLayoutConfig("", "version/key")
    with pytest.raises(ConfigurationError):
        PublishLayoutConfig("root", "\t")


def test_path_fields_require_path_instances_without_task3_boundary_checks() -> None:
    """验证所有路径字段拒绝字符串，但不提前检查存在性、绝对性或根目录边界。

    参数：
        无。

    返回：
        无返回值；严格类型失败和未启用 Task 3 边界检查均由断言确认。

    异常：
        字符串路径应抛 ``ConfigurationError``；合法 ``Path`` 不应因不存在、绝对
        或父目录段而失败。

    约束与副作用：
        不访问文件系统；路径逃逸与工具存在性明确留给 Task 3。
    """
    path = Path("relative")

    with pytest.raises(ConfigurationError):
        ProjectConfig("se", "source", path, path)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        ProjectConfig("se", path, "output", path)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        ProjectConfig("se", path, path, "temp")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        UnityToolConfig("Unity.exe", 60)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        ObjectStoreConfig("filesystem", "destination", "root")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        LoggingConfig("INFO", "ERROR", True, True, "logs", 1)  # type: ignore[arg-type]

    unchecked = ProjectConfig(
        "se",
        Path("Z:/definitely/missing"),
        Path("../outside-output"),
        Path("/absolute/temp"),
    )
    assert unchecked.output_root == Path("../outside-output")


def test_simple_models_validate_boolean_integer_and_logging_boundaries() -> None:
    """验证布尔、超时、日志级别与保留天数使用精确类型和声明的数值边界。

    参数：
        无。

    返回：
        无返回值；参数化边界由统一异常断言验证。

    异常：
        非法字段应抛 ``ConfigurationError``；布尔值不得冒充整数。

    约束与副作用：
        日志级别只接受四个大写常量，不初始化日志系统或创建目录。
    """
    path = Path("relative")

    for value in (0, -1, True, "60"):
        with pytest.raises(ConfigurationError):
            UnityToolConfig(path, value)  # type: ignore[arg-type]

    for value in (-1, True, "7"):
        with pytest.raises(ConfigurationError):
            LoggingConfig("INFO", "ERROR", True, False, path, value)  # type: ignore[arg-type]

    for level in ("debug", "TRACE", "", 1):
        with pytest.raises(ConfigurationError):
            LoggingConfig(level, "ERROR", True, False, path, 0)  # type: ignore[arg-type]
        with pytest.raises(ConfigurationError):
            LoggingConfig("INFO", level, True, False, path, 0)  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError):
        ProfileConfig(1, True, False)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        LoggingConfig("INFO", "ERROR", 1, False, path, 0)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        LoggingConfig("INFO", "ERROR", True, 0, path, 0)  # type: ignore[arg-type]


def test_task_config_accepts_relative_slash_paths_and_is_immutable() -> None:
    """验证任务配置接受单段或多段 ``/`` 相对路径，并保持冻结。

    参数：
        无。

    返回：
        无返回值；通过字段、冻结和延后冲突校验断言验证成功路径。

    异常：
        合法路径被拒绝、对象可变或提前检查跨任务冲突时测试失败。

    约束与副作用：
        两个对象可暂时声明相同输出；跨任务输出冲突明确留给 Task 3。
    """
    task = TaskConfig(True, "lua", "script/runtime")
    duplicate_output = TaskConfig(False, "lua/extra", "script/runtime")

    assert task.enabled is True
    assert task.source == "lua"
    assert task.output == "script/runtime"
    assert duplicate_output.output == task.output

    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        task.output = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "logical_path",
    (
        "",
        "   ",
        "/absolute",
        "C:/absolute",
        "C:child",
        "c:folder/file",
        "bad\x00name",
        "bad\u202ename",
        "bad\u200bname",
        "bad\ufeffname",
        "source\\child",
        ".",
        "..",
        "source/./child",
        "source/../child",
        "source//child",
        "source/",
    ),
)
def test_task_config_rejects_invalid_logical_paths(logical_path: str) -> None:
    """验证任务 source/output 拒绝绝对、反斜杠、点段与空段路径。

    参数：
        logical_path: 参数化提供的非法客户端逻辑路径。

    返回：
        无返回值；source 与 output 两个字段均应拒绝相同非法形式。

    异常：
        每个非法输入预期抛 ``ConfigurationError``；其他结果使测试失败。

    约束与副作用：
        只校验单个字段的纯文本语法，不访问文件系统或比较任务输出。
    """
    with pytest.raises(ConfigurationError):
        TaskConfig(True, logical_path, "valid/output")
    with pytest.raises(ConfigurationError):
        TaskConfig(True, "valid/source", logical_path)


def test_task_config_requires_exact_runtime_field_types() -> None:
    """验证任务开关必须是布尔值，source/output 必须是字符串。

    参数：无。
    返回：无返回值；通过统一异常断言验证运行时类型边界。
    异常：每个错误类型预期抛 ``ConfigurationError``。
    约束与副作用：不进行类型隐式转换，无外部副作用。
    """
    with pytest.raises(ConfigurationError):
        TaskConfig(1, "source", "output")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        TaskConfig(True, Path("source"), "output")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        TaskConfig(True, "source", Path("output"))  # type: ignore[arg-type]


def test_task_config_accepts_normal_chinese_logical_paths() -> None:
    """验证收紧盘符与控制字符规则后仍接受普通中文相对逻辑路径。

    参数：无。
    返回：无返回值；通过字段往返断言验证中文路径未被误拒绝。
    异常：合法中文路径被拒绝或文本变化时测试失败。
    约束与副作用：仅构造内存配置，不访问对应文件系统路径。
    """
    task = TaskConfig(True, "配置/源", "输出/资源")

    assert task.source == "配置/源"
    assert task.output == "输出/资源"


def test_build_config_sorts_named_entries_and_keeps_tuples_immutable() -> None:
    """验证聚合配置按名称 UTF-8 排序，并以冻结元组保留嵌套模型。

    参数：
        无。

    返回：
        无返回值；通过顺序、类型、身份与冻结断言验证确定性。

    异常：
        排序不稳定、集合被转成可变结构或对象可赋值时测试失败。

    约束与副作用：
        两个任务故意共享输出；Task 2 不得提前执行跨任务冲突检查。
    """
    release = ProfileConfig(False, True, True)
    debug = ProfileConfig(True, False, False)
    lua = TaskConfig(True, "lua", "shared/output")
    config_task = TaskConfig(True, "config", "shared/output")
    profiles = (("release", release), ("debug", debug))
    tasks = (("lua", lua), ("config", config_task))

    config = _valid_build_config(profiles, tasks)

    assert config.schema_version == 1
    assert isinstance(config.profiles, tuple)
    assert isinstance(config.tasks, tuple)
    assert config.profiles == (("debug", debug), ("release", release))
    assert config.tasks == (("config", config_task), ("lua", lua))
    assert profiles == (("release", release), ("debug", debug))
    assert tasks == (("lua", lua), ("config", config_task))

    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        config.schema_version = 2  # type: ignore[misc]
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        config.profiles += (("other", debug),)  # type: ignore[misc]


@pytest.mark.parametrize("schema_version", (0, 2, -1, True, "1"))
def test_build_config_requires_schema_version_one(schema_version: object) -> None:
    """验证 schema_version 必须是精确整数 1，布尔和字符串均不被隐式接受。

    参数：
        schema_version: 参数化提供的非 1 或非精确整数输入。

    返回：无返回值；预期构造统一失败。
    异常：预期 ``ConfigurationError``，未抛或异常类型错误时测试失败。
    约束与副作用：只校验 schema，不进行配置迁移或文件读写。
    """
    valid = _valid_build_config()
    with pytest.raises(ConfigurationError):
        BuildConfig(
            schema_version=schema_version,  # type: ignore[arg-type]
            project=valid.project,
            profiles=valid.profiles,
            unity=valid.unity,
            version_control=valid.version_control,
            object_store=valid.object_store,
            publish_layout=valid.publish_layout,
            tasks=valid.tasks,
            logging=valid.logging,
        )


@pytest.mark.parametrize(
    "name",
    ("", " ", "Upper", "1start", "hyphen-name", "éclair"),
)
def test_build_config_rejects_invalid_profile_and_task_names(name: str) -> None:
    """验证 Profile 与任务名称只接受 ``[a-z][a-z0-9_]*``。

    参数：
        name: 参数化提供的空白、首字符、字符集或大小写非法名称。

    返回：无返回值；两个命名集合都应拒绝该名称。
    异常：预期 ``ConfigurationError``。
    约束与副作用：名称按 ASCII 契约校验，不访问配置文件。
    """
    with pytest.raises(ConfigurationError):
        _valid_build_config(profiles=((name, ProfileConfig(False, False, False)),))
    with pytest.raises(ConfigurationError):
        _valid_build_config(tasks=((name, TaskConfig(True, "source", "output")),))


def test_build_config_requires_nonempty_unique_strict_tuples() -> None:
    """验证 profiles/tasks 至少一项、名称唯一，并严格使用类型正确的嵌套元组。

    参数：无。
    返回：无返回值；所有非法集合形状均应统一失败。
    异常：预期 ``ConfigurationError``，避免泄漏拆包或排序产生的底层异常。
    约束与副作用：不修改输入集合，不做任务输出冲突检查。
    """
    profile = ProfileConfig(False, False, False)
    task = TaskConfig(True, "source", "output")

    with pytest.raises(ConfigurationError):
        _valid_build_config(profiles=())
    with pytest.raises(ConfigurationError):
        _valid_build_config(tasks=())
    with pytest.raises(ConfigurationError):
        _valid_build_config(profiles=(("same", profile), ("same", profile)))
    with pytest.raises(ConfigurationError):
        _valid_build_config(tasks=(("same", task), ("same", task)))
    with pytest.raises(ConfigurationError):
        _valid_build_config(profiles=[("release", profile)])  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        _valid_build_config(tasks=[("config", task)])  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        _valid_build_config(profiles=(("release", object()),))  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        _valid_build_config(tasks=(("config", object()),))  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        _valid_build_config(profiles=(("release",),))  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        _valid_build_config(tasks=(("config",),))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field_name",
    (
        "project",
        "unity",
        "version_control",
        "object_store",
        "publish_layout",
        "logging",
    ),
)
def test_build_config_rejects_wrong_nested_model_types(field_name: str) -> None:
    """验证 BuildConfig 不接收伪装成嵌套配置模型的普通对象。

    参数：
        field_name: 参数化提供的六个强类型嵌套配置字段名。

    返回：
        无返回值；每个字段替换为普通对象后均应统一失败。

    异常：预期 ``ConfigurationError``。
    约束与副作用：只检查模型类型，不调用任何适配器或工具。
    """
    valid = _valid_build_config()
    nested_configs: dict[str, object] = {
        "project": valid.project,
        "unity": valid.unity,
        "version_control": valid.version_control,
        "object_store": valid.object_store,
        "publish_layout": valid.publish_layout,
        "logging": valid.logging,
    }
    nested_configs[field_name] = object()

    with pytest.raises(ConfigurationError):
        BuildConfig(
            schema_version=1,
            project=cast(ProjectConfig, nested_configs["project"]),
            profiles=valid.profiles,
            unity=cast(UnityToolConfig, nested_configs["unity"]),
            version_control=cast(
                VersionControlConfig,
                nested_configs["version_control"],
            ),
            object_store=cast(ObjectStoreConfig, nested_configs["object_store"]),
            publish_layout=cast(
                PublishLayoutConfig,
                nested_configs["publish_layout"],
            ),
            tasks=valid.tasks,
            logging=cast(LoggingConfig, nested_configs["logging"]),
        )


def test_resolved_build_config_matches_profile_by_name_and_value() -> None:
    """验证已解析配置按名称找到 Profile，并接受值相等但身份不同的实例。

    参数：无。
    返回：无返回值；通过值相等、身份不同与冻结断言验证成功路径。
    异常：合法值匹配被拒绝或对象允许修改时测试失败。
    约束与副作用：只读取冻结配置元组，不修改 ``BuildConfig`` 或访问外部系统。
    """
    stored_profile = ProfileConfig(True, False, False)
    config = _valid_build_config(profiles=(("debug", stored_profile),))
    equal_profile = ProfileConfig(True, False, False)

    resolved = ResolvedBuildConfig(config, "debug", equal_profile)

    assert resolved.config is config
    assert resolved.profile_name == "debug"
    assert resolved.profile == stored_profile
    assert resolved.profile is not stored_profile
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        resolved.profile_name = "release"  # type: ignore[misc]


def test_resolved_build_config_rejects_missing_or_mismatched_profile() -> None:
    """验证已解析配置拒绝空白/未知名称、错误 Profile 值与错误嵌套类型。

    参数：无。
    返回：无返回值；所有无效关联都应统一失败。
    异常：预期 ``ConfigurationError``，不应泄漏查找产生的其他异常。
    约束与副作用：只做名称和值一致性检查，不重新解析或合并配置。
    """
    profile = ProfileConfig(True, False, False)
    config = _valid_build_config(profiles=(("debug", profile),))

    with pytest.raises(ConfigurationError):
        ResolvedBuildConfig(config, "", profile)
    with pytest.raises(ConfigurationError):
        ResolvedBuildConfig(config, "release", profile)
    with pytest.raises(ConfigurationError):
        ResolvedBuildConfig(config, "debug", ProfileConfig(False, False, False))
    with pytest.raises(ConfigurationError):
        ResolvedBuildConfig("config", "debug", profile)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        ResolvedBuildConfig(config, 1, profile)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        ResolvedBuildConfig(config, "debug", object())  # type: ignore[arg-type]
