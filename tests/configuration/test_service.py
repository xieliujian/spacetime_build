"""构建配置服务的文件事务、不可变编辑与纯边界测试。

本模块使用真实临时目录和真实 TOML 字节验证服务的可观察契约。测试替身仅注入
不可控的文件打开、flush、fsync 与原子替换失败，不替换 loader、validator 或模型
行为，也不启动进程、访问版本控制、Jenkins、秘密服务或上传端口。
"""

from __future__ import annotations

import ast
import os
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, NoReturn, cast

import pytest
import tomli

import configuration.service as service_module
from configuration import BuildConfigService
from configuration.loader import canonical_toml_bytes
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


def _valid_config() -> BuildConfig:
    """创建通过模型与纯 validator 的完整不可变配置。

    返回：
        含两个 Profile 和两个任务的 ``BuildConfig``。

    约束与副作用：
        路径仅作为配置值使用，不访问 Unity、文件系统或任何外部服务。
    """
    return BuildConfig(
        schema_version=1,
        project=ProjectConfig(
            name="spacetime",
            source_root=Path("workspace/source"),
            output_root=Path("workspace/output"),
            temp_root=Path("workspace/temp"),
        ),
        profiles=(
            ("release", ProfileConfig(False, True, True)),
            ("debug", ProfileConfig(True, False, False)),
        ),
        unity=UnityToolConfig(Path("C:/Unity/Editor/Unity.exe"), 600),
        version_control=VersionControlConfig("svn", SecretRef("secret://build/svn")),
        object_store=ObjectStoreConfig("filesystem", "release-primary", Path("artifacts")),
        publish_layout=PublishLayoutConfig(
            "releases/{branch}",
            "versions/{platform}/entry.json",
        ),
        tasks=(
            ("shader", TaskConfig(True, "assets/shader", "bundles/shader")),
            ("config", TaskConfig(True, "assets/config", "bundles/config")),
        ),
        logging=LoggingConfig("INFO", "DEBUG", False, True, Path("logs"), 14),
    )


def _invalid_config() -> BuildConfig:
    """创建模型允许但纯 validator 会拒绝的配置。

    返回：
        Unity 可执行路径为相对路径的 ``BuildConfig``。

    约束与副作用：
        只替换不可变值对象，不探测该路径是否存在。
    """
    return replace(
        _valid_config(),
        unity=UnityToolConfig(Path("relative/unity"), 600),
    )


def _temporary_files(target: Path) -> set[Path]:
    """取得服务为指定目标命名的当前同目录临时文件集合。

    参数：
        target: 配置目标路径。

    返回：
        匹配 ``.<目标名>.*.tmp`` 的精确 ``Path`` 集合。

    约束与副作用：
        只扫描目标父目录，不创建、修改或删除任何文件。
    """
    return set(target.parent.glob(f".{target.name}.*.tmp"))


class _FlushFailingStream:
    """在真实临时文件首次 flush 时注入 ``OSError`` 的最小文件边界替身。

    参数：
        wrapped: ``os.fdopen`` 创建的真实二进制临时文件流。

    约束与副作用：
        write、fileno 和 close 均委托真实流；只把 flush 这一不可控 OS 边界改为失败。
    """

    def __init__(self, wrapped: BinaryIO) -> None:
        """保存待委托的真实二进制流。

        参数：
            wrapped: 已接管临时文件描述符的真实流。

        约束与副作用：
            不执行 I/O；流所有权由当前替身的上下文管理器接管。
        """
        self._wrapped = wrapped

    def __enter__(self) -> _FlushFailingStream:
        """返回当前上下文管理器实例而不额外打开文件。

        返回：
            当前 ``_FlushFailingStream``。

        约束与副作用：
            不修改底层流。
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出上下文时始终关闭真实临时文件流。

        参数：
            exc_type: 活跃异常类型或 ``None``。
            exc_value: 活跃异常对象或 ``None``。
            traceback: 活跃异常 traceback 或 ``None``。

        返回：
            ``None``，不吞掉活跃异常。

        约束与副作用：
            关闭底层文件描述符，确保 Windows 上临时文件随后可被精确清理。
        """
        del exc_type, exc_value, traceback
        self._wrapped.close()

    def write(self, content: bytes) -> int:
        """把规范配置字节写入真实临时文件。

        参数：
            content: 待写入的规范 TOML 字节。

        返回：
            底层二进制流报告的写入字节数。

        异常：
            底层写入失败时透传对应异常。

        约束与副作用：
            只修改本次保存的真实临时文件。
        """
        return self._wrapped.write(content)

    def flush(self) -> NoReturn:
        """模拟临时文件 flush 的不可控 OS 失败。

        异常：
            总是抛 ``OSError``。

        约束与副作用：
            不刷新底层流，后续上下文退出仍会关闭真实描述符。
        """
        raise OSError("injected flush failure")

    def fileno(self) -> int:
        """返回真实临时文件描述符。

        返回：
            底层流的整数文件描述符。

        异常：
            流已关闭时透传底层异常。

        约束与副作用：
            不复制或关闭描述符。
        """
        return self._wrapped.fileno()


