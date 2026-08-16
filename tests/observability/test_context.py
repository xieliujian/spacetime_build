"""验证日志上下文、路径值对象与确定性日志命名。

本模块覆盖路径段安全、冻结数据、含时区时间命名、词法 containment 和无 I/O
定位契约。测试只使用内存值或 pytest 临时路径，不访问外部系统。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

from observability import LogContext, LogPaths, build_log_paths


class _InvalidTimezone(tzinfo):
    """测试用时区，其 UTC 偏移计算始终报告无效配置。"""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        """模拟时区实现计算偏移时抛出 ``ValueError``。

        ``dt`` 是标准库传入时间且不会被读取；本方法无返回值并始终抛出异常。
        """
        raise ValueError("invalid offset")


def test_log_context_is_frozen_slotted_and_allows_optional_empty_step() -> None:
    """验证日志上下文冻结、无字典且步骤可用空字符串表示不适用。

    无参数和返回值；字段修改应抛出冻结异常，对象不产生 I/O。
    """
    context = LogContext(build_id="build-1", task_name="scene.main", run_id="run_2")

    assert context.step_name == ""
    assert not hasattr(context, "__dict__")
    with pytest.raises(FrozenInstanceError):
        context.run_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("build_id", ""),
        ("task_name", "."),
        ("run_id", ".."),
        ("build_id", "../escape"),
        ("task_name", "scene/subtask"),
        ("run_id", "run\\child"),
        ("step_name", "hidden\u200dstep"),
        ("step_name", "line\nfeed"),
        ("task_name", "中文任务"),
        ("run_id", "-leading-hyphen"),
    ],
)
def test_log_context_rejects_unsafe_single_path_segments(field_name: str, value: str) -> None:
    """验证必填标识和非空步骤严格拒绝路径跳转及非安全字符。

    ``field_name`` 指定替换字段，``value`` 是非法样例。构造必须抛出
    ``ValueError``；测试无返回值和外部副作用。
    """
    arguments = {
        "build_id": "build-1",
        "task_name": "scene",
        "run_id": "run-1",
        "step_name": "collect",
    }
    arguments[field_name] = value

    with pytest.raises(ValueError, match=field_name):
        LogContext(**arguments)


def test_log_paths_requires_direct_children_with_related_safe_names(tmp_path: Path) -> None:
    """验证四类日志必须直属同一目录并共享确定性基础名。

    ``tmp_path`` 仅提供路径值；测试不创建文件。错误父目录或错误后缀应抛出
    ``ValueError``，合法对象保持冻结。
    """
    directory = tmp_path / "run"
    paths = LogPaths(
        directory=directory,
        main=directory / "scene_20260102_030405_run-1.log",
        stdout=directory / "scene_20260102_030405_run-1_stdout.log",
        stderr=directory / "scene_20260102_030405_run-1_stderr.log",
        unity=directory / "scene_20260102_030405_run-1_unity.log",
    )
    assert not hasattr(paths, "__dict__")
    with pytest.raises(FrozenInstanceError):
        paths.main = directory / "other.log"  # type: ignore[misc]

    with pytest.raises(ValueError, match="stdout"):
        LogPaths(
            directory=directory,
            main=paths.main,
            stdout=directory / "child" / paths.stdout.name,
            stderr=paths.stderr,
            unity=paths.unity,
        )
    with pytest.raises(ValueError, match="unity"):
        LogPaths(
            directory=directory,
            main=paths.main,
            stdout=paths.stdout,
            stderr=paths.stderr,
            unity=directory / "unrelated.log",
        )


def test_build_log_paths_uses_injected_local_wall_time_and_fixed_suffixes(tmp_path: Path) -> None:
    """验证日志路径使用显式时间自身时区的墙钟值和四个固定后缀。

    ``tmp_path`` 是未创建的日志根路径来源。函数返回路径值但不得创建任何目录；
    时间不转换到 UTC，也不读取当前时间。
    """
    root = tmp_path / "missing-logs"
    context = LogContext("build-42", "scene", "run-7", "collect")
    started_at = datetime(2026, 1, 2, 3, 4, 5, 987654, tzinfo=timezone(timedelta(hours=8)))

    paths = build_log_paths(root, context, started_at)

    expected_directory = root / "build-42" / "scene" / "run-7"
    expected_base = "scene_20260102_030405_run-7"
    assert paths == LogPaths(
        directory=expected_directory,
        main=expected_directory / f"{expected_base}.log",
        stdout=expected_directory / f"{expected_base}_stdout.log",
        stderr=expected_directory / f"{expected_base}_stderr.log",
        unity=expected_directory / f"{expected_base}_unity.log",
    )
    assert not root.exists()


def test_build_log_paths_lexically_normalizes_root_without_resolving_entities(
    tmp_path: Path,
) -> None:
    """验证含点段根路径只做词法规范化且不要求文件系统实体存在。

    ``tmp_path`` 提供未创建路径。返回目录应位于规范根下；函数不创建输入或结果
    路径，不解析符号链接和 reparse point。
    """
    root = tmp_path / "parent" / ".." / "logs"
    context = LogContext("build", "task", "run")

    paths = build_log_paths(root, context, datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert paths.directory == tmp_path / "logs" / "build" / "task" / "run"
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize(
    "started_at",
    [datetime(2026, 1, 1), "2026-01-01"],
)
def test_build_log_paths_rejects_naive_or_non_datetime_values(started_at: object) -> None:
    """验证开始时间必须是含有效时区的 datetime。

    ``started_at`` 是无时区或错误类型样例。调用应抛出 ``ValueError``；测试不
    读取时间或文件系统。
    """
    with pytest.raises(ValueError, match="started_at"):
        build_log_paths(
            Path("logs"),
            LogContext("build", "task", "run"),
            started_at,  # type: ignore[arg-type]
        )


def test_build_log_paths_requires_runtime_path_and_context_types() -> None:
    """验证根目录和上下文不接受隐式字符串或结构相似对象。

    无参数和返回值；非法类型均应抛出 ``ValueError``，测试不执行 I/O。
    """
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = LogContext("build", "task", "run")

    with pytest.raises(ValueError, match="root"):
        build_log_paths("logs", context, timestamp)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="context"):
        build_log_paths(Path("logs"), object(), timestamp)  # type: ignore[arg-type]


def test_context_public_values_reject_wrong_types_and_invalid_main_extension(
    tmp_path: Path,
) -> None:
    """验证上下文字段运行时类型和主日志扩展名不可由类型标注绕过。

    ``tmp_path`` 只提供路径值。错误类型或非 ``.log`` 主文件应抛出 ``ValueError``；
    测试不创建目录和文件。
    """
    with pytest.raises(ValueError, match="build_id"):
        LogContext(object(), "task", "run")  # type: ignore[arg-type]

    directory = tmp_path / "run"
    with pytest.raises(ValueError, match=r"main.*\.log"):
        LogPaths(
            directory=directory,
            main=directory / "task.txt",
            stdout=directory / "task_stdout.log",
            stderr=directory / "task_stderr.log",
            unity=directory / "task_unity.log",
        )


def test_build_log_paths_wraps_timezone_offset_failures_as_value_error() -> None:
    """验证异常时区实现不会把底层偏移错误泄漏为未分类异常。

    无参数和返回值；显式无效时区在路径定位边界转换为含 ``started_at`` 的
    ``ValueError``，且不访问文件系统。
    """
    started_at = datetime(2026, 1, 1, tzinfo=_InvalidTimezone())

    with pytest.raises(ValueError, match="started_at.*有效时区"):
        build_log_paths(Path("logs"), LogContext("build", "task", "run"), started_at)
