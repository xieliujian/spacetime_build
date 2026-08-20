"""Android 包体签名的短秘密租约执行器。

本模块消费 ``AndroidSigningPlan``，通过 ``SecretProvider`` 在最短边界解析签名材料，
再把材料绑定到受控临时文件或敏感环境槽位；签名工具的 argv 只包含路径和公开参数。
签名完成、失败、取消时都关闭租约并删除临时文件，不把秘密写入 Gradle 请求、日志或
PackageManifest。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.errors import ToolExecutionError
from package.platforms.android.signing import AndroidSecretDelivery, AndroidSigningPlan
from ports.process import CancellationToken, ProcessOutcome, ProcessRequest, ProcessRunner
from ports.secrets import SecretLease, SecretLeaseRequest, SecretProvider


@dataclass(frozen=True, slots=True)
class AndroidSigningResult:
    """Android 签名进程结果和已签名产物路径。"""

    artifact_path: Path
    process_result: object


class AndroidPackageSigner:
    """使用短期 SecretLease 调用受控 Android 签名工具。"""

    def __init__(self, process_runner: ProcessRunner, secret_provider: SecretProvider) -> None:
        """绑定进程和秘密端口，不申请租约。"""
        self._process_runner = process_runner
        self._secret_provider = secret_provider

    def sign(
        self,
        artifact_path: Path,
        plan: AndroidSigningPlan,
        executable: Path,
        log_directory: Path,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AndroidSigningResult:
        """为 APK/AAB 产物执行一次签名并在 finally 清理秘密材料。

        参数：
            artifact_path: 已构建包体的普通文件路径。
            plan: 不含秘密明文的 Android 签名计划。
            executable: apksigner 或固定签名 wrapper 的绝对路径。
            log_directory: 受控日志目录绝对路径。
            cancellation: 可选协作取消令牌。

        返回：
            ``AndroidSigningResult``，不携带秘密值。

        异常：
            输入、租约、进程失败或取消时抛 ``ValueError`` / ``ToolExecutionError``。

        约束与副作用：
            只有签名工具调用和受控临时材料会产生副作用；秘密租约始终关闭。
        """
        if not isinstance(artifact_path, Path) or not artifact_path.is_file():
            raise ValueError("artifact_path 必须是存在的普通文件")
        if not isinstance(plan, AndroidSigningPlan):
            raise TypeError("plan 必须是 AndroidSigningPlan")
        if not isinstance(executable, Path) or not executable.is_absolute():
            raise ValueError("executable 必须是绝对 Path")
        if not isinstance(log_directory, Path) or not log_directory.is_absolute():
            raise ValueError("log_directory 必须是绝对 Path")
        if cancellation is not None and cancellation.is_cancelled:
            raise ToolExecutionError("Android 签名已取消")
        log_directory.mkdir(parents=True, exist_ok=True)
        lease = self._secret_provider.acquire(
            SecretLeaseRequest(plan.secret_ref, "android-package-signing", ("keystore",))
        )
        temporary_path: Path | None = None
        try:
            environment: tuple[tuple[str, str], ...] = ()
            if plan.delivery is AndroidSecretDelivery.TEMP_FILE:
                temporary_path = self._write_temporary_material(log_directory, lease)
                material_argument = temporary_path.as_posix()
            else:
                secret = lease.resolve("keystore")
                environment = (("ANDROID_KEYSTORE_MATERIAL", secret),)
                material_argument = "env:ANDROID_KEYSTORE_MATERIAL"
            request = ProcessRequest(
                executable,
                ("sign", "--keystore-material", material_argument, artifact_path.as_posix()),
                artifact_path.parent,
                log_directory / "android-sign.stdout.log",
                log_directory / "android-sign.stderr.log",
                environment=environment,
            )
            result = self._process_runner.run(request, cancellation)
            if result.outcome is not ProcessOutcome.COMPLETED or result.exit_code != 0:
                raise ToolExecutionError(
                    f"Android 签名失败: outcome={result.outcome.value}, exit_code={result.exit_code}, "
                    f"diagnostic={result.diagnostic_message}"
                )
            return AndroidSigningResult(artifact_path, result)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
            lease.close()

    @staticmethod
    def _write_temporary_material(log_directory: Path, lease: SecretLease) -> Path:
        """把租约材料写入受控临时文件并返回路径，不返回秘密文本。"""
        descriptor, name = tempfile.mkstemp(prefix=".android-keystore-", dir=log_directory)
        path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(lease.resolve("keystore").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            return path
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                path.unlink()
            except OSError:
                pass
            raise


__all__ = ["AndroidPackageSigner", "AndroidSigningResult"]
