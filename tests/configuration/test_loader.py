"""配置分层加载、严格解码、规范 TOML 与快照测试。

本模块以真实字典、TOML 字节和临时文件覆盖 Task 3 的纯配置边界，不使用
mock，也不访问版本控制、Unity、对象存储或秘密服务。
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest
import tomli

import configuration.loader as loader_module
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
    SecretRef,
    TaskConfig,
    UnityToolConfig,
    VersionControlConfig,
)
from core.errors import ConfigurationError


def _valid_data() -> dict[str, object]:
    """创建字段完整且可被纯校验器接受的原始配置字典。

    返回：
        每次调用均返回独立的可变字典，便于错误测试局部修改。

    约束与副作用：
        路径和值只用于内存测试；不创建文件、不解析秘密。
    """
    return {
        "schema_version": 1,
        "project": {
            "name": "spacetime",
            "source_root": "workspace/source",
            "output_root": "workspace/output",
            "temp_root": "workspace/temp",
        },
        "profile": {
            "release": {
                "emit_config_txt": False,
                "compile_lua": True,
                "encrypt_lua": True,
            },
            "debug": {
                "emit_config_txt": True,
                "compile_lua": False,
                "encrypt_lua": False,
            },
        },
        "tools": {
            "unity": {
                "executable": "C:/Unity/Editor/Unity.exe",
                "timeout_seconds": 600,
            }
        },
        "version_control": {
            "provider": "svn",
            "credential": "secret://build/svn",
        },
        "object_store": {
            "provider": "filesystem",
            "destination_id": "release-primary",
            "root": "artifacts",
        },
        "publish": {
            "layout": {
                "root_prefix": "releases/{branch}",
                "version_entry_key": "versions/{platform}/entry.json",
            }
        },
        "tasks": {
            "shader": {
                "enabled": True,
                "source": "assets/shader",
                "output": "bundles/shader",
            },
            "config": {
                "enabled": True,
                "source": "assets/config",
                "output": "bundles/config",
            },
        },
        "logging": {
            "console_level": "INFO",
            "file_level": "DEBUG",
            "show_traceback": False,
            "capture_process_output": True,
            "root": "logs",
            "retain_days": 14,
        },
    }


def _valid_config(*, reverse_dynamic_order: bool = False) -> BuildConfig:
    """创建可序列化的强类型配置，并可反转动态项输入顺序。

    参数：
        reverse_dynamic_order: 为真时以相反顺序传入 profile 和 task。

    返回：
        通过模型局部约束与 Task 3 纯校验规则的 ``BuildConfig``。

    约束与副作用：
        不读取路径或工具；动态项顺序仅用于确定性序列化测试。
    """
    profiles = (
        ("release", ProfileConfig(False, True, True)),
        ("debug", ProfileConfig(True, False, False)),
    )
    tasks = (
        ("shader", TaskConfig(True, "assets/shader", "bundles/shader")),
        ("config", TaskConfig(True, "assets/config", "bundles/config")),
    )
    if reverse_dynamic_order:
        profiles = tuple(reversed(profiles))
        tasks = tuple(reversed(tasks))
    return BuildConfig(
        schema_version=1,
        project=ProjectConfig(
            name="spacetime",
            source_root=Path("workspace/source"),
            output_root=Path("workspace/output"),
            temp_root=Path("workspace/temp"),
        ),
        profiles=profiles,
        unity=UnityToolConfig(Path("C:/Unity/Editor/Unity.exe"), 600),
        version_control=VersionControlConfig("svn", SecretRef("secret://build/svn")),
        object_store=ObjectStoreConfig("filesystem", "release-primary", Path("artifacts")),
        publish_layout=PublishLayoutConfig(
            "releases/{branch}",
            "versions/{platform}/entry.json",
        ),
        tasks=tasks,
        logging=LoggingConfig("INFO", "DEBUG", False, True, Path("logs"), 14),
    )


def _nested_mapping(data: dict[str, object], path: tuple[str, ...]) -> dict[str, object]:
    """按字段路径取得测试字典中的嵌套映射。

    参数：
        data: 待导航的顶层测试字典。
        path: 由映射键构成的非空或空路径。

    返回：
        指定位置的可变字符串键字典。

    异常：
        fixture 结构与测试声明不一致时由断言立即终止测试。
    """
    current = data
    for key in path:
        value = current[key]
        assert isinstance(value, dict)
        current = cast(dict[str, object], value)
    return current


def test_merge_config_layers_recurses_in_fixed_order_without_mutating_inputs() -> None:
    """验证 base/profile/environment 递归覆盖顺序及输入不变性。

    三层包含嵌套 mapping 和 list；后层应覆盖同类型叶子及整个 list，同时保留
    未覆盖字段。函数不得修改任何输入对象。
    """
    base: dict[str, object] = {
        "nested": {"value": 1, "kept": "base"},
        "items": ["base"],
    }
    profile: dict[str, object] = {
        "nested": {"value": 2},
        "items": ["profile"],
    }
    environment: dict[str, object] = {"nested": {"value": 3}}
    originals = deepcopy((base, profile, environment))

    merged = merge_config_layers(base, profile, environment)

    assert merged == {
        "nested": {"value": 3, "kept": "base"},
        "items": ["profile"],
    }
    assert (base, profile, environment) == originals
    cast(list[str], merged["items"]).append("changed")
    assert profile["items"] == ["profile"]


@pytest.mark.parametrize(
    ("base_value", "override_value"),
    [
        ({"child": 1}, 1),
        (1, {"child": 1}),
        (True, 1),
        (1, True),
        (["one"], ("one",)),
    ],
)
def test_merge_config_layers_rejects_mapping_and_runtime_type_conflicts(
    base_value: object,
    override_value: object,
) -> None:
    """验证 mapping/非 mapping 与精确运行时类型冲突均被拒绝。

    参数：
        base_value: base 层叶子值。
        override_value: profile 层冲突值。

    异常：
        预期 ``ConfigurationError`` 且消息包含冲突字段路径。
    """
    with pytest.raises(ConfigurationError, match="value"):
        merge_config_layers({"value": base_value}, {"value": override_value}, {})


def test_merge_config_layers_rejects_nesting_deeper_than_32() -> None:
    """验证配置 mapping 超过公开安全深度时主动失败而非递归溢出。

    32 层边界继续允许；第 33 层必须在递归进入前抛 ``ConfigurationError``，
    调用方不应观察到 ``RecursionError``。
    """

    def nested_mapping(depth: int) -> dict[str, object]:
        """构造指定 mapping 深度的独立测试输入。

        参数：
            depth: ``root`` 值下连续 ``child`` mapping 的数量。

        返回：
            最深叶子为字符串的嵌套字典。
        """
        value: object = "leaf"
        for _ in range(depth):
            value = {"child": value}
        return {"root": value}

    assert merge_config_layers(nested_mapping(32), {}, {}) == nested_mapping(32)
    with pytest.raises(ConfigurationError, match="嵌套"):
        merge_config_layers(nested_mapping(33), {}, {})
    assert loader_module.MAX_CONFIG_NESTING == 32


def test_decode_build_config_converts_paths_secret_and_schema_tables() -> None:
    """验证白名单 TOML 结构被显式转换为不可变领域模型。

    ``profile``、``tools.unity`` 与 ``publish.layout`` 应映射到模型的 profiles、
    unity 和 publish_layout，路径及秘密引用不得作为裸字符串遗留。
    """
    config = decode_build_config(_valid_data())

    assert config.project.source_root == Path("workspace/source")
    assert config.unity.executable == Path("C:/Unity/Editor/Unity.exe")
    assert config.object_store.root == Path("artifacts")
    assert config.logging.root == Path("logs")
    assert config.version_control.credential == SecretRef("secret://build/svn")
    assert tuple(name for name, _ in config.profiles) == ("debug", "release")
    assert tuple(name for name, _ in config.tasks) == ("config", "shader")


@pytest.mark.parametrize("field_name", ["source_root", "output_root", "temp_root"])
@pytest.mark.parametrize(
    "invalid_path",
    [
        "workspace/./source",
        r"workspace\.\source",
        "workspace/../source",
    ],
)
def test_decode_build_config_rejects_raw_project_dot_segments_before_path_conversion(
    field_name: str,
    invalid_path: str,
) -> None:
    """验证 Project 原始字符串中的两类分隔符点段不会被 Path 规范化吞掉。

    参数：
        field_name: ProjectConfig 的三个路径字段之一。
        invalid_path: 使用 ``/`` 或反斜杠表达的 ``.`` / ``..`` 原始路径。

    异常：
        解码必须在构造 Path 前抛 ``ConfigurationError``，并以精确 TOML 字段路径
        ``config.project.<field>`` 开始诊断。
    """
    data = _valid_data()
    _nested_mapping(data, ("project",))[field_name] = invalid_path
    expected_path = f"config.project.{field_name}"

    with pytest.raises(ConfigurationError) as caught:
        decode_build_config(data)

    assert str(caught.value).startswith(f"{expected_path}:")


@pytest.mark.parametrize(
    ("container_path", "expected_path"),
    [
        ((), "unknown"),
        (("project",), "project.unknown"),
        (("profile", "release"), "profile.release.unknown"),
        (("tools",), "tools.unknown"),
        (("tools", "unity"), "tools.unity.unknown"),
        (("version_control",), "version_control.unknown"),
        (("object_store",), "object_store.unknown"),
        (("publish",), "publish.unknown"),
        (("publish", "layout"), "publish.layout.unknown"),
        (("tasks", "config"), "tasks.config.unknown"),
        (("logging",), "logging.unknown"),
    ],
)
def test_decode_build_config_rejects_unknown_fields_at_every_major_layer(
    container_path: tuple[str, ...],
    expected_path: str,
) -> None:
    """验证每个主要 schema 层级都严格拒绝未知字段。

    参数：
        container_path: 注入未知字段的映射路径。
        expected_path: 必须出现在异常消息中的完整字段路径。
    """
    data = _valid_data()
    _nested_mapping(data, container_path)["unknown"] = "unexpected"

    with pytest.raises(ConfigurationError, match=expected_path.replace(".", r"\.")):
        decode_build_config(data)


@pytest.mark.parametrize(
    "field_path",
    [
        ("schema_version",),
        ("project", "name"),
        ("profile", "release", "compile_lua"),
        ("tools", "unity", "timeout_seconds"),
        ("version_control", "credential"),
        ("object_store", "destination_id"),
        ("publish", "layout", "version_entry_key"),
        ("tasks", "config", "source"),
        ("logging", "retain_days"),
    ],
)
def test_decode_build_config_rejects_missing_fields_with_full_path(
    field_path: tuple[str, ...],
) -> None:
    """验证顶层和嵌套必填字段缺失时报告完整字段路径。

    参数：
        field_path: 要删除的必填字段路径。
    """
    data = _valid_data()
    _nested_mapping(data, field_path[:-1]).pop(field_path[-1])
    expected = ".".join(field_path)

    with pytest.raises(ConfigurationError, match=expected.replace(".", r"\.")):
        decode_build_config(data)


@pytest.mark.parametrize(
    ("field_path", "invalid_value"),
    [
        (("schema_version",), True),
        (("project", "source_root"), 123),
        (("profile", "release", "compile_lua"), 1),
        (("tools", "unity", "timeout_seconds"), True),
        (("version_control", "credential"), ["secret://build/svn"]),
        (("tasks", "config", "enabled"), 1),
        (("logging", "retain_days"), False),
    ],
)
def test_decode_build_config_rejects_wrong_types_with_full_path(
    field_path: tuple[str, ...],
    invalid_value: object,
) -> None:
    """验证严格类型检查拒绝 bool/int 混用及隐式 Path/Secret 转换。

    参数：
        field_path: 待替换字段的完整路径。
        invalid_value: 与 schema 声明类型不一致的值。
    """
    data = _valid_data()
    _nested_mapping(data, field_path[:-1])[field_path[-1]] = invalid_value
    expected = ".".join(field_path)

    with pytest.raises(ConfigurationError, match=expected.replace(".", r"\.")):
        decode_build_config(data)


def test_decode_build_config_preserves_model_validation_as_exception_cause() -> None:
    """验证领域模型的语义错误被包装时保留底层异常 cause。

    SecretRef 的 scheme 错误应定位到 version_control.credential，同时原始
    ``ConfigurationError`` 可通过 ``__cause__`` 诊断。
    """
    data = _valid_data()
    _nested_mapping(data, ("version_control",))["credential"] = "SECRET://wrong"

    with pytest.raises(ConfigurationError) as caught:
        decode_build_config(data)

    assert str(caught.value).startswith("config.version_control.credential:")
    assert isinstance(caught.value.__cause__, ConfigurationError)


@pytest.mark.parametrize(
    ("section", "valid_name", "invalid_name"),
    [
        ("profile", "release", "Release"),
        ("tasks", "config", "Config"),
    ],
)
def test_decode_build_config_reports_dynamic_name_boundary_with_cause(
    section: str,
    valid_name: str,
    invalid_name: str,
) -> None:
    """验证动态 profile/task 非法名称在已知条目边界精确包装。

    参数：
        section: ``profile`` 或 ``tasks`` 动态表。
        valid_name: fixture 中待替换的合法名称。
        invalid_name: 不符合小写条目名规则的名称。

    异常：
        顶层异常以 ``config.<section>.<name>`` 开始，原始
        ``ConfigurationError`` 保留为 cause，不依赖其消息文本反推路径。
    """
    data = _valid_data()
    entries = _nested_mapping(data, (section,))
    entries[invalid_name] = entries.pop(valid_name)

    with pytest.raises(ConfigurationError) as caught:
        decode_build_config(data)

    assert str(caught.value).startswith(f"config.{section}.{invalid_name}:")
    assert isinstance(caught.value.__cause__, ConfigurationError)


def test_canonical_toml_bytes_has_fixed_order_encoding_and_newline() -> None:
    """验证规范 TOML 的顶层/静态/动态顺序与精确字节规则。

    输出必须由 TOML writer 产生，可由 tomli 重新解析；使用 UTF-8 无 BOM、LF，
    末尾恰一个换行，Path 和 SecretRef 输出公开 locator 表示。
    """
    canonical = canonical_toml_bytes(_valid_config(reverse_dynamic_order=True))
    parsed = tomli.loads(canonical.decode("utf-8"))

    assert tuple(parsed) == (
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
    assert tuple(parsed["project"]) == ("name", "source_root", "output_root", "temp_root")
    assert tuple(parsed["profile"]) == ("debug", "release")
    assert tuple(parsed["tasks"]) == ("config", "shader")
    assert parsed["tools"]["unity"]["executable"] == "C:/Unity/Editor/Unity.exe"
    assert parsed["version_control"]["credential"] == "secret://build/svn"
    assert not canonical.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in canonical
    assert canonical.endswith(b"\n")
    assert not canonical.endswith(b"\n\n")


def test_canonical_toml_bytes_matches_complete_golden_document() -> None:
    """锁定完整小型 BuildConfig 在 tomli-w 1.2.0 下的逐字节 TOML。

    Golden 同时覆盖所有顶层表、两个动态 profile、两个动态 task、Path POSIX
    表示、SecretRef locator、布尔/整数格式、表间空行与唯一尾换行。
    """
    expected = b"""schema_version = 1

