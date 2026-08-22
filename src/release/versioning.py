"""正式资源版本号分配、激活状态和本地持久化。

版本分配以最终版本入口为流身份，FileListNo 只允许在 ``1..Int32.MAX_VALUE`` 内单调
增长。``VersionAllocator`` 对同一 build_id 幂等，对不同构建保留已分配号码；发布状态
通过明确白名单推进，入口 CAS 的观测结果由 ``prepare_activation``/``confirm`` 固化。
本模块不上传对象、不读取 CDN 内容，也不把显示版本写入旧客户端六字段协议。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Protocol, TypeVar, cast

from core.manifest_codec import canonical_json_bytes
from core.artifacts import BlobRef

INT32_MAX = 2**31 - 1
_STATE_SCHEMA_VERSION = 1
_T = TypeVar("_T")


class VersionError(ValueError):
    """表示版本分配或状态迁移违反正式发布约束。"""


class VersionConflictError(VersionError):
    """表示版本状态被其他进程或其他身份抢先改变。"""


class VersionStatus(str, Enum):
    """正式版本预留与激活状态。"""

    RESERVED = "reserved"
    READY = "ready"
    ACTIVATING = "activating"
    PUBLISHED = "published"
    ABANDONED = "abandoned"
    CONFLICTED = "conflicted"


@dataclass(frozen=True, slots=True)
class VersionStream:
    """由最终物理版本入口归一化出的版本流身份。"""

    destination_id: str
    object_store_namespace: str
    version_entry_key: str
    stream_id: str


def version_stream(
    destination_id: str,
    object_store_namespace: str,
    version_entry_key: str,
) -> VersionStream:
    """计算不含凭据、对布局稳定的 version_stream_id。"""
    values = (destination_id, object_store_namespace, version_entry_key)
    if any(
        not isinstance(value, str) or not value or any(c in value for c in "\r\n")
        for value in values
    ):
        raise VersionError("版本流身份字段必须是非空且不含换行的字符串")
    stream_id = hashlib.sha256(canonical_json_bytes(list(values))).hexdigest()
    return VersionStream(destination_id, object_store_namespace, version_entry_key, stream_id)


@dataclass(frozen=True, slots=True)
class VersionReservation:
    """一个 FileListNo 预留及其发布状态快照。"""

    reservation_id: str
    stream_id: str
    request_id: str
    build_id: str
    file_list_no: int
    display_version: str
    status: VersionStatus
    bundle_id: str | None = None
    upload_plan_id: str | None = None
    verification_digest: str | None = None
    expected_generation: int | None = None
    expected_entry_digest: str | None = None
    replacement_entry_digest: str | None = None
    replacement_blob: BlobRef | None = None

    def __post_init__(self) -> None:
        """校验版本身份、Int32 边界和状态依赖字段。"""
        for name, value in (
            ("reservation_id", self.reservation_id),
            ("stream_id", self.stream_id),
            ("request_id", self.request_id),
            ("build_id", self.build_id),
            ("display_version", self.display_version),
        ):
            if not isinstance(value, str) or not value or any(c in value for c in "\r\n"):
                raise VersionError(f"{name} 必须是非空且不含换行的字符串")
        if not isinstance(self.file_list_no, int) or isinstance(self.file_list_no, bool):
            raise VersionError("file_list_no 必须是 int")
        if not 1 <= self.file_list_no <= INT32_MAX:
            raise VersionError("file_list_no 超出正 Int32 范围")
        if not isinstance(self.status, VersionStatus):
            raise VersionError("status 必须是 VersionStatus")
        if self.status in {VersionStatus.READY, VersionStatus.ACTIVATING, VersionStatus.PUBLISHED}:
            if not self.bundle_id or not self.upload_plan_id or not self.verification_digest:
                raise VersionError(f"{self.status.value} 必须包含 Bundle、plan 和验证摘要")
        if self.status in {VersionStatus.ACTIVATING, VersionStatus.PUBLISHED}:
            if self.expected_generation is None or not self.replacement_entry_digest:
                raise VersionError(f"{self.status.value} 必须包含入口代际和 replacement 摘要")
        if self.replacement_blob is not None and not isinstance(self.replacement_blob, BlobRef):
            raise VersionError("replacement_blob 必须是 BlobRef 或 None")


@dataclass(frozen=True, slots=True)
class VersionStreamState:
    """一个版本流的持久化状态。"""

    stream_id: str
    allocated_high_watermark: int
    published_high_watermark: int
    reservations: tuple[VersionReservation, ...] = ()

    def __post_init__(self) -> None:
        """校验高水位单调关系和预留身份唯一性。"""
        if not isinstance(self.stream_id, str) or not self.stream_id:
            raise VersionError("stream_id 必须是非空字符串")
        for name, value in (
            ("allocated_high_watermark", self.allocated_high_watermark),
            ("published_high_watermark", self.published_high_watermark),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= INT32_MAX:
                raise VersionError(f"{name} 必须是 0..Int32.MAX_VALUE")
        if self.published_high_watermark > self.allocated_high_watermark:
            raise VersionError("published_high_watermark 不得超过 allocated_high_watermark")
        if not isinstance(self.reservations, tuple):
            raise VersionError("reservations 必须是 tuple")
        if len({item.reservation_id for item in self.reservations}) != len(self.reservations):
            raise VersionError("reservation_id 不得重复")
        if len({item.build_id for item in self.reservations}) != len(self.reservations):
            raise VersionError("build_id 不得重复")
        if any(item.stream_id != self.stream_id for item in self.reservations):
            raise VersionError("预留记录必须属于当前 stream")


class VersionStateStore(Protocol):
    """提供单流原子 read-modify-write 的版本状态存储。"""

    def read(self, stream_id: str) -> VersionStreamState:
        """读取版本流状态，不存在时返回空状态。"""
        ...

    def update(
        self,
        stream_id: str,
        operation: Callable[[VersionStreamState], tuple[VersionStreamState, _T]],
    ) -> _T:
        """在单流互斥边界内更新状态并返回业务结果。"""
        ...


class MemoryVersionStateStore:
    """进程内版本状态存储，供测试和 dry-run 使用。"""

    def __init__(self) -> None:
        """初始化空状态和互斥锁。"""
        self._states: dict[str, VersionStreamState] = {}
        self._lock = threading.Lock()

    def read(self, stream_id: str) -> VersionStreamState:
        """返回指定流快照。"""
        return self._states.get(stream_id, VersionStreamState(stream_id, 0, 0))

    def update(
        self,
        stream_id: str,
        operation: Callable[[VersionStreamState], tuple[VersionStreamState, _T]],
    ) -> _T:
        """串行执行一次单流状态变更。"""
        with self._lock:
            current = self.read(stream_id)
            next_state, result = operation(current)
            if next_state.stream_id != stream_id:
                raise VersionError("更新结果 stream_id 不一致")
            self._states[stream_id] = next_state
            return result


class FileVersionStateStore:
    """以 JSON 文件保存版本流，并用排他 lock 文件避免覆盖并发更新。"""

    def __init__(self, root: Path) -> None:
        """绑定绝对状态根目录。"""
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("root 必须是绝对 Path")
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def read(self, stream_id: str) -> VersionStreamState:
        """读取指定流 JSON，不存在时返回空状态。"""
        path = self._path(stream_id)
        if not path.is_file():
            return VersionStreamState(stream_id, 0, 0)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return _state_from_payload(payload, stream_id)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise VersionError(f"版本状态损坏: {path}") from exc

    def update(
        self,
        stream_id: str,
        operation: Callable[[VersionStreamState], tuple[VersionStreamState, _T]],
    ) -> _T:
        """在 lock 文件存在时快速报冲突，否则原子写入新状态。"""
        path = self._path(stream_id)
        lock_path = path.with_suffix(".lock")
        try:
            with lock_path.open("x", encoding="ascii") as lock:
                lock.write("locked")
            current = self.read(stream_id)
            next_state, result = operation(current)
            if next_state.stream_id != stream_id:
                raise VersionError("更新结果 stream_id 不一致")
            content = (
                json.dumps(_state_payload(next_state), ensure_ascii=False, sort_keys=True) + "\n"
            ).encode()
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, path)
            return result
        except FileExistsError as exc:
            raise VersionConflictError("版本流正在被其他进程更新") from exc
        finally:
            if lock_path.exists():
                lock_path.unlink()

    def _path(self, stream_id: str) -> Path:
        """把流身份映射到根目录内的固定 JSON 文件。"""
        if (
            not isinstance(stream_id, str)
            or len(stream_id) != 64
            or any(character not in "0123456789abcdef" for character in stream_id)
        ):
            raise VersionError("stream_id 必须是小写 SHA256")
        return self._root / f"{stream_id}.json"


@dataclass(frozen=True, slots=True)
class VersionPreview:
    """下一可用 FileListNo 的只读预览。"""

    stream_id: str
    file_list_no: int
    display_version: str


class VersionAllocator:
    """执行正式版本 preview、allocate、ready、activation 和 reconcile。"""

    def __init__(self, store: VersionStateStore) -> None:
        """绑定版本状态存储。"""
        if not callable(getattr(store, "read", None)) or not callable(
            getattr(store, "update", None)
        ):
            raise TypeError("store 必须实现 read/update")
        self._store = store

    def preview(
        self,
        stream: VersionStream,
        *,
        major: int,
        minor: int,
    ) -> VersionPreview:
        """预览下一个未分配号码，不写入状态。"""
        state = self._store.read(stream.stream_id)
        number = _next_number(state)
        return VersionPreview(stream.stream_id, number, f"{major}.{minor}.{number}")

    def allocate(
        self,
        stream: VersionStream,
        *,
        build_id: str,
        request_id: str,
        major: int,
        minor: int,
    ) -> VersionReservation:
        """为正式构建预留 FileListNo；同一 build_id 幂等返回原记录。"""
        _validate_display_parts(build_id, request_id, major, minor)

        def operation(state: VersionStreamState) -> tuple[VersionStreamState, VersionReservation]:
            """在单流锁内创建或返回同一 build 的预留记录。"""
            existing = next(
                (item for item in state.reservations if item.build_id == build_id), None
            )
            if existing is not None:
                return state, existing
            number = _next_number(state)
            reservation_id = hashlib.sha256(
                canonical_json_bytes([stream.stream_id, build_id])
            ).hexdigest()
            reservation = VersionReservation(
                reservation_id,
                stream.stream_id,
                request_id,
                build_id,
                number,
                f"{major}.{minor}.{number}",
                VersionStatus.RESERVED,
            )
            next_state = replace(
                state,
                allocated_high_watermark=number,
                reservations=state.reservations + (reservation,),
            )
            return next_state, reservation

        return self._store.update(stream.stream_id, operation)

    def mark_ready(
        self,
        reservation_id: str,
        *,
        bundle_id: str,
        upload_plan_id: str,
        verification_digest: str,
    ) -> VersionReservation:
        """把 RESERVED 固化为 READY，并对重复相同请求保持幂等。"""
        _validate_identity(bundle_id, "bundle_id")
        _validate_identity(upload_plan_id, "upload_plan_id")
        _validate_identity(verification_digest, "verification_digest")
        reservation = self._find(reservation_id)

        def operation(state: VersionStreamState) -> tuple[VersionStreamState, VersionReservation]:
            """在单流锁内推进 READY 并拒绝身份冲突。"""
            current = _find_in_state(state, reservation_id)
            if current.status is VersionStatus.READY:
                if (
                    current.bundle_id == bundle_id
                    and current.upload_plan_id == upload_plan_id
                    and current.verification_digest == verification_digest
                ):
                    return state, current
                raise VersionConflictError("READY 记录身份不一致")
            if current.status is not VersionStatus.RESERVED:
                raise VersionError(f"{current.status.value} 不能 mark_ready")
            updated = replace(
                current,
                status=VersionStatus.READY,
                bundle_id=bundle_id,
                upload_plan_id=upload_plan_id,
                verification_digest=verification_digest,
            )
            return _replace_reservation(state, updated), updated

        return self._store.update(reservation.stream_id, operation)

    def prepare_activation(
        self,
        reservation_id: str,
        *,
        expected_generation: int,
        expected_entry_digest: str,
        replacement_entry: bytes,
    ) -> VersionReservation:
        """记录入口 CAS 前置条件并把 READY 推进到 ACTIVATING。"""
        if (
            not isinstance(expected_generation, int)
            or isinstance(expected_generation, bool)
            or expected_generation < 0
        ):
            raise VersionError("expected_generation 必须是非负整数")
        if not isinstance(replacement_entry, bytes):
            raise TypeError("replacement_entry 必须是 bytes")
        _validate_digest(expected_entry_digest, "expected_entry_digest")
        replacement_digest = hashlib.sha256(replacement_entry).hexdigest()
        reservation = self._find(reservation_id)
        blob = BlobRef(f"blobs/{replacement_digest}", replacement_digest, len(replacement_entry))

        def operation(state: VersionStreamState) -> tuple[VersionStreamState, VersionReservation]:
            """在单流锁内固化入口 CAS replacement 前置条件。"""
            current = _find_in_state(state, reservation_id)
            if current.status is VersionStatus.ACTIVATING:
                if (
                    current.expected_generation == expected_generation
                    and current.expected_entry_digest == expected_entry_digest
                    and current.replacement_entry_digest == replacement_digest
                ):
                    return state, current
                raise VersionConflictError("ACTIVATING 记录前置条件不一致")
            if current.status is not VersionStatus.READY:
                raise VersionError(f"{current.status.value} 不能 prepare_activation")
            updated = replace(
                current,
                status=VersionStatus.ACTIVATING,
                expected_generation=expected_generation,
                expected_entry_digest=expected_entry_digest,
                replacement_entry_digest=replacement_digest,
                replacement_blob=blob,
            )
            return _replace_reservation(state, updated), updated

        return self._store.update(reservation.stream_id, operation)

    def confirm(
        self,
        reservation_id: str,
        *,
        observed_generation: int,
        observed_entry_digest: str,
    ) -> VersionReservation:
        """确认入口已指向 replacement，并把 ACTIVATING 推进到 PUBLISHED。"""
        reservation = self._find(reservation_id)
        _validate_digest(observed_entry_digest, "observed_entry_digest")

        def operation(state: VersionStreamState) -> tuple[VersionStreamState, VersionReservation]:
            """在单流锁内确认 CAS 回执并推进发布高水位。"""
            current = _find_in_state(state, reservation_id)
            if current.status is VersionStatus.PUBLISHED:
                return state, current
            if current.status is not VersionStatus.ACTIVATING:
                raise VersionError(f"{current.status.value} 不能 confirm")
            if current.expected_generation is None or current.replacement_entry_digest is None:
                raise VersionError("ACTIVATING 缺少 CAS 前置条件")
            if (
                not isinstance(observed_generation, int)
                or observed_generation < current.expected_generation
            ):
                raise VersionConflictError("入口 generation 未达到 replacement 结果")
            if observed_entry_digest != current.replacement_entry_digest:
                raise VersionConflictError("入口摘要不是本次 replacement")
            updated = replace(current, status=VersionStatus.PUBLISHED)
            next_state = replace(
                _replace_reservation(state, updated),
                published_high_watermark=max(state.published_high_watermark, current.file_list_no),
            )
            return next_state, updated

        return self._store.update(reservation.stream_id, operation)

    def reconcile(
        self,
        reservation_id: str,
        *,
        observed_generation: int,
        observed_entry_digest: str,
    ) -> VersionReservation:
        """在崩溃恢复时按同一 replacement 条件确认或标记冲突。"""
        try:
            return self.confirm(
                reservation_id,
                observed_generation=observed_generation,
                observed_entry_digest=observed_entry_digest,
            )
        except VersionConflictError:
            reservation = self._find(reservation_id)

            def operation(
                state: VersionStreamState,
            ) -> tuple[VersionStreamState, VersionReservation]:
                """在单流锁内把未确认的激活记录标记为冲突。"""
                current = _find_in_state(state, reservation_id)
                if current.status is VersionStatus.PUBLISHED:
                    return state, current
                if current.status is not VersionStatus.ACTIVATING:
                    raise VersionError(f"{current.status.value} 不能 reconcile")
                updated = replace(current, status=VersionStatus.CONFLICTED)
                return _replace_reservation(state, updated), updated

            return self._store.update(reservation.stream_id, operation)

    def abandon(self, reservation_id: str) -> VersionReservation:
        """废弃 RESERVED/READY 预留并保留已分配号码。"""
        reservation = self._find(reservation_id)

        def operation(state: VersionStreamState) -> tuple[VersionStreamState, VersionReservation]:
            """在单流锁内废弃尚未激活的预留记录。"""
            current = _find_in_state(state, reservation_id)
            if current.status is VersionStatus.ABANDONED:
                return state, current
            if current.status not in {VersionStatus.RESERVED, VersionStatus.READY}:
                raise VersionError(f"{current.status.value} 不能 abandon")
            updated = replace(current, status=VersionStatus.ABANDONED)
            return _replace_reservation(state, updated), updated

        return self._store.update(reservation.stream_id, operation)

    def _find(self, reservation_id: str) -> VersionReservation:
        """在所有可访问流状态中查找预留记录。"""
        if not isinstance(reservation_id, str) or not reservation_id:
            raise VersionError("reservation_id 必须是非空字符串")
        # 当前 allocator 的 reservation ID 带 stream 之外的索引；调用方通常只管理一个流。
        stores_object: object = getattr(self._store, "_states", None)
        if isinstance(stores_object, dict):
            stores = cast(dict[str, VersionStreamState], stores_object)
            for state in stores.values():
                for reservation in state.reservations:
                    if reservation.reservation_id == reservation_id:
                        return reservation
        root = getattr(self._store, "_root", None)
        if isinstance(root, Path):
            for path in root.glob("*.json"):
                state = self._store.read(path.stem)
                for reservation in state.reservations:
                    if reservation.reservation_id == reservation_id:
                        return reservation
        raise VersionError(f"找不到 reservation_id: {reservation_id}")


def _next_number(state: VersionStreamState) -> int:
    """计算永不复用的下一个正 Int32 号码。"""
    if state.allocated_high_watermark >= INT32_MAX:
        raise VersionError("FileListNo 已达到 Int32.MAX_VALUE")
    return max(state.allocated_high_watermark, state.published_high_watermark) + 1


def _validate_identity(value: str, field_name: str) -> None:
    """校验发布身份摘要文本。"""
    if not isinstance(value, str) or not value or any(c in value for c in "\r\n"):
        raise VersionError(f"{field_name} 非法")


def _validate_digest(value: str, field_name: str) -> None:
    """校验小写 SHA256 文本。"""
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise VersionError(f"{field_name} 必须是小写 SHA256")


def _validate_display_parts(build_id: str, request_id: str, major: int, minor: int) -> None:
    """校验分配请求的公开显示版本组成。"""
    _validate_identity(build_id, "build_id")
    _validate_identity(request_id, "request_id")
    for name, value in (("major", major), ("minor", minor)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise VersionError(f"{name} 必须是非负整数")


def _find_in_state(state: VersionStreamState, reservation_id: str) -> VersionReservation:
    """从单流状态查找预留。"""
    for reservation in state.reservations:
        if reservation.reservation_id == reservation_id:
            return reservation
    raise VersionError(f"找不到 reservation_id: {reservation_id}")


def _replace_reservation(
    state: VersionStreamState, replacement: VersionReservation
) -> VersionStreamState:
    """用新快照替换同一 reservation。"""
    return replace(
        state,
        reservations=tuple(
            replacement if item.reservation_id == replacement.reservation_id else item
            for item in state.reservations
        ),
    )


def _state_payload(state: VersionStreamState) -> dict[str, object]:
    """生成持久化 JSON payload。"""
    return {
        "allocated_high_watermark": state.allocated_high_watermark,
        "published_high_watermark": state.published_high_watermark,
        "reservations": [_reservation_payload(item) for item in state.reservations],
        "schema_version": _STATE_SCHEMA_VERSION,
        "stream_id": state.stream_id,
    }


def _reservation_payload(reservation: VersionReservation) -> dict[str, object]:
    """生成一个预留记录的稳定 JSON mapping。"""
    return {
        "bundle_id": reservation.bundle_id,
        "build_id": reservation.build_id,
        "display_version": reservation.display_version,
        "expected_entry_digest": reservation.expected_entry_digest,
        "expected_generation": reservation.expected_generation,
        "file_list_no": reservation.file_list_no,
        "request_id": reservation.request_id,
        "replacement_blob": (
            None
            if reservation.replacement_blob is None
            else {
                "locator": reservation.replacement_blob.locator,
                "sha256": reservation.replacement_blob.sha256,
                "size": reservation.replacement_blob.size,
            }
        ),
        "replacement_entry_digest": reservation.replacement_entry_digest,
        "reservation_id": reservation.reservation_id,
        "status": reservation.status.value,
        "stream_id": reservation.stream_id,
        "upload_plan_id": reservation.upload_plan_id,
        "verification_digest": reservation.verification_digest,
    }


def _state_from_payload(payload: object, stream_id: str) -> VersionStreamState:
    """严格读取 JSON 状态，拒绝未知 schema 和缺失字段。"""
    if not isinstance(payload, dict):
        raise VersionError("版本状态根节点必须是 object")
    payload_map = cast(dict[str, object], payload)
    if payload_map.get("schema_version") != _STATE_SCHEMA_VERSION:
        raise VersionError("版本状态 schema_version 不受支持")
    raw_items_object = payload_map.get("reservations")
    if not isinstance(raw_items_object, list):
        raise VersionError("版本状态 reservations 必须是 list")
    raw_items = cast(list[object], raw_items_object)
    items: list[VersionReservation] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise VersionError("版本预留记录必须是 object")
        raw_map = cast(dict[str, object], raw)
        blob_raw = raw_map.get("replacement_blob")
        blob = None
        if blob_raw is not None:
            if not isinstance(blob_raw, dict):
                raise VersionError("replacement_blob 必须是 object 或 null")
            blob_map = cast(dict[str, object], blob_raw)
            blob = BlobRef(
                _required_json_str(blob_map, "locator"),
                _required_json_str(blob_map, "sha256"),
                _required_json_int(blob_map, "size"),
            )
        items.append(
            VersionReservation(
                _required_json_str(raw_map, "reservation_id"),
                _required_json_str(raw_map, "stream_id"),
                _required_json_str(raw_map, "request_id"),
                _required_json_str(raw_map, "build_id"),
                _required_json_int(raw_map, "file_list_no"),
                _required_json_str(raw_map, "display_version"),
                VersionStatus(_required_json_str(raw_map, "status")),
                _optional_json_str(raw_map, "bundle_id"),
                _optional_json_str(raw_map, "upload_plan_id"),
                _optional_json_str(raw_map, "verification_digest"),
                _optional_json_int(raw_map, "expected_generation"),
                _optional_json_str(raw_map, "expected_entry_digest"),
                _optional_json_str(raw_map, "replacement_entry_digest"),
                blob,
            )
        )
    return VersionStreamState(
        stream_id,
        _required_json_int(payload_map, "allocated_high_watermark"),
        _required_json_int(payload_map, "published_high_watermark"),
        tuple(items),
    )


def _required_json_str(payload: dict[str, object], key: str) -> str:
    """读取持久化 JSON 中的必需字符串字段。"""
    value = payload.get(key)
    if not isinstance(value, str):
        raise VersionError(f"版本状态字段 {key} 必须是 str")
    return value


def _required_json_int(payload: dict[str, object], key: str) -> int:
    """读取持久化 JSON 中的必需非布尔整数。"""
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise VersionError(f"版本状态字段 {key} 必须是 int")
    return value


def _optional_json_str(payload: dict[str, object], key: str) -> str | None:
    """读取持久化 JSON 中的可选字符串字段。"""
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise VersionError(f"版本状态字段 {key} 必须是 str 或 null")
    return value


def _optional_json_int(payload: dict[str, object], key: str) -> int | None:
    """读取持久化 JSON 中的可选非布尔整数。"""
    value = payload.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise VersionError(f"版本状态字段 {key} 必须是 int 或 null")
    return value


__all__ = [
    "FileVersionStateStore",
    "INT32_MAX",
    "MemoryVersionStateStore",
    "VersionAllocator",
    "VersionError",
    "VersionConflictError",
    "VersionPreview",
    "VersionReservation",
    "VersionStatus",
    "VersionStream",
    "VersionStreamState",
    "version_stream",
]
