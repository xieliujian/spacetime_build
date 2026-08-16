"""完整构建配置的纯内存跨字段校验与稳定问题报告。

模型层负责单字段类型和局部值约束，本模块在配置进入规划或集成层前汇总路径
安全、Profile 组合和任务冲突等语义问题。校验过程不访问文件系统，不探测 Unity
是否存在，也不连接版本控制、秘密服务或对象存储。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from configuration.model import BuildConfig, TaskConfig
from core.errors import ConfigurationError

_WINDOWS_DRIVE_PREFIX_PATTERN = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """单个可定位配置问题的不可变值对象。

    参数：
        path: 使用点分 schema 名称定位的非空字段路径。
        message: 面向配置维护者的非空问题说明。

    异常：
        path 或 message 不是非空字符串时抛 ``ConfigurationError``。

    约束与副作用：
        对象冻结并使用 slots；不携带原始秘密值，也不产生外部副作用。
    """

    path: str
    message: str

    def __post_init__(self) -> None:
        """校验问题路径与消息是非空字符串。

        返回：
            ``None``。

        异常：
            任一字段类型错误或 ``strip`` 后为空时抛 ``ConfigurationError``。

        约束与副作用：
            保留合法文本原样，不裁剪、不记录日志。
        """
        for field_name, value in (("path", self.path), ("message", self.message)):
            if not isinstance(cast(object, value), str) or not value.strip():
                raise ConfigurationError(f"ValidationIssue.{field_name} 必须是非空 str")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """按 UTF-8 路径和消息稳定排序的不可变校验报告。

    参数：
        issues: 零个或多个 ``ValidationIssue`` 组成的 tuple。

    异常：
        issues 不是 tuple 或包含其他类型时抛 ``ConfigurationError``。

    约束与副作用：
        构造时仅规范化顺序，不去重；相同输入问题集合产生相同报告顺序。
    """

    issues: tuple[ValidationIssue, ...]

    def __post_init__(self) -> None:
        """校验 issue 集合形状并写回稳定排序的新元组。

        返回：
            ``None``。

        异常：
            issues 运行时形状错误时抛 ``ConfigurationError``。

        约束与副作用：
            只通过 ``object.__setattr__`` 更新当前冻结对象，不修改调用方元组。
        """
        issues_object = cast(object, self.issues)
        if not isinstance(issues_object, tuple):
            raise ConfigurationError("ValidationReport.issues 必须是 tuple")
        issues_tuple = cast(tuple[object, ...], issues_object)
        if any(not isinstance(issue, ValidationIssue) for issue in issues_tuple):
            raise ConfigurationError("ValidationReport.issues 只能包含 ValidationIssue")
        sorted_issues = tuple(
            sorted(
                cast(tuple[ValidationIssue, ...], issues_tuple),
                key=lambda issue: (issue.path.encode("utf-8"), issue.message.encode("utf-8")),
            )
        )
        object.__setattr__(self, "issues", sorted_issues)

    @property
    def is_valid(self) -> bool:
        """返回报告是否没有任何校验问题。

        返回：
            ``issues`` 为空时为真，否则为假。

        异常：
            无。

        约束与副作用：
            只读取冻结元组，不重新执行校验。
        """
        return not self.issues


def _project_path_issue(path: Path) -> str | None:
    """检查项目工作路径是否为无点段的受约束相对路径。

    参数：
        path: ProjectConfig 中的 Path 值对象。

    返回：
        合法时返回 ``None``；否则返回稳定问题说明。

    约束与副作用：
        只检查 Path 的词法属性和 parts，不 resolve、不 stat、不访问文件系统。
    """
    if path.is_absolute() or path.drive or path.root:
        return "必须是相对路径，不得包含绝对路径或盘符根"
    if not path.parts or path == Path("."):
        return "不得是空路径或 . 当前目录"
    if any(part in {".", ".."} for part in path.parts):
        return "不得包含 . 或 .. 路径段"
    return None


def _safe_relative_template_path_issue(value: str) -> str | None:
    """按 TaskConfig 安全规则检查允许模板花括号的相对逻辑路径。

    参数：
        value: 发布根前缀或版本入口 key。

    返回：
        合法时返回 ``None``；否则返回稳定问题说明。

    约束与副作用：
        花括号不作替换或解析；仅检查非空、分隔符、绝对前缀、百分号、控制字符和点段。
    """
    if not isinstance(cast(object, value), str) or not value.strip():
        return "必须是非空相对逻辑路径"
    if "\\" in value:
        return "必须使用 / 分隔，不得包含反斜杠"
    if "%" in value:
        return "不得包含潜在 URL 转义百分号"
    if value.startswith("/") or _WINDOWS_DRIVE_PREFIX_PATTERN.match(value):
        return "必须是相对逻辑路径"
    if any(unicodedata.category(character).startswith("C") for character in value):
        return "不得包含 Unicode 控制类字符"
    if any(segment in {"", ".", ".."} for segment in value.split("/")):
        return "不得包含空段、. 或 .. 段"
    return None


def _conflict_issues(
    tasks: tuple[tuple[str, TaskConfig], ...],
    field_name: str,
    value_getter: Callable[[TaskConfig], str],
) -> list[ValidationIssue]:
    """收集启用任务字段在 casefold 后的全部重复组成员。

    参数：
        tasks: BuildConfig 中已稳定排序的任务元组。
        field_name: 当前为 ``output``，用于问题路径和消息。
        value_getter: 从 TaskConfig 取得对应逻辑路径的纯函数。

    返回：
        每个冲突启用任务各一个 issue；禁用任务不占用输出所有权。

    约束与副作用：
        不修改任务、不检查路径存在性；组内名称按 UTF-8 字节排序，消息确定性生成。
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    for task_name, task in tasks:
        if not task.enabled:
            continue
        value = value_getter(task)
        groups.setdefault(value.casefold(), []).append((task_name, value))

    issues: list[ValidationIssue] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        sorted_members = sorted(members, key=lambda item: item[0].encode("utf-8"))
        names = ", ".join(name for name, _ in sorted_members)
        for task_name, _ in sorted_members:
            issues.append(
                ValidationIssue(
                    f"tasks.{task_name}.{field_name}",
                    f"{field_name} 与任务组 [{names}] 存在大小写折叠冲突",
                )
            )
    return issues


