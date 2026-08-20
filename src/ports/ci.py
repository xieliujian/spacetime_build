"""Jenkins 等 CI 系统的稳定端口契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CiJobRequest:
    """触发一个受控 CI Job 的请求。"""

    job_name: str
    parameters: tuple[tuple[str, str], ...]
    idempotency_key: str

    def __post_init__(self) -> None:
        """校验 Job 名、参数唯一性和幂等键。"""
        for field_name, value in (
            ("job_name", self.job_name),
            ("idempotency_key", self.idempotency_key),
        ):
            if not isinstance(value, str) or not value or any(c in value for c in "\r\n"):
                raise ValueError(f"{field_name} 无效")
        if not isinstance(self.parameters, tuple):
            raise TypeError("parameters 必须是 tuple")
        names: set[str] = set()
        for name, value in self.parameters:
            if not isinstance(name, str) or not name or name.casefold() in names:
                raise ValueError("CI 参数名必须非空且唯一")
            if not isinstance(value, str) or any(c in value for c in "\r\n"):
                raise ValueError("CI 参数值无效")
            names.add(name.casefold())


@dataclass(frozen=True, slots=True)
class CiJobHandle:
    """CI 队列或构建句柄。"""

    job_name: str
    queue_id: str
    build_id: int | None = None

    def __post_init__(self) -> None:
        """校验句柄字段。"""
        if not self.job_name or not self.queue_id:
            raise ValueError("job_name 和 queue_id 必须非空")
        if self.build_id is not None and (not isinstance(self.build_id, int) or self.build_id <= 0):
            raise ValueError("build_id 必须是正整数或 None")


class CiJobState(str, Enum):
    """CI Job 生命周期状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CiJobStatus:
    """CI 状态查询结果。"""

    handle: CiJobHandle
    state: CiJobState
    result: str | None = None


class CiJobClient(Protocol):
    """CI Job 客户端的结构化协议基类。"""

    def trigger(self, request: CiJobRequest) -> CiJobHandle:
        """触发 Job 并返回可轮询句柄。"""
        ...

    def get_status(self, handle: CiJobHandle) -> CiJobStatus:
        """查询 Job 当前状态。"""
        ...

    def cancel(self, handle: CiJobHandle) -> bool:
        """请求取消 Job，返回是否发生状态变化。"""
        ...