def test_load_reads_valid_canonical_toml_without_external_side_effects(tmp_path: Path) -> None:
    """验证 load 从真实普通文件完成 TOML 解码和纯校验。

    参数：
        tmp_path: pytest 提供的独立临时目录。
    """
    expected = _valid_config()
    path = tmp_path / "build.toml"
    path.write_bytes(canonical_toml_bytes(expected))

    loaded = BuildConfigService().load(path)

    assert loaded == expected


@pytest.mark.parametrize("invalid_path", ["build.toml", object()])
def test_load_rejects_non_path_runtime_values(invalid_path: object) -> None:
    """验证 load 不把字符串或任意对象隐式转换为路径。

    参数：
        invalid_path: 运行时不是 ``Path`` 的输入值。
    """
    with pytest.raises(ConfigurationError, match="pathlib.Path"):
        BuildConfigService().load(cast(Path, invalid_path))


def test_load_rejects_missing_path_and_directory(tmp_path: Path) -> None:
    """验证 load 只接受已存在普通文件，而不接受缺失路径或目录。

    参数：
        tmp_path: 同时充当目录反例和缺失文件父目录。
    """
    with pytest.raises(ConfigurationError) as missing:
        BuildConfigService().load(tmp_path / "missing.toml")
    assert isinstance(missing.value.__cause__, FileNotFoundError)

    with pytest.raises(ConfigurationError, match="普通文件") as directory:
        BuildConfigService().load(tmp_path)
    assert directory.value.__cause__ is None


