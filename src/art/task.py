"""把 ArtTaskRequest 接入通用 BuildTask 规划/执行协议。"""

from __future__ import annotations

import hashlib
from typing import Protocol

from art.model import ArtTaskRequest, ArtTaskResult
from core.manifest_codec import canonical_json_bytes
from core.tasks import ArtifactCollection, BuildContext, TaskPlan, TaskResult, TaskSpec


class ArtOperationExecutor(Protocol):
    """美术操作执行器端口，不携带命令拼接能力。"""

    def plan(self, request: ArtTaskRequest) -> tuple[str, ...]:
        """返回精确逻辑输出路径。"""
        ...

    def execute(
        self,
        request: ArtTaskRequest,
        context: BuildContext,
        inputs: ArtifactCollection,
    ) -> ArtTaskResult:
        """执行一次已规划操作并返回 CAS 结果。"""
        ...


class ArtTask:
    """将专用 art executor 适配为核心 BuildTask。"""

    def __init__(self, request: ArtTaskRequest, executor: ArtOperationExecutor) -> None:
        """保存不可变请求和外部执行器端口。"""
        if not isinstance(request, ArtTaskRequest):
            raise TypeError("request 必须是 ArtTaskRequest")
        if not callable(getattr(executor, "plan", None)) or not callable(
            getattr(executor, "execute", None)
        ):
            raise TypeError("executor 必须实现 plan 和 execute")
        self._request = request
        self._executor = executor

    @property
    def name(self) -> str:
        """返回任务标识。"""
        return self._request.task_id

    def plan(self, context: BuildContext) -> TaskPlan:
        """生成包含规则、操作和输出集合的完整 TaskPlan。"""
        if not isinstance(context, BuildContext):
            raise TypeError("context 必须是 BuildContext")
        outputs = self._executor.plan(self._request)
        if (
            not isinstance(outputs, tuple)
            or not outputs
            or not all(isinstance(path, str) and path and "\\" not in path for path in outputs)
        ):
            raise ValueError("art executor.plan 必须返回非空安全逻辑路径 tuple")
        if len(set(outputs)) != len(outputs):
            raise ValueError("art 输出逻辑路径不得重复")
        request_payload = {
            "input_snapshot_id": self._request.input_snapshot_id,
            "operation": self._request.operation.value,
            "options": [list(item) for item in self._request.options],
            "rule_version": self._request.rule_version,
            "task_id": self._request.task_id,
        }
        digest = hashlib.sha256(canonical_json_bytes(request_payload)).hexdigest()
        spec = TaskSpec(
            self.name,
            (),
            frozenset(outputs),
            "art-1",
            (
                ("operation", self._request.operation.value),
                ("rule_version", self._request.rule_version),
            ),
        )
        return TaskPlan(spec, digest, digest)

    def execute(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """执行 art executor 并把 CAS 结果映射为核心 TaskResult。"""
        if not isinstance(context, BuildContext):
            raise TypeError("context 必须是 BuildContext")
        if not isinstance(inputs, ArtifactCollection):
            raise TypeError("inputs 必须是 ArtifactCollection")
        result = self._executor.execute(self._request, context, inputs)
        if not isinstance(result, ArtTaskResult):
            raise TypeError("executor.execute 必须返回 ArtTaskResult")
        return TaskResult(result.artifacts)


__all__ = ["ArtOperationExecutor", "ArtTask"]