[project]
name = "spacetime"
source_root = "workspace/source"
output_root = "workspace/output"
temp_root = "workspace/temp"

[profile.debug]
emit_config_txt = true
compile_lua = false
encrypt_lua = false

[profile.release]
emit_config_txt = false
compile_lua = true
encrypt_lua = true

[tools.unity]
executable = "C:/Unity/Editor/Unity.exe"
timeout_seconds = 600

[version_control]
provider = "svn"
credential = "secret://build/svn"

[object_store]
provider = "filesystem"
destination_id = "release-primary"
root = "artifacts"

[publish.layout]
root_prefix = "releases/{branch}"
version_entry_key = "versions/{platform}/entry.json"

[tasks.config]
enabled = true
source = "assets/config"
output = "bundles/config"

[tasks.shader]
enabled = true
source = "assets/shader"
output = "bundles/shader"

[logging]
console_level = "INFO"
file_level = "DEBUG"
show_traceback = false
capture_process_output = true
root = "logs"
retain_days = 14
"""

    assert canonical_toml_bytes(_valid_config()) == expected


def test_canonical_toml_and_snapshot_digest_ignore_dynamic_input_order() -> None:
    """验证动态项插入顺序不影响规范字节和 SHA-256 摘要。

    两个等价 BuildConfig 以不同顺序输入 profile/task 后，应产生逐字节相同输出；
    ``ConfigSnapshot`` 只接受对应规范字节的小写摘要并保留 source 顺序。
    """
    first = _valid_config()
    second = _valid_config(reverse_dynamic_order=True)
    first_bytes = canonical_toml_bytes(first)
    second_bytes = canonical_toml_bytes(second)
    digest = sha256(first_bytes).hexdigest()
    sources = (Path("base.toml"), Path("profile.toml"), Path("environment.toml"))

    snapshot = ConfigSnapshot(first, first_bytes, digest, sources)

    assert first_bytes == second_bytes
    assert snapshot.digest == sha256(second_bytes).hexdigest()
    assert snapshot.sources == sources
    with pytest.raises(ConfigurationError, match="digest"):
        ConfigSnapshot(first, first_bytes, digest.upper(), sources)


def test_config_snapshot_rejects_bytes_from_another_configuration() -> None:
    """验证匹配伪造字节摘要仍不能替代 config 自身的规范 TOML。

    攻击者可以同时提供任意 B 字节与 ``sha256(B)``；快照必须先由 config A 重算
    规范字节并逐字节比较，不能只验证 digest 与调用方字节彼此自洽。
    """
    config = _valid_config()
    forged_bytes = b"schema_version = 1\n"
    forged_digest = sha256(forged_bytes).hexdigest()
    sources = (Path("base.toml"), Path("profile.toml"), Path("environment.toml"))

    with pytest.raises(ConfigurationError, match="canonical_bytes"):
        ConfigSnapshot(config, forged_bytes, forged_digest, sources)


def test_load_layered_config_reads_three_files_merges_validates_and_snapshots(
    tmp_path: Path,
) -> None:
    """验证三文件真实加载按固定优先级生成不可变快照。

    参数：
        tmp_path: pytest 提供的隔离临时目录。

    约束与副作用：
        仅在临时目录写入三个 UTF-8 TOML fixture；生产加载器不得写文件。
    """
    base_path = tmp_path / "base.toml"
    profile_path = tmp_path / "profile.toml"
    environment_path = tmp_path / "environment.toml"
    base_path.write_text(
        """schema_version = 1

