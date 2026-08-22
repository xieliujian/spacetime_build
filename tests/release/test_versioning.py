"""正式版本流分配、幂等、状态迁移和持久化测试。"""

import hashlib
from pathlib import Path

import pytest

from release.versioning import (
    FileVersionStateStore,
    INT32_MAX,
    MemoryVersionStateStore,
    VersionAllocator,
    VersionError,
    VersionStatus,
    version_stream,
)


def _allocator() -> tuple[VersionAllocator, object]:
    """构造内存版本分配器和固定入口流。"""
    stream = version_stream("prod", "local-cdn", "windows/main-entry.json")
    return VersionAllocator(MemoryVersionStateStore()), stream


def test_version_stream_is_stable_and_allocate_is_build_idempotent() -> None:
    """验证入口决定流身份，同一 build 重试不消耗新号码。"""
    allocator, stream = _allocator()
    first = allocator.allocate(stream, build_id="jenkins-7", request_id="req-1", major=1, minor=0)
    retry = allocator.allocate(stream, build_id="jenkins-7", request_id="req-2", major=9, minor=9)
    second = allocator.allocate(stream, build_id="jenkins-8", request_id="req-3", major=1, minor=0)

    assert first == retry
    assert first.file_list_no == 1
    assert second.file_list_no == 2
    assert first.display_version == "1.0.1"


def test_version_lifecycle_is_monotonic_and_confirm_is_idempotent() -> None:
    """验证 RESERVED→READY→ACTIVATING→PUBLISHED 和重复确认。"""
    allocator, stream = _allocator()
    reserved = allocator.allocate(stream, build_id="b1", request_id="r1", major=1, minor=2)
    ready = allocator.mark_ready(
        reserved.reservation_id,
        bundle_id="bundle-1",
        upload_plan_id="plan-1",
        verification_digest="v" * 64,
    )
    replacement = b'{"FileListNo":1}'
    activating = allocator.prepare_activation(
        ready.reservation_id,
        expected_generation=0,
        expected_entry_digest="e" * 64,
        replacement_entry=replacement,
    )
    published = allocator.confirm(
        activating.reservation_id,
        observed_generation=1,
        observed_entry_digest=hashlib.sha256(replacement).hexdigest(),
    )
    assert ready.status is VersionStatus.READY
    assert published.status is VersionStatus.PUBLISHED
    assert (
        allocator.confirm(
            published.reservation_id,
            observed_generation=1,
            observed_entry_digest=hashlib.sha256(replacement).hexdigest(),
        )
        == published
    )


def test_reconcile_marks_changed_entry_as_conflicted() -> None:
    """验证入口被其他发布改变时恢复只能进入 CONFLICTED。"""
    allocator, stream = _allocator()
    reservation = allocator.allocate(stream, build_id="b1", request_id="r1", major=1, minor=0)
    allocator.mark_ready(
        reservation.reservation_id,
        bundle_id="bundle-1",
        upload_plan_id="plan-1",
        verification_digest="v" * 64,
    )
    allocator.prepare_activation(
        reservation.reservation_id,
        expected_generation=0,
        expected_entry_digest="e" * 64,
        replacement_entry=b"replacement",
    )

    result = allocator.reconcile(
        reservation.reservation_id,
        observed_generation=4,
        observed_entry_digest="0" * 64,
    )

    assert result.status is VersionStatus.CONFLICTED


def test_abandon_does_not_reuse_number_and_file_store_round_trips(tmp_path: Path) -> None:
    """验证废弃号码保留高水位，文件存储重载后仍可继续分配。"""
    stream = version_stream("prod", "local-cdn", "entry.json")
    allocator = VersionAllocator(FileVersionStateStore(tmp_path / "versions"))
    first = allocator.allocate(stream, build_id="b1", request_id="r1", major=1, minor=0)
    allocator.abandon(first.reservation_id)
    second = allocator.allocate(stream, build_id="b2", request_id="r2", major=1, minor=0)

    assert second.file_list_no == 2
    assert (
        VersionAllocator(FileVersionStateStore(tmp_path / "versions"))
        .preview(stream, major=1, minor=0)
        .file_list_no
        == 3
    )


def test_file_store_round_trips_activation_replacement_blob(tmp_path: Path) -> None:
    """验证持久 JSON 重载后仍可完成 ACTIVATING→PUBLISHED。"""
    stream = version_stream("prod", "local-cdn", "entry.json")
    root = tmp_path / "versions"
    allocator = VersionAllocator(FileVersionStateStore(root))
    reservation = allocator.allocate(stream, build_id="b1", request_id="r1", major=1, minor=0)
    allocator.mark_ready(
        reservation.reservation_id,
        bundle_id="bundle-1",
        upload_plan_id="plan-1",
        verification_digest="v" * 64,
    )
    replacement = b"replacement-entry"
    allocator.prepare_activation(
        reservation.reservation_id,
        expected_generation=0,
        expected_entry_digest="e" * 64,
        replacement_entry=replacement,
    )

    reloaded = VersionAllocator(FileVersionStateStore(root))
    published = reloaded.confirm(
        reservation.reservation_id,
        observed_generation=1,
        observed_entry_digest=hashlib.sha256(replacement).hexdigest(),
    )

    assert published.status is VersionStatus.PUBLISHED


def test_preview_rejects_int32_exhaustion() -> None:
    """验证到达 Int32 上限后不会回绕或复用号码。"""
    store = MemoryVersionStateStore()
    stream = version_stream("prod", "local", "entry")
    store._states[stream.stream_id] = store.read(stream.stream_id).__class__(
        stream.stream_id, INT32_MAX, INT32_MAX
    )

    with pytest.raises(VersionError, match="Int32"):
        VersionAllocator(store).preview(stream, major=1, minor=0)
