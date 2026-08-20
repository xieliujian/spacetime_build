"""验证 application 运行记录的确定性对象和 CAS 索引。"""

import hashlib

import pytest

from application.model import RunState
from application.records import RecordConflictError, RunRecord, RunRecordRepository
from ports.storage import (
    CompareAndSwapRequest,
    CompareAndSwapResult,
    ObjectStore,
    ObjectVerification,
    PutObjectRequest,
    StoredObject,
)


class _Store(ObjectStore):
    """支持对象写入和 CAS 的内存替身。"""

    def __init__(self, *, conflict: bool = False) -> None:
        """创建可控制 CAS 冲突的对象存储替身。"""
        self.objects: dict[str, bytes] = {}
        self.generation = 0
        self.conflict = conflict
        self.put_calls = 0
        self.cas_calls = 0

    def put(self, request: PutObjectRequest) -> StoredObject:
        """保存对象并返回结构化回执。"""
        self.put_calls += 1
        self.objects[request.key] = request.content
        return StoredObject(request.key, request.sha256, len(request.content))

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """按引用读取内存对象。"""
        content = self.objects.get(reference.key)
        return ObjectVerification(
            reference,
            content is not None,
            hashlib.sha256(content).hexdigest() if content is not None else None,
            len(content) if content is not None else None,
        )

    def compare_and_swap(self, request: CompareAndSwapRequest) -> CompareAndSwapResult:
        """按期望代际执行内存 CAS。"""
        self.cas_calls += 1
        if self.conflict or request.expected_generation != self.generation:
            return CompareAndSwapResult(False, self.generation, None)
        self.objects[request.key] = request.content
        self.generation += 1
        return CompareAndSwapResult(
            True,
            self.generation,
            hashlib.sha256(request.content).hexdigest(),
        )


def _record(state: RunState) -> RunRecord:
    """创建测试运行记录。"""
    return RunRecord("run-1", state, "request-digest", ("manifest-1",), None)


def test_record_bytes_are_deterministic_and_repeated_write_is_idempotent() -> None:
    """Given 同一记录，When 重复写入，Then 内容 ID 相同且不重复 CAS。"""
    store = _Store()
    repository = RunRecordRepository(store)
    first = repository.write(_record(RunState.CREATED), expected_generation=0)
    second = repository.write(_record(RunState.CREATED), expected_generation=0)

    assert first.record_id == second.record_id
    assert first.idempotent is False
    assert second.idempotent is True
    assert store.cas_calls == 1
    assert len(store.objects) == 2


def test_record_repository_rejects_state_regression_and_cas_conflict() -> None:
    """Given 终态或竞争更新，When 写记录，Then 不覆盖历史索引。"""
    store = _Store()
    repository = RunRecordRepository(store)
    repository.write(_record(RunState.CREATED), expected_generation=0)
    with pytest.raises(RecordConflictError):
        repository.write(
            _record(RunState.FAILED), expected_generation=1, previous_state=RunState.SUCCEEDED
        )

    conflict_repository = RunRecordRepository(_Store(conflict=True))
    with pytest.raises(RecordConflictError):
        conflict_repository.write(_record(RunState.CREATED), expected_generation=0)