[project]
name = "spacetime"
source_root = "workspace/source"
output_root = "workspace/output"
temp_root = "workspace/temp"

[profile.release]
emit_config_txt = false
compile_lua = true
encrypt_lua = false

[tools.unity]
executable = "C:/Unity/Editor/Unity.exe"
timeout_seconds = 600

[version_control]
provider = "svn"
credential = "secret://build/svn"

[object_store]
provider = "filesystem"
destination_id = "release-primary"
root = "artifacts"

[publish.layout]
root_prefix = "releases/{branch}"
version_entry_key = "versions/{platform}/entry.json"

[tasks.config]
enabled = true
source = "assets/config"
output = "bundles/config"

[logging]
console_level = "INFO"
file_level = "DEBUG"
show_traceback = false
capture_process_output = true
root = "logs"
retain_days = 14
""",
        encoding="utf-8",
    )
    profile_path.write_text(
        """[profile.release]
encrypt_lua = true

[logging]
console_level = "WARNING"
""",
        encoding="utf-8",
    )
    environment_path.write_text(
        """[logging]
console_level = "ERROR"
""",
        encoding="utf-8",
    )

    snapshot = load_layered_config(base_path, profile_path, environment_path)

    assert snapshot.config.logging.console_level == "ERROR"
    assert dict(snapshot.config.profiles)["release"].encrypt_lua is True
    assert snapshot.sources == (base_path, profile_path, environment_path)
    assert snapshot.digest == sha256(snapshot.canonical_bytes).hexdigest()
    assert snapshot.canonical_bytes == canonical_toml_bytes(snapshot.config)


def test_load_layered_config_preserves_toml_decode_error_as_cause(tmp_path: Path) -> None:
    """验证 TOML 语法错误转换为配置错误且保留解析器 cause。

    参数：
        tmp_path: pytest 提供的隔离临时目录。
    """
    base_path = tmp_path / "base.toml"
    profile_path = tmp_path / "profile.toml"
    environment_path = tmp_path / "environment.toml"
    base_path.write_text("schema_version = [", encoding="utf-8")
    profile_path.write_text("", encoding="utf-8")
    environment_path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="base") as caught:
        load_layered_config(base_path, profile_path, environment_path)

    assert isinstance(caught.value.__cause__, tomli.TOMLDecodeError)


@pytest.mark.parametrize(
    ("invalid_source_index", "expected_argument"),
    [
        (0, "base_path"),
        (1, "profile_path"),
        (2, "environment_path"),
    ],
)
def test_load_layered_config_preserves_invalid_utf8_as_cause(
    tmp_path: Path,
    invalid_source_index: int,
    expected_argument: str,
) -> None:
    """验证三个 TOML 输入任一包含非法 UTF-8 时转换并保留解码 cause。

    参数：
        tmp_path: pytest 提供的隔离临时目录。
        invalid_source_index: 要写入非法字节的 base/profile/environment 位置。
        expected_argument: 顶层异常必须定位的加载参数名。

    异常：
        预期 ``ConfigurationError``，其 ``__cause__`` 必须是原始
        ``UnicodeDecodeError``，不得把编码错误泄漏给 API 调用方。
    """
    paths = (
        tmp_path / "base.toml",
        tmp_path / "profile.toml",
        tmp_path / "environment.toml",
    )
    paths[0].write_bytes(canonical_toml_bytes(_valid_config()))
    paths[1].write_bytes(b"")
    paths[2].write_bytes(b"")
    paths[invalid_source_index].write_bytes(b"\xff")

    with pytest.raises(ConfigurationError, match=expected_argument) as caught:
        load_layered_config(*paths)

    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


def test_load_layered_config_rejects_non_path_missing_and_directory_sources(
    tmp_path: Path,
) -> None:
    """验证三个配置源必须是 Path 且必须指向现有普通文件。

    参数：
        tmp_path: 同时提供合法目录和不存在路径的隔离根。

    异常：
        每种非法来源都应在读取前抛 ``ConfigurationError``。
    """
    valid = tmp_path / "valid.toml"
    valid.write_text("", encoding="utf-8")
    missing = tmp_path / "missing.toml"

    with pytest.raises(ConfigurationError, match="base_path"):
        load_layered_config(cast(Path, "base.toml"), valid, valid)
    with pytest.raises(ConfigurationError, match="profile_path"):
        load_layered_config(valid, missing, valid)
    with pytest.raises(ConfigurationError, match="environment_path"):
        load_layered_config(valid, valid, tmp_path)
