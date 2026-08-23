"""验证日志保留清理的锁、保护条件和精确删除边界。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from observability.retention import LogRetentionCleaner, LogRunMetadata


def test_retention_cleaner_deletes_only_expired_archived_runs(tmp_path: Path) -> None:
    """Given 多个历史运行，When 清理，Then 只删除满足全部门禁的精确目录。"""
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    root = tmp_path / "logs"
    eligible = root / "build-1" / "map" / "run-old"
    active = root / "build-1" / "map" / "run-active"
    unarchived = root / "build-1" / "map" / "run-unarchived"
    for directory in (eligible, active, unarchived):
        directory.mkdir(parents=True)
        (directory / "main.log").write_text("log", encoding="utf-8")

    report = LogRetentionCleaner.clean(
        root,
        now=now,
        retain_days=30,
        runs=(
            LogRunMetadata(eligible, now - timedelta(days=31), archived=True),
            LogRunMetadata(active, now - timedelta(days=31), archived=True, active_lease=True),
            LogRunMetadata(unarchived, now - timedelta(days=31), archived=False),
        ),
    )

    assert report.deleted == (eligible,)
    assert not eligible.exists()
    assert active.exists()
    assert unarchived.exists()
    assert {item.reason for item in report.skipped} == {"active_lease", "not_archived"}
    assert not (root / ".maintenance.lock").exists()


def test_retention_cleaner_rejects_busy_lock_and_protected_directory(tmp_path: Path) -> None:
    """Given 维护锁或目录越界，When 清理，Then 不执行删除。"""
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    root = tmp_path / "logs"
    root.mkdir()
    lock = root / ".maintenance.lock"
    lock.write_text("busy", encoding="utf-8")
    with pytest.raises(RuntimeError, match="维护锁"):
        LogRetentionCleaner.clean(root, now=now, retain_days=0, runs=())
    lock.unlink()

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="日志根目录"):
        LogRetentionCleaner.clean(
            root,
            now=now,
            retain_days=0,
            runs=(LogRunMetadata(outside, now - timedelta(days=1), archived=True),),
        )
