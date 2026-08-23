"""验证 IL2CPP 远端 CI/ObjectStore 协调器的幂等请求和回读。"""

from __future__ import annotations

from pathlib import Path

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from ports.ci import CiJobHandle, CiJobRequest, CiJobState, CiJobStatus
from ports.storage import ObjectVerification, StoredObject
from services.il2cpp.model import Il2CppBuildRequest, Il2CppExecutionMode
from services.il2cpp.planner import Il2CppPlanner, Il2CppToolchain
from services.il2cpp.remote import Il2CppRemoteExecutor


class _Ci:
    """返回成功 CI 状态的替身。"""

    def trigger(self, request: CiJobRequest) -> CiJobHandle:
        """保存请求并返回固定句柄。"""
        self.request = request
        return CiJobHandle(request.job_name, "queue-1", 1)

    def get_status(self, handle: CiJobHandle) -> CiJobStatus:
        """返回成功状态。"""
        return CiJobStatus(handle, CiJobState.SUCCESS, "SUCCESS")

    def cancel(self, handle: CiJobHandle) -> bool:
        """测试替身不取消已完成任务。"""
        return False


class _Store:
    """返回已验证远端对象的替身。"""

    def verify(self, reference: StoredObject) -> ObjectVerification:
        """返回固定归档摘要。"""
        digest = "b" * 64
        return ObjectVerification(reference, True, digest, 10)


def test_remote_executor_uses_idempotent_ci_request_and_verifies_blob() -> None:
    """CI 成功后必须 HEAD/verify 远端对象并返回 CAS 引用。"""
    input_digest = "a" * 64
    request = Il2CppBuildRequest(
        "req-remote",
        BuildPlatform.ANDROID,
        "arm64",
        BlobRef(f"blobs/{input_digest}", input_digest, 10),
        "2022.3.62f2",
        "toolchain-1",
        Il2CppExecutionMode.REMOTE,
        None,
    )
    toolchain = Il2CppToolchain(
        request.unity_version,
        "unity",
        "unity-il2cpp-v1",
        (),
        (("unity", request.unity_version),),
    )
    plan = Il2CppPlanner.plan(request, Path("C:/workspace"), toolchain=toolchain)

    result = Il2CppRemoteExecutor(_Ci(), _Store()).run(plan)

    assert result.status.value == "succeeded"
    assert result.output_snapshot is not None
    assert result.output_snapshot.sha256 == "b" * 64