def test_load_preserves_file_open_io_failure_as_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证真实文件通过 stat 后，打开失败仍统一映射并保留 cause。

    参数：
        tmp_path: 保存可 stat 普通文件的临时目录。
        monkeypatch: 仅在不可控文件打开边界注入 ``PermissionError``。
    """
    path = tmp_path / "build.toml"
    path.write_bytes(canonical_toml_bytes(_valid_config()))

    def fail_open(
        _path: Path,
        _mode: str,
    ) -> NoReturn:
        """模拟操作系统拒绝打开已存在普通文件。

        参数：
            _path: 被打开的路径实例。
            _mode: 服务请求的二进制读取模式。

        异常：
            总是抛 ``PermissionError``。
        """
        raise PermissionError("injected open failure")

    monkeypatch.setattr(Path, "open", fail_open)

    with pytest.raises(ConfigurationError) as caught:
        BuildConfigService().load(path)
    assert isinstance(caught.value.__cause__, PermissionError)


@pytest.mark.parametrize(
    ("content", "cause_type"),
    [
        (b"\xff", UnicodeDecodeError),
        (b"schema_version = [", tomli.TOMLDecodeError),
    ],
)
def test_load_maps_utf8_and_toml_errors_with_original_cause(
    tmp_path: Path,
    content: bytes,
    cause_type: type[BaseException],
) -> None:
    """验证非法 UTF-8 与 TOML 均映射为带原 cause 的配置异常。

    参数：
        tmp_path: 保存非法配置字节的临时目录。
        content: 非法 UTF-8 或非法 TOML 输入。
        cause_type: 期望保留的解析异常类型。
    """
    path = tmp_path / "invalid.toml"
    path.write_bytes(content)

    with pytest.raises(ConfigurationError) as caught:
        BuildConfigService().load(path)
    assert isinstance(caught.value.__cause__, cause_type)


def test_load_preserves_schema_model_failure_chain(tmp_path: Path) -> None:
    """验证 schema 模型失败沿用 loader 的异常链而不被服务吞掉。

    参数：
        tmp_path: 保存 schema 版本错误 TOML 的临时目录。
    """
    content = canonical_toml_bytes(_valid_config()).replace(
        b"schema_version = 1",
        b"schema_version = 2",
        1,
    )
    path = tmp_path / "invalid-schema.toml"
    path.write_bytes(content)

    with pytest.raises(ConfigurationError, match="schema_version") as caught:
        BuildConfigService().load(path)
    assert isinstance(caught.value.__cause__, ConfigurationError)


def test_load_stably_aggregates_validation_issues(tmp_path: Path) -> None:
    """验证解码后 validator issue 按稳定路径和消息汇总后失败。

    参数：
        tmp_path: 保存模型合法但跨字段无效 TOML 的临时目录。
    """
    path = tmp_path / "invalid-validation.toml"
    path.write_bytes(canonical_toml_bytes(_invalid_config()))

    with pytest.raises(ConfigurationError) as caught:
        BuildConfigService().load(path)
    assert str(caught.value) == (
        "配置校验失败:\n- tools.unity.executable: 必须是绝对路径；工具存在性由外部集成检查"
    )
    assert caught.value.__cause__ is None


def test_validate_matches_the_existing_pure_validator_contract() -> None:
    """验证服务 validate 与现有 validator 返回完全相同的稳定报告。

    不替换 validator 或断言内部调用次数，避免把测试绑定到实现交互细节。
    """
    config = _invalid_config()

    assert BuildConfigService().validate(config) == validate_build_config(config)
    with pytest.raises(ConfigurationError, match="BuildConfig"):
        BuildConfigService().validate(cast(BuildConfig, object()))


def test_save_writes_exact_canonical_bytes_and_preserves_unrelated_temp(
    tmp_path: Path,
) -> None:
    """验证 save 原子落盘规范字节且唯一临时名不覆盖无关文件。

    参数：
        tmp_path: 保存目标与预存无关临时文件的真实目录。
    """
    target = tmp_path / "build.toml"
    unrelated = tmp_path / ".build.toml.external.tmp"
    unrelated.write_bytes(b"external")
    config = _valid_config()

    BuildConfigService().save(target, config)

    assert target.read_bytes() == canonical_toml_bytes(config)
    assert unrelated.read_bytes() == b"external"
    assert _temporary_files(target) == {unrelated}


def test_save_rejects_invalid_config_before_writing(tmp_path: Path) -> None:
    """验证配置有 issue 时不创建临时文件且不覆盖旧目标。

    参数：
        tmp_path: 保存旧目标字节的真实目录。
    """
    target = tmp_path / "build.toml"
    target.write_bytes(b"old")

    with pytest.raises(ConfigurationError, match="配置校验失败"):
        BuildConfigService().save(target, _invalid_config())

    assert target.read_bytes() == b"old"
    assert not _temporary_files(target)


def test_save_requires_existing_parent_directory(tmp_path: Path) -> None:
    """验证 save 不自动创建缺失父目录。

    参数：
        tmp_path: 提供尚不存在的子目录父路径。
    """
    parent = tmp_path / "missing"
    target = parent / "build.toml"

    with pytest.raises(ConfigurationError) as caught:
        BuildConfigService().save(target, _valid_config())

    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert not parent.exists()


def test_save_flush_failure_preserves_old_target_and_cleans_only_own_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证临时流 flush 失败保留旧目标并精确清理本次临时文件。

    参数：
        tmp_path: 保存旧目标与无关临时文件的目录。
        monkeypatch: 仅替换 ``os.fdopen`` 文件边界以返回 flush 失败流。
    """
    target = tmp_path / "build.toml"
    target.write_bytes(b"old")
    unrelated = tmp_path / ".build.toml.external.tmp"
    unrelated.write_bytes(b"external")
    real_fdopen = os.fdopen

    def failing_fdopen(file_descriptor: int, mode: str) -> _FlushFailingStream:
        """用真实 fdopen 流构造仅 flush 失败的边界替身。

        参数：
            file_descriptor: ``mkstemp`` 排他创建的真实描述符。
            mode: 服务请求的二进制写入模式。

        返回：
            接管真实流所有权的 ``_FlushFailingStream``。
        """
        return _FlushFailingStream(cast(BinaryIO, real_fdopen(file_descriptor, mode)))

    monkeypatch.setattr(service_module.os, "fdopen", failing_fdopen)

    with pytest.raises(ConfigurationError) as caught:
        BuildConfigService().save(target, _valid_config())

    assert isinstance(caught.value.__cause__, OSError)
    assert target.read_bytes() == b"old"
    assert unrelated.read_bytes() == b"external"
    assert _temporary_files(target) == {unrelated}


