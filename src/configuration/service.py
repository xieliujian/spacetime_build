"""构建配置的安全读写、任务编辑与 Profile 解析服务。

本模块向编辑器和应用层提供无状态的 ``BuildConfigService``。服务只组合现有
loader、validator 与不可变配置模型，不复制 TOML schema 或跨字段校验规则；读取
和保存配置不会启动进程、访问版本控制、解析秘密或上传资源。
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast

import tomli

from configuration import loader, validator
from configuration.model import BuildConfig, ResolvedBuildConfig, TaskConfig
from configuration.validator import ValidationReport
from core.errors import ConfigurationError


def _require_path(value: object, argument_name: str) -> Path:
    """校验公开文件 API 收到运行时真实的 ``Path``。

    参数：
        value: 调用方传入的路径对象。
        argument_name: 用于稳定错误定位的参数名。

    返回：
        未经解析或改写的原 ``Path``。

    异常：
        ``value`` 不是 ``pathlib.Path`` 实例时抛 ``ConfigurationError``。

    约束与副作用：
        不接受字符串路径；不读取文件元数据，也不创建目录或文件。
    """
    if not isinstance(value, Path):
        raise ConfigurationError(f"{argument_name} 必须是 pathlib.Path")
    return value


def _require_existing_file(path: Path) -> None:
    """确认加载目标存在且是普通文件。

    参数：
        path: 已通过运行时 ``Path`` 类型检查的加载路径。

    返回：
        ``None``。

    异常：
        路径不存在或不是普通文件时抛 ``ConfigurationError``；元数据读取失败时
        同样映射为 ``ConfigurationError`` 并保留原异常 cause。

    约束与副作用：
        ``stat`` 跟随符号链接，但只读取元数据，不打开或修改目标。
    """
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ConfigurationError(f"配置文件不可访问: {path}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError(f"配置路径必须指向现有普通文件: {path}")


def _require_save_destination(path: Path) -> None:
    """确认保存目标的父目录已存在且目标不是目录。

    参数：
        path: 已通过运行时 ``Path`` 类型检查的保存路径。

    返回：
        ``None``。

    异常：
        父路径缺失、不是目录、目标已是非普通文件，或元数据读取失败时抛
        ``ConfigurationError``；底层 I/O 失败保留为 cause。

    约束与副作用：
        只读取元数据；不会自动创建父目录，也不会修改现有目标。
    """
    try:
        parent_metadata = path.parent.stat()
    except OSError as exc:
        raise ConfigurationError(f"配置父目录不可访问: {path.parent}: {exc}") from exc
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise ConfigurationError(f"配置父路径必须是现有目录: {path.parent}")

    try:
        target_metadata = path.stat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConfigurationError(f"配置目标不可访问: {path}: {exc}") from exc
    if not stat.S_ISREG(target_metadata.st_mode):
        raise ConfigurationError(f"配置目标必须是普通文件或尚不存在: {path}")


def _raise_for_issues(report: ValidationReport) -> None:
    """把校验报告中的全部问题汇总为稳定异常消息。

    参数：
        report: validator 已按 UTF-8 路径和消息排序的不可变报告。

    返回：
        报告无问题时返回 ``None``。

    异常：
        报告含任意 issue 时抛一个 ``ConfigurationError``，逐行保留所有问题。

    约束与副作用：
        不重新排序、不去重、不执行第二套校验，也不记录可能含秘密的配置值。
    """
    if report.is_valid:
        return
    details = "\n".join(f"- {issue.path}: {issue.message}" for issue in report.issues)
    raise ConfigurationError(f"配置校验失败:\n{details}")


def _cleanup_temporary_file(path: Path) -> OSError | None:
    """尽力删除本次保存创建的精确临时文件。

    参数：
        path: ``mkstemp`` 返回且仅归属于本次保存调用的文件路径。

    返回：
        删除成功或文件已不存在时返回 ``None``；删除失败时返回原 ``OSError``。

    异常：
        不直接抛异常，便于调用方优先保留触发保存失败的原始 cause。

    约束与副作用：
        只删除精确路径，不使用通配符、不扫描目录，也不触碰目标配置文件。
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return exc
    return None


def _write_and_sync(stream: BinaryIO, content: bytes) -> None:
    """写入全部规范字节并把文件内容同步到存储设备。

    参数：
        stream: 以排他方式创建的同目录二进制临时文件流。
        content: ``canonical_toml_bytes`` 生成的完整配置字节。

    返回：
        ``None``。

    异常：
        写入、刷新、取得描述符或 ``fsync`` 失败时透传底层异常，由保存入口统一映射。

    约束与副作用：
        只修改临时文件；调用返回前已执行 ``write``、``flush`` 和 ``os.fsync``。
    """
    stream.write(content)
    stream.flush()
    os.fsync(stream.fileno())


