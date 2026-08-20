"""IL2CPP 构建请求、计划、结果和状态模型测试。"""

from pathlib import Path

import pytest

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from services.il2cpp.model import (
    Il2CppBuildRequest,
    Il2CppBuildResult,
    Il2CppExecutionMode,
    Il2CppExecutionPlan,
    Il2CppStatus,
)


def _blob(char: str = "a") -> BlobRef:
    """构造内容寻址测试 Blob。"""
    digest = char * 64
    return BlobRef(f"blobs/{digest}", digest, 10)


def _request(mode: Il2CppExecutionMode = Il2CppExecutionMode.LOCAL) -> Il2CppBuildRequest:
    """构造固定 IL2CPP 请求。"""
    return Il2CppBuildRequest(
        request_id="request-1",
        platform=BuildPlatform.ANDROID,
        architecture="arm64-v8a",
        input_snapshot=_blob(),
        unity_version="2022.3.62f2",
        toolchain_digest="toolchain-digest",
        mode=mode,
        protection_policy=None,
    )


def test_il2cpp_request_and_plan_keep_local_remote_and_protection_explicit() -> None:
    """验证请求和计划显式区分执行模式、架构和输入 Blob。"""
    request = _request(Il2CppExecutionMode.REMOTE)
    plan = Il2CppExecutionPlan(
        request=request,
        workspace=Path("C:/isolated/il2cpp"),
        output_locator="blobs/" + "b" * 64,
        cache_key="c" * 64,
    )

    assert request.mode is Il2CppExecutionMode.REMOTE
    assert plan.request.input_snapshot == _blob()
    assert plan.output_locator == "blobs/" + "b" * 64


def test_il2cpp_result_requires_output_for_success_and_preserves_failure_status() -> None:
    """验证成功必须有输出 Blob，失败结果可以只携带脱敏诊断。"""
    success = Il2CppBuildResult(
        request_id="request-1",
        status=Il2CppStatus.SUCCEEDED,
        output_snapshot=_blob("b"),
        diagnostic="",
    )
    failed = Il2CppBuildResult(
        request_id="request-1",
        status=Il2CppStatus.FAILED,
        output_snapshot=None,
        diagnostic="tool failed",
    )

    assert success.output_snapshot == _blob("b")
    assert failed.output_snapshot is None


def test_il2cpp_model_rejects_invalid_identity_and_success_without_output() -> None:
    """验证空身份、非法模式、保护空值和无输出成功状态全部拒绝。"""
    with pytest.raises(ValueError):
        Il2CppBuildRequest(
            "", BuildPlatform.IOS, "arm64", _blob(), "2022", "tool", Il2CppExecutionMode.LOCAL, None
        )
    with pytest.raises(ValueError):
        Il2CppBuildRequest(
            "id", BuildPlatform.IOS, "", _blob(), "2022", "tool", Il2CppExecutionMode.LOCAL, None
        )
    with pytest.raises(ValueError):
        Il2CppBuildRequest(
            "id", BuildPlatform.IOS, "arm64", _blob(), "2022", "tool", Il2CppExecutionMode.LOCAL, ""
        )
    with pytest.raises(ValueError, match="output"):
        Il2CppBuildResult("id", Il2CppStatus.SUCCEEDED, None, "")