def test_save_fsync_failure_preserves_old_target_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 fsync 失败保留旧目标、保留 cause 且不残留临时文件。

    参数：
        tmp_path: 保存旧目标的真实目录。
        monkeypatch: 仅在不可控 ``os.fsync`` 边界注入失败。
    """
    target = tmp_path / "build.toml"
    target.write_bytes(b"old")

    def fail_fsync(_file_descriptor: int) -> NoReturn:
        """模拟操作系统同步临时文件失败。

        参数：
            _file_descriptor: 已 flush 的真实临时文件描述符。

        异常：
            总是抛 ``OSError``。
        """
        raise OSError("injected fsync failure")

    monkeypatch.setattr(service_module.os, "fsync", fail_fsync)

    with pytest.raises(ConfigurationError) as caught:
        BuildConfigService().save(target, _valid_config())

    assert isinstance(caught.value.__cause__, OSError)
    assert target.read_bytes() == b"old"
    assert not _temporary_files(target)


def test_save_replace_failure_preserves_old_target_and_cleans_only_own_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证原子替换失败保留旧目标并且只清理本次临时文件。

    参数：
        tmp_path: 保存旧目标与调用方无关临时文件的真实目录。
        monkeypatch: 仅在不可控 ``os.replace`` 边界注入失败。
    """
    target = tmp_path / "build.toml"
    target.write_bytes(b"old")
    unrelated = tmp_path / ".build.toml.external.tmp"
    unrelated.write_bytes(b"external")

    def fail_replace(_source: Path, _destination: Path) -> NoReturn:
        """模拟操作系统拒绝原子替换。

        参数：
            _source: 已同步并关闭的本次临时文件。
            _destination: 应保持旧内容的目标文件。

        异常：
            总是抛 ``OSError``。
        """
        raise OSError("injected replace failure")

    monkeypatch.setattr(service_module.os, "replace", fail_replace)

    with pytest.raises(ConfigurationError) as caught:
        BuildConfigService().save(target, _valid_config())

    assert isinstance(caught.value.__cause__, OSError)
    assert target.read_bytes() == b"old"
    assert unrelated.read_bytes() == b"external"
    assert _temporary_files(target) == {unrelated}


def test_update_task_returns_sorted_new_config_without_mutating_original() -> None:
    """验证任务替换返回新聚合根且原对象和未替换任务保持不变。"""
    service = BuildConfigService()
    original = _valid_config()
    replacement = TaskConfig(False, "new/source", "new/output")

    updated = service.update_task(original, "config", replacement)

    assert updated is not original
    assert tuple(name for name, _ in updated.tasks) == ("config", "shader")
    assert dict(updated.tasks)["config"] == replacement
    assert dict(original.tasks)["config"] == TaskConfig(
        True,
        "assets/config",
        "bundles/config",
    )
    assert dict(updated.tasks)["shader"] is dict(original.tasks)["shader"]


def test_update_task_rejects_unknown_case_and_runtime_types() -> None:
    """验证任务名称精确匹配并防御 config、name、task_config 运行时类型错误。"""
    service = BuildConfigService()
    config = _valid_config()
    task = TaskConfig(True, "source", "output")

    for unknown in ("CONFIG", "missing"):
        with pytest.raises(ConfigurationError, match="任务不存在"):
            service.update_task(config, unknown, task)
    with pytest.raises(ConfigurationError, match="config"):
        service.update_task(cast(BuildConfig, object()), "config", task)
    with pytest.raises(ConfigurationError, match="name"):
        service.update_task(config, cast(str, object()), task)
    with pytest.raises(ConfigurationError, match="task_config"):
        service.update_task(config, "config", cast(TaskConfig, object()))


def test_enable_and_disable_task_are_immutable_idempotent_and_exact() -> None:
    """验证启禁任务只替换 enabled、允许返回原对象且未知名称失败。"""
    service = BuildConfigService()
    enabled = _valid_config()

    assert service.enable_task(enabled, "config") is enabled
    disabled = service.disable_task(enabled, "config")
    assert disabled is not enabled
    assert dict(disabled.tasks)["config"] == replace(
        dict(enabled.tasks)["config"],
        enabled=False,
    )
    assert dict(enabled.tasks)["config"].enabled is True
    assert service.disable_task(disabled, "config") is disabled
    reenabled = service.enable_task(disabled, "config")
    assert dict(reenabled.tasks)["config"] == dict(enabled.tasks)["config"]

    for operation in (service.enable_task, service.disable_task):
        with pytest.raises(ConfigurationError, match="任务不存在"):
            operation(enabled, "CONFIG")


def test_resolve_profile_requires_exact_valid_name_and_preserves_config() -> None:
    """验证 Profile 解析精确绑定原配置，未知或非法名称统一失败。"""
    service = BuildConfigService()
    config = _valid_config()

    resolved = service.resolve_profile(config, "release")

    assert resolved.config is config
    assert resolved.profile_name == "release"
    assert resolved.profile == dict(config.profiles)["release"]
    for invalid_name in ("Release", "missing", " "):
        with pytest.raises(ConfigurationError):
            service.resolve_profile(config, invalid_name)
    with pytest.raises(ConfigurationError, match="name"):
        service.resolve_profile(config, cast(str, object()))


def test_service_source_has_no_process_vcs_jenkins_or_upload_dependencies() -> None:
    """验证配置服务源码没有导入或动态调用任何外部执行与发布依赖。"""
    source_path = Path(service_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    forbidden_roots = {
        "subprocess",
        "integrations",
        "ports.process",
        "ports.source_control",
        "ports.jenkins",
        "ports.object_store",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    assert imported.isdisjoint(forbidden_roots)