class BuildConfigService:
    """无状态的构建配置服务门面。

    职责：
        组合严格 TOML loader、纯 validator 与不可变模型，提供安全加载、原子保存、
        任务开关编辑和精确 Profile 解析。

    参数：
        无；实例不保存依赖、缓存或配置状态。

    返回：
        通过各公开方法返回不可变模型或校验报告。

    异常：
        参数、配置内容或文件操作无效时抛 ``ConfigurationError``；底层解析或 I/O
        错误通过异常 cause 保留。

    约束与副作用：
        仅 ``load`` 读取文件、``save`` 原子替换文件；不会调用外部进程、版本控制、
        Jenkins、秘密服务或对象存储。
    """

    __slots__ = ()

    def load(self, path: Path) -> BuildConfig:
        """从单个现有普通 TOML 文件加载并校验完整构建配置。

        参数：
            path: 运行时必须为 ``Path`` 且指向现有普通文件的配置路径。

        返回：
            经 ``decode_build_config`` 解码且通过纯 validator 的不可变配置。

        异常：
            路径、UTF-8、TOML、schema、模型或跨字段校验失败时抛
            ``ConfigurationError``；解析和 I/O 原异常保留为 cause。

        约束与副作用：
            只读取指定文件；不探测工具、不执行任务、不访问版本控制或上传端口。
        """
        validated_path = _require_path(cast(object, path), "path")
        _require_existing_file(validated_path)
        try:
            with validated_path.open("rb") as stream:
                document = cast(dict[str, object], tomli.load(stream))
        except (OSError, UnicodeDecodeError, tomli.TOMLDecodeError) as exc:
            raise ConfigurationError(f"配置文件加载失败: {validated_path}: {exc}") from exc

        config = loader.decode_build_config(document)
        _raise_for_issues(self.validate(config))
        return config

    def validate(self, config: BuildConfig) -> ValidationReport:
        """委托现有纯 validator 汇总配置问题。

        参数：
            config: 待校验的完整不可变构建配置。

        返回：
            ``validator.validate_build_config`` 返回的稳定 ``ValidationReport``。

        异常：
            ``config`` 运行时类型错误时由 validator 抛 ``ConfigurationError``。

        约束与副作用：
            不复制校验规则，不访问文件系统或外部系统，也不修改配置。
        """
        return validator.validate_build_config(config)

    def save(self, path: Path, config: BuildConfig) -> None:
        """校验配置并以同目录临时文件原子保存规范 TOML。

        参数：
            path: 运行时必须为 ``Path`` 的目标文件；父目录必须已存在且是目录。
            config: 待保存的完整不可变构建配置。

        返回：
            ``None``；成功后目标字节精确等于 ``canonical_toml_bytes(config)``。

        异常：
            配置无效时在写入前抛 ``ConfigurationError``；路径、序列化、排他创建、
            写入、同步或原子替换失败时统一抛 ``ConfigurationError`` 并保留 cause。

        约束与副作用：
            不创建父目录。临时文件在目标同目录排他创建，写入后 flush、fsync 并关闭，
            最后通过 ``os.replace`` 替换；失败时保留旧目标并清理本次精确临时文件。
        """
        report = self.validate(config)
        _raise_for_issues(report)
        validated_path = _require_path(cast(object, path), "path")
        _require_save_destination(validated_path)

        try:
            content = loader.canonical_toml_bytes(config)
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError(f"配置规范序列化失败: {validated_path}: {exc}") from exc

        descriptor = -1
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{validated_path.name}.",
                suffix=".tmp",
                dir=validated_path.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                _write_and_sync(stream, content)
            os.replace(temporary_path, validated_path)
            temporary_path = None
        except Exception as exc:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            cleanup_error = (
                _cleanup_temporary_file(temporary_path) if temporary_path is not None else None
            )
            if cleanup_error is not None:
                raise ConfigurationError(
                    f"配置保存失败且临时文件清理失败: {validated_path}: {cleanup_error}"
                ) from exc
            raise ConfigurationError(f"配置保存失败: {validated_path}: {exc}") from exc

    def update_task(
        self,
        config: BuildConfig,
        name: str,
        task_config: TaskConfig,
    ) -> BuildConfig:
        """按精确名称替换已有任务并返回新的完整配置。

        参数：
            config: 原不可变完整构建配置。
            name: 必须与已有任务名称大小写精确一致的非空字符串。
            task_config: 用于替换的完整 ``TaskConfig``。

        返回：
            通过 ``dataclasses.replace`` 构造、任务按 ``BuildConfig`` 规则排序的新对象。

        异常：
            参数运行时类型错误、名称非法或任务不存在时抛 ``ConfigurationError``。

        约束与副作用：
            不修改原配置或原任务元组，不新增任务，也不自动执行跨字段校验。
        """
        _require_build_config(config)
        validated_name = _require_name(name)
        if not isinstance(cast(object, task_config), TaskConfig):
            raise ConfigurationError("task_config 必须是 TaskConfig")

        found = False
        updated_tasks: list[tuple[str, TaskConfig]] = []
        for task_name, current_task in config.tasks:
            if task_name == validated_name:
                found = True
                updated_tasks.append((task_name, task_config))
            else:
                updated_tasks.append((task_name, current_task))
        if not found:
            raise ConfigurationError(f"任务不存在: {validated_name}")
        return replace(config, tasks=tuple(updated_tasks))

    def enable_task(self, config: BuildConfig, name: str) -> BuildConfig:
        """幂等启用精确名称匹配的已有任务。

        参数：
            config: 原不可变完整构建配置。
            name: 必须精确存在的任务名称。

        返回：
            任务已启用时允许返回原对象；否则返回只替换该任务开关的新配置。

        异常：
            参数运行时类型错误、名称非法或任务不存在时抛 ``ConfigurationError``。

        约束与副作用：
            通过 ``update_task`` 执行替换，不修改原对象，也不执行或持久化任务。
        """
        task = _find_task(config, name)
        if task.enabled:
            return config
        return self.update_task(config, name, replace(task, enabled=True))

    def disable_task(self, config: BuildConfig, name: str) -> BuildConfig:
        """幂等禁用精确名称匹配的已有任务。

        参数：
            config: 原不可变完整构建配置。
            name: 必须精确存在的任务名称。

        返回：
            任务已禁用时允许返回原对象；否则返回只替换该任务开关的新配置。

        异常：
            参数运行时类型错误、名称非法或任务不存在时抛 ``ConfigurationError``。

        约束与副作用：
            通过 ``update_task`` 执行替换，不修改原对象，也不执行或持久化任务。
        """
        task = _find_task(config, name)
        if not task.enabled:
            return config
        return self.update_task(config, name, replace(task, enabled=False))

    def resolve_profile(self, config: BuildConfig, name: str) -> ResolvedBuildConfig:
        """按区分大小写的精确名称解析一个 Profile。

        参数：
            config: 包含候选 Profile 的完整不可变构建配置。
            name: 必须与已有 Profile 名称精确一致的非空字符串。

        返回：
            绑定原完整配置、精确名称和对应 Profile 的 ``ResolvedBuildConfig``。

        异常：
            参数运行时类型错误、名称非法或 Profile 不存在时抛 ``ConfigurationError``。

        约束与副作用：
            不执行大小写折叠或模糊匹配，不重新加载、合并或修改配置。
        """
        _require_build_config(config)
        validated_name = _require_name(name)
        for profile_name, profile in config.profiles:
            if profile_name == validated_name:
                return ResolvedBuildConfig(config, profile_name, profile)
        raise ConfigurationError(f"Profile 不存在: {validated_name}")


