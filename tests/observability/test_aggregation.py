"""验证正式构建汇总日志的确定性、脱敏和原子替换。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from observability.aggregation import BuildLogAssembler, BuildLogEntry
from observability.runtime import RunOutcome


def test_build_log_assembler_writes_sorted_summary_and_replaces_atomically(
    tmp_path: Path,
) -> None:
    """Given 无序任务引用，When 汇总，Then build.log 稳定排序且不泄漏秘密。"""
    started_at = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)
    finished_at = datetime(2026, 8, 22, 10, 0, 3, tzinfo=timezone.utc)
    entries = (
        BuildLogEntry(
            timestamp=datetime(2026, 8, 22, 10, 0, 2, tzinfo=timezone.utc),
            task_name="texture",
            run_id="run-texture",
            outcome=RunOutcome.SUCCEEDED,
            duration_ms=200,
            log_locator="build-1/texture/run-texture/main.log",
            message="纹理完成 token=secret-value",
        ),
        BuildLogEntry(
            timestamp=datetime(2026, 8, 22, 10, 0, 1, tzinfo=timezone.utc),
            task_name="map",
            run_id="run-map",
            outcome=RunOutcome.FAILED,
            duration_ms=100,
            log_locator="build-1/map/run-map/main.log",
            diagnostic_locators=("build-1/map/run-map/unity.log",),
            message="Unity 失败 password=secret-value",
        ),
    )

    output = BuildLogAssembler.write(
        tmp_path / "logs",
        "build-1",
        started_at=started_at,
        finished_at=finished_at,
        outcome=RunOutcome.FAILED,
        fields=(("profile", "release"), ("source_revision", "r123")),
        entries=entries,
    )
    first = output.read_text(encoding="utf-8")

    entries_reversed = tuple(reversed(entries))
    output = BuildLogAssembler.write(
        tmp_path / "logs",
        "build-1",
        started_at=started_at,
        finished_at=finished_at,
        outcome=RunOutcome.FAILED,
        fields=(("profile", "release"), ("source_revision", "r123")),
        entries=entries_reversed,
    )
    second = output.read_text(encoding="utf-8")

    assert first == second
    assert output == tmp_path / "logs" / "build-1" / "build.log"
    assert first.index('task="map"') < first.index('task="texture"')
    assert "secret-value" not in first
    assert ".build-log-" not in first


def test_build_log_assembler_rejects_duplicate_run_references(tmp_path: Path) -> None:
    """Given 同一任务 run 被重复引用，When 汇总，Then 在写盘前拒绝歧义。"""
    timestamp = datetime(2026, 8, 22, tzinfo=timezone.utc)
    entry = BuildLogEntry(
        timestamp=timestamp,
        task_name="map",
        run_id="run-1",
        outcome=RunOutcome.SUCCEEDED,
        duration_ms=1,
        log_locator="map.log",
    )

    try:
        BuildLogAssembler.write(
            tmp_path / "logs",
            "build-1",
            started_at=timestamp,
            finished_at=timestamp,
            outcome=RunOutcome.SUCCEEDED,
            entries=(entry, entry),
        )
    except ValueError as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("重复日志引用应在写盘前失败")
