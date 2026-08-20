"""通过固定 Ruby 工具应用 iOS Xcode 工程变换计划。

本模块是 Python 与 macOS ``xcodeproj`` 工具之间的受控适配边界。它只接受已经由
``xcode_project`` 规划器生成的 ``XcodeProjectPlan``，在 workspace 的固定状态目录中
原子写入 ``XcodeProjectToolRequest``，再通过注入的 ``ProcessRunner`` 调用固定 Ruby
入口。请求不包含脚本、命令或任意路径；工程文本的解析和变换完全留在 Ruby 工具侧。

应用器在 Windows 上只可使用 fake ``ProcessRunner`` 测试，不能因此宣称真实 Xcode
能力已可用。请求文件与失败回滚均限制在 workspace 内，重复的相同请求不重新启动
Ruby，不同请求也不会覆盖已有状态。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.errors import BuildError
from package.platforms.ios.xcode_project import XcodeProjectPlan
from ports.process import (
    CancellationToken,
    ProcessOutcome,
    ProcessRequest,
    ProcessResult,
    ProcessRunner,
)


class XcodeProjectApplyError(BuildError):
    """表示 Xcode 工程计划不能安全应用。

    职责：
        统一描述 workspace 校验、固定工具执行和请求回滚边界中的应用失败。

    参数与返回：
        接受继承自 ``BuildError`` 的标准错误消息，不返回业务值。

    异常与约束：
        该异常本身是应用层失败信号；调用方不得把未验证的工程状态视为成功。

    副作用：
        异常对象只保存安全摘要，不负责清理；清理由应用器或固定 Ruby 工具完成。
    """


class XcodeProjectApplyConflict(XcodeProjectApplyError):
    """表示 workspace 已保存不同的 Xcode 工程请求。

    职责：
        区分内容冲突和普通工具失败，阻止新计划覆盖已有请求状态。

    参数与返回：
        接受继承异常的标准消息，不返回业务值。

    异常与约束：
        发生冲突时原请求文件必须保持不变，调用方应显式选择新的 workspace 或计划。

    副作用：
        异常本身不执行 I/O；冲突检测由应用器在启动 Ruby 前完成。
    """


class XcodeProjectApplyCancelled(XcodeProjectApplyError):
    """表示 Xcode 工程应用在启动前或执行期间被取消。

    职责：
        保留协作取消与工具普通失败的区别，供上层恢复策略判断。

    参数与返回：
        接受继承异常的标准消息，不返回业务值。

    异常与约束：
        应用器在取消路径删除本次新请求文件；已存在的不同请求不会被删除或覆盖。

    副作用：
        异常本身不执行 I/O；取消清理由应用器和受控 ``ProcessRunner`` 协作完成。
    """


@dataclass(frozen=True, slots=True)
class XcodeProjectApplyResult:
    """描述一次 Xcode 工程请求应用的不可变回执。

    参数：
        request_path: workspace 内固定请求 JSON 的路径。
        process_result: Ruby 进程结果；幂等命中时为 ``None``。
        changed: 本次是否写入请求并执行了 Ruby 工具。
        idempotent: 是否发现了内容完全相同的已完成请求。

    返回：
        一个只读回执对象；对象不保存工程文本或秘密材料。

    异常：
        dataclass 构造本身不产生业务异常；字段由应用器在返回前保证类型正确。

    约束与副作用：
        回执只包含路径和进程摘要，不代表当前 Windows 环境已执行真实 Xcode。
    """

    request_path: Path
    process_result: ProcessResult | None
    changed: bool
    idempotent: bool


def _workspace_child(root: Path, relative_path: str) -> Path:
    """解析 workspace 子路径并拒绝符号链接或 ``..`` 导致的路径逃逸。

    参数：
        root: 已解析的绝对 workspace 根目录。
        relative_path: 仅允许使用正斜杠的 workspace 相对路径。

    返回：
        解析后的 workspace 内绝对路径。

    异常：
        路径逃逸 workspace 时抛出 ``XcodeProjectApplyError``。

    约束与副作用：
        只解析路径，不创建目录、不读取文件内容；现有符号链接会按真实目标检查。
    """
    if not isinstance(relative_path, str) or not relative_path:
        raise XcodeProjectApplyError("Xcode workspace 相对路径必须是非空字符串")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise XcodeProjectApplyError(f"Xcode 工程路径逃逸 workspace: {relative_path}") from exc
    return candidate


def _request_bytes(plan: XcodeProjectPlan) -> bytes:
    """把结构化工程计划编码成确定性请求字节。

    参数：
        plan: 已由 ``XcodeProjectPlanner`` 规范化的工程计划。

    返回：
        带结尾换行的 UTF-8 JSON bytes。

    异常：
        ``plan`` 类型错误或计划无法转换时抛出 ``XcodeProjectApplyError``。

    约束与副作用：
        只调用计划的白名单序列化，不执行 Ruby、不读取工程、不产生文件 I/O。
    """
    if not isinstance(plan, XcodeProjectPlan):
        raise TypeError("plan 必须是 XcodeProjectPlan")
    try:
        return plan.to_tool_request().to_json()
    except (TypeError, ValueError) as exc:
        raise XcodeProjectApplyError("Xcode 工程计划无法序列化") from exc


def _atomic_write(path: Path, content: bytes) -> None:
    """在固定父目录中原子写入请求文件并持久化临时文件。

    参数：
        path: workspace 内目标文件路径。
        content: 已确定性编码的请求内容。

    返回：
        ``None``；成功后目标文件替换完成。

    异常：
        文件系统创建、写入、同步或替换失败时沿用 ``OSError``。

    约束与副作用：
        临时文件只在目标父目录内创建；调用方负责在更高层失败时删除目标文件。
    """
    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".xcode-project-request.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
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


class XcodeProjectPlanApplier:
    """通过固定 Ruby ``xcodeproj`` 入口应用 Xcode 工程计划。

    参数：
        process_runner: 注入的外部进程执行器；生产环境应绑定受控实现，测试使用 fake。
        ruby_script_path: 仓库内固定 ``apply_project.rb`` 的绝对路径。
        ruby_executable: Ruby 可执行文件绝对路径，必须由调用方显式提供。
        timeout_seconds: Ruby 工具允许的有限正超时时间。

    返回：
        构造一个尚未启动进程的应用器。

    异常：
        固定脚本路径非绝对路径、超时非法或 Ruby 路径非绝对路径时抛出 ``ValueError``。

    约束与副作用：
        构造不读取脚本、不访问 workspace、不执行 Ruby；请求参数始终由本模块固定。
    """

    def __init__(
        self,
        process_runner: ProcessRunner,
        ruby_script_path: Path,
        *,
        ruby_executable: Path | None = None,
        timeout_seconds: float = 600.0,
    ) -> None:
        """绑定进程端口和固定工具路径，不在构造期间产生外部副作用。"""
        if not isinstance(ruby_script_path, Path) or not ruby_script_path.is_absolute():
            raise ValueError("ruby_script_path 必须是绝对 Path")
        if ruby_executable is None:
            raise ValueError("ruby_executable 必须显式提供绝对 Path")
        executable = ruby_executable
        if not isinstance(executable, Path) or not executable.is_absolute():
            raise ValueError("ruby_executable 必须是绝对 Path")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds 必须是 int 或 float")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是正数")
        self._process_runner = process_runner
        self._ruby_script_path = ruby_script_path
        self._ruby_executable = executable
        self._timeout_seconds = float(timeout_seconds)

    def apply(
        self,
        plan: XcodeProjectPlan,
        workspace: Path,
        *,
        cancellation: CancellationToken | None = None,
    ) -> XcodeProjectApplyResult:
        """原子保存工程请求并通过固定 Ruby 工具应用。

        参数：
            plan: 已规范化的 ``XcodeProjectPlan``，不接受原始脚本或任意 JSON。
            workspace: 已存在的绝对 Xcode workspace 根目录。
            cancellation: 可选协作取消令牌，会传递给 ``ProcessRunner``。

        返回：
            新请求返回 ``changed=True``；同字节请求返回 ``idempotent=True`` 且不重启 Ruby。

        异常：
            workspace、固定脚本、工程目录或计划非法时抛 ``XcodeProjectApplyError``；
            请求冲突抛 ``XcodeProjectApplyConflict``；取消抛 ``XcodeProjectApplyCancelled``；
            Ruby 非零、超时或启动失败抛 ``XcodeProjectApplyError``。

        约束与副作用：
            只写 ``workspace/.spacetime/xcode-project-request.json`` 和诊断日志；失败时
            删除本次请求文件。工程本身的保存回滚由固定 Ruby 工具的事务边界负责。
        """
        if not isinstance(plan, XcodeProjectPlan):
            raise TypeError("plan 必须是 XcodeProjectPlan")
        if not isinstance(workspace, Path) or not workspace.is_absolute() or not workspace.is_dir():
            raise XcodeProjectApplyError("workspace 必须是已存在的绝对目录")
        if not self._ruby_script_path.is_file():
            raise XcodeProjectApplyError(f"固定 Ruby 工具不存在: {self._ruby_script_path}")
        if cancellation is not None and cancellation.is_cancelled:
            raise XcodeProjectApplyCancelled("Xcode 工程应用已取消")

        root = workspace.resolve()
        try:
            project_path = _workspace_child(root, plan.project_path)
        except (AttributeError, TypeError) as exc:
            raise XcodeProjectApplyError("Xcode 工程计划路径无效") from exc
        if not project_path.name.endswith(".xcodeproj") or not project_path.is_dir():
            raise XcodeProjectApplyError(f"Xcode 工程目录不存在: {plan.project_path}")

        content = _request_bytes(plan)
        state_directory = _workspace_child(root, ".spacetime")
        if state_directory.exists() and not state_directory.is_dir():
            raise XcodeProjectApplyError("Xcode 状态路径不是目录")
        state_directory.mkdir(parents=False, exist_ok=True)
        request_path = _workspace_child(root, ".spacetime/xcode-project-request.json")
        if request_path.exists():
            if not request_path.is_file():
                raise XcodeProjectApplyError("Xcode 请求路径不是普通文件")
            try:
                existing = request_path.read_bytes()
            except OSError as exc:
                raise XcodeProjectApplyError("无法读取已有 Xcode 请求") from exc
            if existing == content:
                return XcodeProjectApplyResult(request_path, None, False, True)
            raise XcodeProjectApplyConflict(f"workspace 已存在不同 Xcode 工程请求: {request_path}")

        try:
            _atomic_write(request_path, content)
            if cancellation is not None and cancellation.is_cancelled:
                raise XcodeProjectApplyCancelled("Xcode 工程应用已取消")
            stdout_path = _workspace_child(root, ".spacetime/xcode-project.stdout.log")
            stderr_path = _workspace_child(root, ".spacetime/xcode-project.stderr.log")
            process_request = ProcessRequest(
                self._ruby_executable,
                (self._ruby_script_path.as_posix(), request_path.as_posix()),
                root,
                stdout_path,
                stderr_path,
                timeout_seconds=self._timeout_seconds,
            )
            result = self._process_runner.run(process_request, cancellation)
            if result.outcome is ProcessOutcome.CANCELLED:
                raise XcodeProjectApplyCancelled("Xcode 工程应用已取消")
            if result.outcome is not ProcessOutcome.COMPLETED or result.exit_code != 0:
                raise XcodeProjectApplyError(
                    "Xcode 工程应用失败: "
                    f"outcome={result.outcome.value}, exit_code={result.exit_code}, "
                    f"diagnostic={result.diagnostic_message}"
                )
            return XcodeProjectApplyResult(request_path, result, True, False)
        except BaseException:
            if request_path.exists():
                try:
                    request_path.unlink()
                except OSError:
                    pass
            raise


__all__ = [
    "XcodeProjectApplyCancelled",
    "XcodeProjectApplyConflict",
    "XcodeProjectApplyError",
    "XcodeProjectApplyResult",
    "XcodeProjectPlanApplier",
]
