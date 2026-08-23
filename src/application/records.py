"""application 运行记录的确定性对象存储与 CAS 索引仓库。

运行记录内容对象按 SHA256 固定在 ``runs/<run>/records/``，当前记录索引单独通过
ObjectStore 的 compare-and-swap 更新。这样 CAS 冲突不会覆盖历史记录，上传成功但
索引冲突的不可变对象可以被恢复流程审计。记录序列化不包含时间和随机值，导入模块
不产生外部副作用。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from application.model import RunState, can_transition
from core.errors import BuildError
from ports.storage import CompareAndSwapRequest, ObjectStore, PutObjectRequest, StoredObject

RUN_RECORD_SCHEMA_VERSION = 1
_IDENTITY = re.compile(r"^[A-Za-z0-9._:-]+$")


class RecordError(BuildError):
    """表示运行记录不满足 schema、状态或对象存储边界。"""


class RecordConflictError(RecordError):
    """表示当前 run 索引 CAS 被其他执行者抢先更新。"""


@dataclass(frozen=True, slots=True)
class RunRecord:
    """不包含运行时间的可恢复运行记录 payload。

    参数：
        run_id: application 运行身份。
        state: 当前统一状态。
        request_digest: 请求配置的稳定摘要。
        artifact_ids: 目前已产生的身份元组。
        error: 脱敏错误摘要；无错误时为 ``None``。
        schema_version: 记录 schema，当前必须为 1。

    返回：
        无；``record_id`` 在构造时由规范 JSON 自动计算。

    异常：
        字段类型、schema 或身份文本非法时抛 ``RecordError``。

    约束与副作用：
        ``record_id`` 不进入自身摘要输入；对象不可变，不访问存储。
    """

    run_id: str
    state: RunState
    request_digest: str
    artifact_ids: tuple[str, ...]
    error: str | None
    schema_version: int = RUN_RECORD_SCHEMA_VERSION
    record_id: str = field(init=False)

    def __post_init__(self) -> None:
        """校验记录 payload 并绑定确定性 record ID。"""
        if self.schema_version != RUN_RECORD_SCHEMA_VERSION:
            raise RecordError(f"不支持的 RunRecord schema_version: {self.schema_version}")
        for name, value in (("run_id", self.run_id), ("request_digest", self.request_digest)):
            if not isinstance(value, str) or not value or _IDENTITY.fullmatch(value) is None:
                raise RecordError(f"{name} 必须是无控制字符身份文本")
        if not isinstance(self.state, RunState):
            raise RecordError("state 必须是 RunState")
        if not isinstance(self.artifact_ids, tuple) or any(
            not isinstance(item, str) or not item for item in self.artifact_ids
        ):
            raise RecordError("artifact_ids 必须是 tuple[str, ...]")
        if self.error is not None and (not isinstance(self.error, str) or not self.error):
            raise RecordError("error 必须是非空 str 或 None")
        object.__setattr__(
            self, "record_id", hashlib.sha256(canonical_record_bytes(self)).hexdigest()
        )


@dataclass(frozen=True, slots=True)
class RecordReceipt:
    """一次记录对象和索引 CAS 的不可变回执。"""

    record_id: str
    locator: str
    generation: int
    idempotent: bool


def _record_payload(record: RunRecord) -> dict[str, object]:
    """构建不含 record_id 的规范 JSON mapping。"""
    return {
        "artifact_ids": list(record.artifact_ids),
        "error": record.error,
        "request_digest": record.request_digest,
        "run_id": record.run_id,
        "schema_version": record.schema_version,
        "state": record.state.value,
    }


def canonical_record_bytes(record: RunRecord) -> bytes:
    """返回运行记录的 UTF-8、排序键、无空白规范 JSON 字节。"""
    if not isinstance(record, RunRecord):
        raise RecordError("record 必须是 RunRecord")
    return json.dumps(
        _record_payload(record),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class RunRecordRepository:
    """把运行记录对象和当前索引写入现有 ObjectStore 端口。"""

    def __init__(self, object_store: ObjectStore) -> None:
        """绑定对象存储并创建本进程幂等回执缓存。"""
        if not isinstance(object_store, ObjectStore):
            raise TypeError("object_store 必须是 ObjectStore")
        self._object_store = object_store
        self._receipts: dict[str, RecordReceipt] = {}

    def write(
        self,
        record: RunRecord,
        *,
        expected_generation: int,
        previous_state: RunState | None = None,
    ) -> RecordReceipt:
        """写入不可变记录并 CAS 更新当前索引。

        参数：
            record: 待追加的运行记录。
            expected_generation: 调用方读取当前索引时的代际。
            previous_state: 可选前一状态；提供时必须存在合法状态机边。

        返回：
            记录对象 locator、record ID 和新的 CAS generation。

        异常：
            状态倒退、重复身份不一致、对象回执错误或 CAS 冲突时抛
            ``RecordError`` / ``RecordConflictError``。

        约束与副作用：
            先写不可变内容对象，再 CAS 当前索引；CAS 冲突不删除已写对象、不重试。
        """
        if not isinstance(record, RunRecord):
            raise RecordError("record 必须是 RunRecord")
        if (
            not isinstance(expected_generation, int)
            or isinstance(expected_generation, bool)
            or expected_generation < 0
        ):
            raise RecordError("expected_generation 必须是非负整数")
        if previous_state is not None:
            if not isinstance(previous_state, RunState):
                raise RecordError("previous_state 必须是 RunState")
            if not can_transition(previous_state, record.state):
                raise RecordConflictError(
                    f"不允许状态倒退: {previous_state.value} -> {record.state.value}"
                )
        existing = self._receipts.get(record.record_id)
        if existing is not None:
            return RecordReceipt(
                existing.record_id,
                existing.locator,
                existing.generation,
                True,
            )
        content = canonical_record_bytes(record)
        digest = hashlib.sha256(content).hexdigest()
        locator = f"runs/{record.run_id}/records/{record.record_id}.json"
        stored = self._object_store.put(PutObjectRequest(locator, content, digest))
        if stored != StoredObject(locator, digest, len(content)):
            raise RecordError(f"运行记录对象回执不一致: {locator}")
        index_key = f"runs/{record.run_id}/current"
        cas = self._object_store.compare_and_swap(
            CompareAndSwapRequest(index_key, expected_generation, locator.encode("utf-8"))
        )
        if not cas.applied:
            raise RecordConflictError(
                f"运行记录索引 CAS 冲突: expected={expected_generation}, actual={cas.generation}"
            )
        receipt = RecordReceipt(record.record_id, locator, cas.generation, False)
        self._receipts[record.record_id] = receipt
        return receipt

    def read_current(self, run_id: str) -> tuple[RunRecord, int]:
        """读取持久化 current 索引指向的最新运行记录。

        参数：
            run_id: 要读取的运行身份。

        返回：
            ``(RunRecord, generation)``，generation 可直接作为下一次 CAS 的期望代际。

        异常：
            对象缺失、索引 locator 越界、JSON/schema/状态非法或存储不支持读取时抛出
            ``RecordError``。

        约束与副作用：
            只读取对象存储，不改变 current 索引；索引 locator 必须指向同一 run 的
            ``runs/<run>/records/<record>.json``，防止跨运行路径注入。
        """
        _validate_identity(run_id, "run_id")
        index_key = f"runs/{run_id}/current"
        try:
            locator = self._object_store.read(index_key).decode("ascii")
            locator_parts = locator.split("/")
            if (
                len(locator_parts) != 4
                or locator_parts[0] != "runs"
                or locator_parts[1] != run_id
                or locator_parts[2] != "records"
                or not locator_parts[3].endswith(".json")
                or _IDENTITY.fullmatch(locator_parts[3][:-5]) is None
            ):
                raise RecordError("运行记录 current locator 非法")
            document = json.loads(self._object_store.read(locator).decode("utf-8"))
            record = _record_from_document(document)
            if record.run_id != run_id:
                raise RecordError("运行记录 run_id 与 current 索引不一致")
            generation = self._object_store.current_generation(index_key)
        except RecordError:
            raise
        except (
            LookupError,
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise RecordError(f"运行记录读取失败: {run_id}") from exc
        return record, generation


def _validate_identity(value: object, field_name: str) -> str:
    """校验运行记录身份字段。"""
    if not isinstance(value, str) or not value or _IDENTITY.fullmatch(value) is None:
        raise RecordError(f"{field_name} 必须是无控制字符身份文本")
    return value


def _record_from_document(value: object) -> RunRecord:
    """从不可信 JSON 文档严格重建 RunRecord。"""
    if not isinstance(value, Mapping):
        raise RecordError("运行记录根对象必须是 object")
    document = cast(Mapping[str, object], value)
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise RecordError("运行记录 schema_version 必须是 int")
    try:
        state = RunState(document.get("state"))
    except ValueError as exc:
        raise RecordError("运行记录 state 非法") from exc
    artifact_ids = document.get("artifact_ids")
    if not isinstance(artifact_ids, list):
        raise RecordError("运行记录 artifact_ids 必须是 list")
    raw_artifact_ids = cast(list[object], artifact_ids)
    if any(not isinstance(item, str) for item in raw_artifact_ids):
        raise RecordError("运行记录 artifact_ids 必须是字符串列表")
    error = document.get("error")
    if error is not None and not isinstance(error, str):
        raise RecordError("运行记录 error 必须是字符串或 None")
    return RunRecord(
        _validate_identity(document.get("run_id"), "run_id"),
        state,
        _validate_identity(document.get("request_digest"), "request_digest"),
        tuple(cast(list[str], raw_artifact_ids)),
        error,
        schema_version,
    )


__all__ = [
    "RUN_RECORD_SCHEMA_VERSION",
    "RecordConflictError",
    "RecordError",
    "RecordReceipt",
    "RunRecord",
    "RunRecordRepository",
    "canonical_record_bytes",
]
