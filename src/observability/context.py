"""不可变日志上下文与确定性日志路径定位。

本模块定义构建、任务、运行和步骤的安全日志上下文，并根据显式注入的开始时间
生成彼此隔离的日志路径。所有校验只处理内存中的字符串和路径表示，不访问文件
系统、不解析符号链接或 reparse point，也不创建目录和文件。
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

_SAFE_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _validate_safe_segment(value: str, *, field_name: str, allow_empty: bool = False) -> None:
    """校验字符串是不会改变目录层级的单个安全路径段。

    参数：
        value: 待校验的路径段文本。
        field_name: 用于异常消息的字段名。
        allow_empty: 是否允许用空字符串表示字段不适用。

    返回：
        校验成功时返回 ``None``。

    异常、约束与副作用：
        非字符串、非法空值、``.``、``..``、控制/格式字符或不符合 ASCII 安全
        字符集的值抛出 ``ValueError``。函数不访问文件系统且无外部副作用。
    """
    value_object = cast(object, value)
    if not isinstance(value_object, str):
        raise ValueError(f"{field_name} 必须是 str")
    if value_object == "":
        if allow_empty:
            return
        raise ValueError(f"{field_name} 不得为空")
    if value_object in {".", ".."}:
        raise ValueError(f"{field_name} 不得为 '.' 或 '..'")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value_object):
        raise ValueError(f"{field_name} 不得包含控制或格式字符")
    if _SAFE_SEGMENT_PATTERN.fullmatch(value_object) is None:
        raise ValueError(
            f"{field_name} 必须匹配 [A-Za-z0-9][A-Za-z0-9._-]*",
        )


def _require_path(value: Path, *, field_name: str) -> Path:
    """校验公开路径参数在运行时确实是 ``Path``。

    参数：
        value: 待校验的路径对象。
        field_name: 用于异常消息的字段名。

    返回：
        原始 ``Path`` 对象，不执行字符串或其他 PathLike 隐式转换。

    异常、约束与副作用：
        类型不是 ``Path`` 时抛出 ``ValueError``。函数不读取路径对应的实体，也不
        解析符号链接或 reparse point。
    """
    value_object = cast(object, value)
    if not isinstance(value_object, Path):
        raise ValueError(f"{field_name} 必须是 Path")
    return value_object


@dataclass(frozen=True, slots=True)
class LogContext:
    """一次任务运行的不可变日志定位上下文。

    职责：
        保存构建、任务、运行和可选步骤标识，并在构造时阻断路径分隔符、路径跳转
        以及不可见控制字符进入日志目录或日志字段。

    参数：
        build_id: 非空构建标识，必须是安全单路径段。
        task_name: 非空任务名，必须是安全单路径段。
        run_id: 非空运行标识，必须是安全单路径段。
        step_name: 可选步骤名；空字符串表示不适用，否则必须是安全单路径段。

    返回：
        无；构造成功后得到冻结且使用 slots 的纯值对象。

    异常、约束与副作用：
        字段类型或格式非法时抛出 ``ValueError``。本对象不依赖配置异常体系，不
        执行 I/O、日志记录或全局状态修改。
    """

    build_id: str
    task_name: str
    run_id: str
    step_name: str = ""

    def __post_init__(self) -> None:
        """校验全部日志上下文字段。

        本方法没有参数和返回值；读取实例字段并调用统一路径段校验。任何非法值
        抛出 ``ValueError``，冻结对象不会被修改，方法也不产生外部副作用。
        """
        _validate_safe_segment(self.build_id, field_name="build_id")
        _validate_safe_segment(self.task_name, field_name="task_name")
        _validate_safe_segment(self.run_id, field_name="run_id")
        _validate_safe_segment(self.step_name, field_name="step_name", allow_empty=True)


@dataclass(frozen=True, slots=True)
class LogPaths:
    """一次任务运行对应的不可变日志路径集合。

    职责：
        保存日志目录、主日志、标准输出、标准错误和 Unity 原始日志路径，并保证四
        个文件都是目录的直属子项且共享同一安全基础名。

    参数：
        directory: 当前运行独占的日志目录。
        main: 名称为 ``{base}.log`` 的主日志路径。
        stdout: 名称为 ``{base}_stdout.log`` 的标准输出日志路径。
        stderr: 名称为 ``{base}_stderr.log`` 的标准错误日志路径。
        unity: 名称为 ``{base}_unity.log`` 的 Unity 原始日志路径。

    返回：
        无；构造成功后得到冻结且使用 slots 的纯值对象。

    异常、约束与副作用：
        任一字段不是 ``Path``、文件不直属 ``directory``、名称不安全或四类名称
        关系不一致时抛出 ``ValueError``。校验不查询路径是否存在且无 I/O。
    """

    directory: Path
    main: Path
    stdout: Path
    stderr: Path
    unity: Path

    def __post_init__(self) -> None:
        """校验四个日志文件的父目录和确定性名称关系。

        本方法没有参数和返回值。非法路径类型、父目录或名称抛出 ``ValueError``；
        不创建目录或文件，也不解析文件系统链接。
        """
        directory = _require_path(self.directory, field_name="directory")
        main = _require_path(self.main, field_name="main")
        stdout = _require_path(self.stdout, field_name="stdout")
        stderr = _require_path(self.stderr, field_name="stderr")
        unity = _require_path(self.unity, field_name="unity")

        paths = (
            ("main", main),
            ("stdout", stdout),
            ("stderr", stderr),
            ("unity", unity),
        )
        for field_name, path in paths:
            if path.parent != directory:
                raise ValueError(f"{field_name} 必须是 directory 的直属文件")
            _validate_safe_segment(path.name, field_name=f"{field_name}.name")

        if not main.name.endswith(".log"):
            raise ValueError("main 名称必须以 .log 结尾")
        base_name = main.name.removesuffix(".log")
        _validate_safe_segment(base_name, field_name="main 基础名")
        expected_names = {
            "stdout": f"{base_name}_stdout.log",
            "stderr": f"{base_name}_stderr.log",
            "unity": f"{base_name}_unity.log",
        }
        for field_name, path in paths[1:]:
            if path.name != expected_names[field_name]:
                raise ValueError(f"{field_name} 名称必须为 {expected_names[field_name]!r}")


def build_log_paths(root: Path, context: LogContext, started_at: datetime) -> LogPaths:
    """根据显式上下文和开始时间确定一组日志路径。

    参数：
        root: 日志根目录，运行时必须是 ``Path``；可以是相对或绝对路径。
        context: 已校验的不可变日志上下文。
        started_at: 含时区的任务开始时间；文件名直接使用该对象自身时区的墙钟值。

    返回：
        ``directory`` 为 ``root/build_id/task_name/run_id`` 的 ``LogPaths``。基础名为
        ``{task_name}_{YYYYMMDD_HHMMSS}_{run_id}``，并生成主日志、stdout、stderr
        和 Unity 四个固定后缀。

    异常、约束与副作用：
        参数类型非法、时间无时区或词法 containment 校验失败时抛出 ``ValueError``。
        函数不读取当前时间，不创建路径，不调用 ``resolve``，也不检查符号链接或
        reparse point；这些 I/O 安全检查由后续 handler 负责。
    """
    root_path = _require_path(root, field_name="root")
    context_object = cast(object, context)
    if not isinstance(context_object, LogContext):
        raise ValueError("context 必须是 LogContext")
    started_at_object = cast(object, started_at)
    if not isinstance(started_at_object, datetime):
        raise ValueError("started_at 必须是 datetime")
    try:
        utc_offset = started_at_object.utcoffset()
    except (TypeError, ValueError) as exc:
        raise ValueError("started_at 必须包含有效时区") from exc
    if started_at_object.tzinfo is None or utc_offset is None:
        raise ValueError("started_at 必须是 timezone-aware datetime")

    # normpath 只折叠路径文本中的冗余分隔符和点段，不触碰文件系统实体。
    normalized_root = Path(os.path.normpath(root_path))
    directory = (
        normalized_root / context_object.build_id / context_object.task_name / context_object.run_id
    )
    try:
        relative_directory = directory.relative_to(normalized_root)
    except ValueError as exc:
        raise ValueError("日志目录必须位于 root 内") from exc
    expected_parts = (
        context_object.build_id,
        context_object.task_name,
        context_object.run_id,
    )
    if relative_directory.parts != expected_parts:
        raise ValueError("日志目录层级与上下文不一致")

    timestamp = started_at_object.strftime("%Y%m%d_%H%M%S")
    base_name = f"{context_object.task_name}_{timestamp}_{context_object.run_id}"
    return LogPaths(
        directory=directory,
        main=directory / f"{base_name}.log",
        stdout=directory / f"{base_name}_stdout.log",
        stderr=directory / f"{base_name}_stderr.log",
        unity=directory / f"{base_name}_unity.log",
    )
