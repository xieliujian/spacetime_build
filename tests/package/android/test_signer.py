"""验证 Android 签名执行的短租约、参数脱敏和清理边界。"""

from pathlib import Path

import pytest

from configuration.model import SecretRef
from core.errors import ToolExecutionError
from observability.failures import ErrorCode
from package.platforms.android.model import (
    AndroidAbi,
    AndroidBuildType,
    AndroidOutputKind,
    AndroidPackageOptions,
)
from package.platforms.android.signer import AndroidPackageSigner
from package.platforms.android.signing import AndroidSigningPlanner
from ports.process import ProcessOutcome, ProcessRequest, ProcessResult
from ports.secrets import SecretLeaseRequest


class _Lease:
    """记录 close 的秘密租约替身。"""

    def __init__(self) -> None:
        """创建未关闭租约。"""
        self.closed = False

    def resolve(self, binding_id: str) -> str:
        """返回测试秘密。"""
        return "test-keystore-material"

    def close(self) -> None:
        """清除租约状态。"""
        self.closed = True


class _Provider:
    """记录租约申请的凭据提供器替身。"""

    def __init__(self) -> None:
        """创建空调用记录。"""
        self.requests: list[SecretLeaseRequest] = []
        self.lease = _Lease()

    def acquire(self, request: SecretLeaseRequest) -> _Lease:
        """保存申请并返回租约。"""
        self.requests.append(request)
        return self.lease


class _Runner:
    """记录签名进程请求的替身。"""

    def __init__(self, success: bool = True) -> None:
        """创建成功或失败模式。"""
        self.success = success
        self.request: ProcessRequest | None = None

    def run(self, request: ProcessRequest, cancellation: object = None) -> ProcessResult:
        """记录请求并返回结果。"""
        self.request = request
        if self.success:
            return ProcessResult(
                ProcessOutcome.COMPLETED, 0, 0, request.stdout_path, request.stderr_path, 0, 0
            )
        return ProcessResult(
            ProcessOutcome.START_FAILED,
            None,
            0,
            request.stdout_path,
            request.stderr_path,
            0,
            0,
            error_code=ErrorCode.INTERNAL_ERROR,
            diagnostic_message="sign failed",
        )


def _plan():
    """创建 APK 签名计划。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.APK,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        1,
    )
    return AndroidSigningPlanner.plan(options, SecretRef("secret://android/keystore"), "a" * 64)


def test_signer_uses_short_lease_without_putting_secret_in_arguments(tmp_path: Path) -> None:
    """Given 签名计划，When 执行，Then 租约关闭且 argv 不含秘密。"""
    artifact = tmp_path / "game.apk"
    artifact.write_bytes(b"apk")
    provider = _Provider()
    runner = _Runner()

    result = AndroidPackageSigner(runner, provider).sign(
        artifact,
        _plan(),
        tmp_path / "apksigner",
        tmp_path / "logs",
    )

    assert result.artifact_path == artifact
    assert provider.lease.closed is True
    assert runner.request is not None
    assert "test-keystore-material" not in runner.request.arguments


def test_signer_closes_lease_when_process_fails(tmp_path: Path) -> None:
    """Given apksigner 失败，When 执行，Then 抛工具错误且租约仍关闭。"""
    artifact = tmp_path / "game.apk"
    artifact.write_bytes(b"apk")
    provider = _Provider()
    with pytest.raises(ToolExecutionError):
        AndroidPackageSigner(_Runner(False), provider).sign(
            artifact,
            _plan(),
            tmp_path / "apksigner",
            tmp_path / "logs",
        )
    assert provider.lease.closed is True
