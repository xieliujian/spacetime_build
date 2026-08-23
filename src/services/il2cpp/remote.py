"""IL2CPP 远端 CI 与 ObjectStore 协调器。

远端执行器只发送固定参数和内容寻址身份，使用 cache key 作为 Jenkins 幂等键；它不
上传本地路径、不携带秘密、不轮询到无限等待。CI 成功后必须通过 ObjectStore verify
回读输出摘要，缺失或摘要不符时 fail closed。
"""

from __future__ import annotations

from ports.ci import CiJobClient, CiJobRequest, CiJobState
from ports.storage import ObjectStore, StoredObject
from services.il2cpp.model import Il2CppBuildResult, Il2CppStatus
from services.il2cpp.planner import Il2CppCommandPlan
from core.artifacts import BlobRef


class Il2CppRemoteExecutor:
    """触发一次幂等远端 IL2CPP Job 并验证 CAS 输出。"""

    def __init__(self, ci_client: CiJobClient, object_store: ObjectStore) -> None:
        """保存 CI 和对象存储端口。"""
        if not callable(getattr(ci_client, "trigger", None)) or not callable(
            getattr(ci_client, "get_status", None)
        ):
            raise TypeError("ci_client 未实现 trigger/get_status")
        if not callable(getattr(object_store, "verify", None)):
            raise TypeError("object_store 未实现 verify")
        self._ci_client = ci_client
        self._object_store = object_store

    def run(self, plan: Il2CppCommandPlan) -> Il2CppBuildResult:
        """触发远端 Job，查询一次状态并验证输出对象。"""
        if not isinstance(plan, Il2CppCommandPlan):
            raise TypeError("plan 必须是 Il2CppCommandPlan")
        request = plan.execution.request
        if request.mode.value != "remote":
            raise ValueError("Il2CppRemoteExecutor 只接受 remote 计划")
        ci_request = CiJobRequest(
            "spacetime-il2cpp",
            (
                ("request_id", request.request_id),
                ("input_locator", request.input_snapshot.locator),
                ("output_locator", plan.execution.output_locator),
                ("platform", request.platform.value),
                ("architecture", request.architecture),
            ),
            plan.execution.cache_key,
        )
        handle = self._ci_client.trigger(ci_request)
        status = self._ci_client.get_status(handle)
        if status.state in {CiJobState.QUEUED, CiJobState.RUNNING}:
            return Il2CppBuildResult(
                request.request_id, Il2CppStatus.RUNNING, None, "CI Job 仍在运行"
            )
        if status.state is not CiJobState.SUCCESS:
            return Il2CppBuildResult(request.request_id, Il2CppStatus.FAILED, None, "CI Job 未成功")
        reference = StoredObject(plan.execution.output_locator, plan.execution.cache_key, 0)
        verification = self._object_store.verify(reference)
        if not verification.exists or not verification.sha256 or verification.size is None:
            return Il2CppBuildResult(
                request.request_id, Il2CppStatus.FAILED, None, "IL2CPP 远端输出不存在"
            )
        blob = BlobRef(reference.key, verification.sha256, verification.size)
        return Il2CppBuildResult(request.request_id, Il2CppStatus.SUCCEEDED, blob, "")


__all__ = ["Il2CppRemoteExecutor"]
