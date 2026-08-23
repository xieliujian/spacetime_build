"""IL2CPP 本地 Unity 执行器。

执行器消费 ``Il2CppCommandPlan``，通过统一 ``ProcessRunner`` 启动一次 Unity，严格检查
预期 zip 输出后才交给 ``BlobCommitter`` 写入 CAS。它不执行 shell、不读取全量环境、
不重试失败进程，也不把 workspace 路径写入结果模型。
"""

from __future__ import annotations

from pathlib import Path

from core.errors import ArtifactValidationError
from ports.process import CancellationToken, ProcessOutcome, ProcessRequest, ProcessRunner
from resource.blob_committer import BlobCommitter
from services.il2cpp.model import Il2CppBuildResult, Il2CppStatus
from services.il2cpp.planner import Il2CppCommandPlan


class Il2CppLocalExecutor:
    """执行固定 IL2CPP 计划并提交生成归档。"""

    def __init__(
        self,
        process_runner: ProcessRunner,
        *,
        executable: Path,
        blob_committer: BlobCommitter,
        log_root: Path,
        timeout_seconds: float | None = None,
    ) -> None:
        """保存进程、Unity、CAS 和日志依赖。"""
        if not callable(getattr(process_runner, "run", None)):
            raise TypeError("process_runner 必须实现 run")
        if not isinstance(executable, Path) or not executable.is_absolute():
            raise ValueError("executable 必须是绝对 Path")
        if not isinstance(blob_committer, BlobCommitter):
            raise TypeError("blob_committer 必须是 BlobCommitter")
        if not isinstance(log_root, Path) or not log_root.is_absolute():
            raise ValueError("log_root 必须是绝对 Path")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds 必须是正数或 None")
        self._process_runner = process_runner
        self._executable = executable
        self._blob_committer = blob_committer
        self._log_root = log_root
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        plan: Il2CppCommandPlan,
        cancellation: CancellationToken | None = None,
    ) -> Il2CppBuildResult:
        """运行 IL2CPP、校验归档并返回成功或终态失败结果。"""
        if not isinstance(plan, Il2CppCommandPlan):
            raise TypeError("plan 必须是 Il2CppCommandPlan")
        workspace = plan.execution.workspace
        workspace.mkdir(parents=True, exist_ok=True)
        self._log_root.mkdir(parents=True, exist_ok=True)
        output = workspace / "il2cpp-output.zip"
        stdout = self._log_root / f"{plan.execution.request.request_id}.stdout.log"
        stderr = self._log_root / f"{plan.execution.request.request_id}.stderr.log"
        arguments = plan.arguments[1:]
        timeout = self._timeout_seconds or 1_800.0
        request = ProcessRequest(
            executable=self._executable,
            arguments=arguments,
            working_directory=workspace,
            stdout_path=stdout,
            stderr_path=stderr,
            environment=plan.environment,
            timeout_seconds=timeout,
        )
        process_result = self._process_runner.run(request, cancellation)
        if process_result.outcome is ProcessOutcome.CANCELLED:
            return Il2CppBuildResult(
                plan.execution.request.request_id,
                Il2CppStatus.CANCELLED,
                None,
                process_result.diagnostic_message or "IL2CPP 已取消",
            )
        if process_result.outcome is not ProcessOutcome.COMPLETED or process_result.exit_code != 0:
            return Il2CppBuildResult(
                plan.execution.request.request_id,
                Il2CppStatus.FAILED,
                None,
                process_result.diagnostic_message
                or f"IL2CPP 进程失败: {process_result.outcome.value}",
            )
        if output.is_symlink() or not output.is_file():
            return Il2CppBuildResult(
                plan.execution.request.request_id,
                Il2CppStatus.FAILED,
                None,
                "IL2CPP 缺少 il2cpp-output.zip 普通输出",
            )
        try:
            blob = self._blob_committer.commit(output, allowed_root=workspace)
        except (OSError, ValueError, ArtifactValidationError) as exc:
            return Il2CppBuildResult(
                plan.execution.request.request_id,
                Il2CppStatus.FAILED,
                None,
                f"IL2CPP 输出提交失败: {type(exc).__name__}",
            )
        return Il2CppBuildResult(
            plan.execution.request.request_id,
            Il2CppStatus.SUCCEEDED,
            blob,
            "",
        )


__all__ = ["Il2CppLocalExecutor"]
