"""Android Gradle 配置计划的受控 workspace 应用器。

应用器只把已经白名单化的 ``GradleConfigurationPlan`` 序列化为确定性 JSON，再通过
注入的 ``ProcessRunner`` 调用仓库内固定脚本。请求文件在目标 workspace 内原子写入；
相同计划幂等返回，不同计划拒绝覆盖，进程失败时删除本次新文件。它不执行请求中的
脚本、不读取环境秘密，也不修改 workspace 之外的路径。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.errors import BuildError
from package.platforms.android.gradle_config import GradleConfigurationPlan
from ports.process import (
    CancellationToken,
    ProcessOutcome,
    ProcessRequest,
    ProcessResult,
    ProcessRunner,
)


class GradleApplyError(BuildError):
    """表示 Gradle 配置请求无法安全应用。"""


class GradleApplyConflict(GradleApplyError):
    """表示 workspace 已存在不同的配置请求。"""


class GradleApplyCancelled(GradleApplyError):
    """表示应用前或应用期间收到取消请求。"""


@dataclass(frozen=True, slots=True)
class GradleApplyResult:
    """Gradle 配置应用的不可变回执。"""

    request_path: Path
    process_result: ProcessResult | None
    changed: bool
    idempotent: bool


def _request_bytes(plan: GradleConfigurationPlan) -> bytes:
    """将配置计划编码为固定字段顺序无关的 JSON 字节。"""
    document = {
        "abis": [abi.value for abi in plan.abis],
        "application_id": plan.application_id,
        "build_type": plan.build_type.value,
        "offline_lock": plan.offline_lock,
        "output_kind": plan.output_kind.value,
        "repositories": list(plan.repositories),
        "version_code": plan.version_code,
    }
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _workspace_child(root: Path, relative: str) -> Path:
    """返回 workspace 内的安全子路径。"""
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GradleApplyError(f"Gradle 应用路径逃逸 workspace: {relative}") from exc
    return candidate


class GradleConfigurationApplier:
    """通过固定 Gradle script 在隔离 workspace 应用配置计划。"""

    def __init__(
        self,
        process_runner: ProcessRunner,
        script_path: Path,
        *,
        gradle_executable: Path | None = None,
        timeout_seconds: float = 600.0,
    ) -> None:
        """绑定进程端口、固定脚本和显式工具路径，不启动进程。"""
        if not isinstance(script_path, Path) or not script_path.is_absolute():
            raise ValueError("script_path 必须是绝对 Path")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是正数")
        if gradle_executable is None:
            raise ValueError("gradle_executable 必须显式提供绝对 Path")
        executable = gradle_executable
        if not executable.is_absolute():
            raise ValueError("gradle_executable 必须是绝对 Path")
        self._process_runner = process_runner
        self._script_path = script_path
        self._gradle_executable = executable
        self._timeout_seconds = float(timeout_seconds)

    def apply(
        self,
        plan: GradleConfigurationPlan,
        workspace: Path,
        *,
        cancellation: CancellationToken | None = None,
    ) -> GradleApplyResult:
        """原子写入请求并通过 ProcessRunner 应用 Gradle 配置。

        参数：
            plan: 已由 ``GradleConfigurationPlanner`` 创建的白名单计划。
            workspace: 已存在的绝对 Android Gradle workspace 根目录。
            cancellation: 可选协作取消令牌。

        返回：
            新应用为 changed，已有相同请求为 idempotent；进程结果保留供审计。

        异常：
            workspace/计划类型错误、不同请求冲突、取消或进程非零结果时抛
            ``GradleApplyError`` 子类。

        约束与副作用：
            只写 workspace 内的固定请求文件；失败回滚本次新增文件，不执行任意请求代码。
        """
        if not isinstance(plan, GradleConfigurationPlan):
            raise TypeError("plan 必须是 GradleConfigurationPlan")
        if not isinstance(workspace, Path) or not workspace.is_absolute() or not workspace.is_dir():
            raise GradleApplyError("workspace 必须是已存在的绝对目录")
        if not self._script_path.is_file():
            raise GradleApplyError(f"固定 Gradle script 不存在: {self._script_path}")
        if cancellation is not None and cancellation.is_cancelled:
            raise GradleApplyCancelled("Gradle 配置应用已取消")
        root = workspace.resolve()
        state_directory = _workspace_child(root, ".spacetime")
        state_directory.mkdir(parents=False, exist_ok=True)
        request_path = _workspace_child(root, ".spacetime/gradle-config-request.json")
        content = _request_bytes(plan)
        if request_path.exists():
            if not request_path.is_file():
                raise GradleApplyError("Gradle 请求路径不是普通文件")
            if request_path.read_bytes() == content:
                return GradleApplyResult(request_path, None, False, True)
            raise GradleApplyConflict(f"workspace 已存在不同 Gradle 配置: {request_path}")

        descriptor = -1
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".gradle-config-request.", suffix=".tmp", dir=state_directory
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, request_path)
            temporary_path = None
            stdout_path = _workspace_child(root, ".spacetime/gradle-config.stdout.log")
            stderr_path = _workspace_child(root, ".spacetime/gradle-config.stderr.log")
            process_request = ProcessRequest(
                self._gradle_executable,
                (
                    "--init-script",
                    self._script_path.as_posix(),
                    f"-PspacetimeRequest={request_path.as_posix()}",
                    "tasks",
                ),
                root,
                stdout_path,
                stderr_path,
                timeout_seconds=self._timeout_seconds,
            )
            result = self._process_runner.run(process_request, cancellation)
            if result.outcome is not ProcessOutcome.COMPLETED or result.exit_code != 0:
                raise GradleApplyError(
                    f"Gradle 配置应用失败: outcome={result.outcome.value}, exit_code={result.exit_code}, "
                    f"diagnostic={result.diagnostic_message}"
                )
            return GradleApplyResult(request_path, result, True, False)
        except BaseException:
            if request_path.exists():
                try:
                    request_path.unlink()
                except OSError:
                    pass
            raise
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass


__all__ = [
    "GradleApplyCancelled",
    "GradleApplyConflict",
    "GradleApplyError",
    "GradleApplyResult",
    "GradleConfigurationApplier",
]