def _require_build_config(config: object) -> BuildConfig:
    """防御性确认服务编辑 API 收到 ``BuildConfig``。

    参数：
        config: 调用方传入的运行时对象。

    返回：
        原 ``BuildConfig`` 对象。

    异常：
        类型不符时抛 ``ConfigurationError``。

    约束与副作用：
        不重新构造、不校验跨字段规则，也不修改配置。
    """
    if not isinstance(config, BuildConfig):
        raise ConfigurationError("config 必须是 BuildConfig")
    return config


def _require_name(name: object) -> str:
    """防御性确认任务或 Profile 名称是非空字符串。

    参数：
        name: 调用方传入的运行时名称。

    返回：
        保持原大小写和空白的名称，以便后续执行精确匹配。

    异常：
        类型错误或仅包含空白时抛 ``ConfigurationError``。

    约束与副作用：
        不裁剪、不做 casefold、不复制模型名称 schema，也不访问配置外状态。
    """
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError("name 必须是非空 str")
    return name


def _find_task(config: BuildConfig, name: str) -> TaskConfig:
    """按精确名称查找任务，并统一处理运行时参数错误。

    参数：
        config: 待查询的完整构建配置。
        name: 待精确匹配的任务名称。

    返回：
        与名称关联的不可变 ``TaskConfig``。

    异常：
        配置或名称类型无效、名称为空或任务不存在时抛 ``ConfigurationError``。

    约束与副作用：
        只遍历已排序任务元组，不修改配置，也不执行任务。
    """
    validated_config = _require_build_config(cast(object, config))
    validated_name = _require_name(cast(object, name))
    for task_name, task in validated_config.tasks:
        if task_name == validated_name:
            return task
    raise ConfigurationError(f"任务不存在: {validated_name}")