def validate_build_config(config: BuildConfig) -> ValidationReport:
    """纯内存汇总完整构建配置的跨字段与路径安全问题。

    参数：
        config: 已通过模型局部构造约束的完整配置。

    返回：
        包含全部发现问题、按 path/message UTF-8 字节稳定排序的报告。

    异常：
        ``config`` 运行时不是 ``BuildConfig`` 时抛 ``ConfigurationError``；预期的
        配置问题不会抛异常，而是进入 ``ValidationReport``。

    约束与副作用：
        不访问文件系统，因此不检查 Unity 可执行文件或任何目录是否实际存在；
        timeout 正整数继续由 ``UnityToolConfig`` 模型负责。
    """
    if not isinstance(cast(object, config), BuildConfig):
        raise ConfigurationError("config 必须是 BuildConfig")

    issues: list[ValidationIssue] = []
    project_paths = (
        ("project.source_root", config.project.source_root),
        ("project.output_root", config.project.output_root),
        ("project.temp_root", config.project.temp_root),
    )
    for path_name, path_value in project_paths:
        message = _project_path_issue(path_value)
        if message is not None:
            issues.append(ValidationIssue(path_name, message))

    if not config.unity.executable.is_absolute():
        issues.append(
            ValidationIssue(
                "tools.unity.executable",
                "必须是绝对路径；工具存在性由外部集成检查",
            )
        )

    root_prefix_message = _safe_relative_template_path_issue(config.publish_layout.root_prefix)
    if root_prefix_message is not None:
        issues.append(
            ValidationIssue(
                "publish.layout.root_prefix",
                root_prefix_message,
            )
        )

    version_entry_message = _safe_relative_template_path_issue(
        config.publish_layout.version_entry_key
    )
    if version_entry_message is not None:
        issues.append(
            ValidationIssue(
                "publish.layout.version_entry_key",
                version_entry_message,
            )
        )

    profiles = dict(config.profiles)
    release_profile = profiles.get("release")
    if release_profile is None:
        issues.append(ValidationIssue("profile.release", "必须声明 release Profile"))
    elif release_profile.encrypt_lua and not release_profile.compile_lua:
        issues.append(
            ValidationIssue(
                "profile.release.encrypt_lua",
                "release 启用 encrypt_lua 时必须同时启用 compile_lua",
            )
        )

    issues.extend(
        _conflict_issues(
            config.tasks,
            "output",
            lambda task: task.output,
        )
    )
    return ValidationReport(tuple(issues))
