"""分支计划的受控 SVN 写入适配器。

本模块把已经完成只读前置校验的 ``BranchPlan`` 转换为确定性的 ``svnmucc``
参数序列，并通过统一进程端口执行一次提交。它不解析凭据、不重试部分提交、
不接受任意 shell 文本；属性值只作为参数传入并在请求日志中按索引脱敏。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from branch.model import BranchResult, BranchStatus
from branch.planner import BranchPlan
from ports.process import (
    CancellationToken,
    ProcessOutcome,
    ProcessRequest,
    ProcessResult,
    ProcessRunner,
)


def _append_url(root: str, relative_path: str) -> str:
    """把已经校验的逻辑相对路径追加到 SVN URL。"""
    return f"{root.rstrip('/')}/{relative_path}"


def _validate_message(message: object) -> str:
    """校验提交消息，拒绝空消息和会污染日志的控制字符。"""
    if not isinstance(message, str):
        raise TypeError("message 必须是字符串")
    if not message.strip():
        raise ValueError("message 不得为空")
    if any(unicodedata.category(character).startswith("C") for character in message):
        raise ValueError("message 不得包含控制字符")
    return message


def _redacted_arguments(arguments: tuple[str, ...], indexes: frozenset[int]) -> tuple[str, ...]:
    """用属性值摘要替换秘密参数，生成安全的审计摘要输入。"""
    result = list(arguments)
    for index in indexes:
        digest = hashlib.sha256(arguments[index].encode("utf-8")).hexdigest()
        result[index] = f"<redacted:sha256:{digest}>"
    return tuple(result)


@dataclass(frozen=True, slots=True)
class BranchApplyResult:
    """保存一次分支写入的状态、进程摘要和安全命令摘要。

    参数：
        result: 从计划状态转移得到的终态 ``BranchResult``。
        process_result: 统一进程端口返回的执行摘要。
        command_digest: 不含属性明文的命令 SHA-256 摘要。

    返回：
        无；对象不可变且可直接交给上层审计记录。

    异常：
        字段类型不符合约束时抛出 ``TypeError``。

    约束与副作用：
        结果不保存完整命令参数或凭据；构造过程不执行外部操作。
    """

    result: BranchResult
    process_result: ProcessResult
    command_digest: str

    def __post_init__(self) -> None:
        """校验结果字段的公开类型。"""
        if not isinstance(self.result, BranchResult):
            raise TypeError("result 必须是 BranchResult")
        if not isinstance(self.process_result, ProcessResult):
            raise TypeError("process_result 必须是 ProcessResult")
        if not isinstance(self.command_digest, str) or not self.command_digest:
            raise TypeError("command_digest 必须是非空字符串")


class SvnBranchMutationApplier:
    """通过 ``svnmucc`` 一次性应用安全的分支计划。

    参数：
        executable: ``svnmucc`` 的绝对路径。
        process_runner: 统一外部进程执行端口。
        working_directory: 绝对工作目录。
        log_root: 绝对日志目录；按计划摘要生成输出文件。
        timeout_seconds: 单次提交的有限正超时。

    返回：
        ``apply`` 返回 ``BranchApplyResult``；进程非零退出、取消或超时均为失败终态。

    异常：
        构造参数非法或提交消息不安全时抛出 ``TypeError`` 或 ``ValueError``。
        进程端口本身的实现异常向上传播，不会伪造成功状态。

    约束与副作用：
        每个计划只调用一次 ``ProcessRunner``，不自动重试；实际 SVN 写入只发生在
        注入的执行器中。凭据不进入本适配器，必须由执行器的秘密 binding 机制提供。
    """

    def __init__(
        self,
        executable: Path,
        process_runner: ProcessRunner,
        working_directory: Path,
        log_root: Path,
        *,
        timeout_seconds: float = 600.0,
    ) -> None:
        """校验并保存一次提交所需的固定外部依赖。"""
        for value, field_name in (
            (executable, "executable"),
            (working_directory, "working_directory"),
            (log_root, "log_root"),
        ):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{field_name} 必须是绝对 Path")
        if not callable(getattr(process_runner, "run", None)):
            raise TypeError("process_runner 必须实现 run")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds 必须是数值")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是正数")
        self._executable = executable
        self._process_runner = process_runner
        self._working_directory = working_directory
        self._log_root = log_root
        self._timeout_seconds = float(timeout_seconds)

    def apply(
        self,
        plan: BranchPlan,
        *,
        message: str,
        cancellation: CancellationToken | None = None,
    ) -> BranchApplyResult:
        """将计划转换为单次 ``svnmucc`` 提交并返回终态。

        参数：
            plan: 已通过只读校验的不可变分支计划。
            message: 非空且不含控制字符的 SVN 提交消息。
            cancellation: 可选协作式取消令牌，由进程执行器解释。

        返回：
            成功时为 ``APPLIED``，否则为 ``FAILED`` 的 ``BranchApplyResult``。

        异常：
            计划、消息或进程端口类型非法时抛出 ``TypeError`` 或 ``ValueError``。

        约束与副作用：
            只构造一条命令并调用一次执行器；不会在失败后重试或继续第二次提交。
        """
        if not isinstance(plan, BranchPlan):
            raise TypeError("plan 必须是 BranchPlan")
        validated_message = _validate_message(message)
        arguments, redacted_indexes = self._build_arguments(plan, validated_message)
        command_digest = self._command_digest(plan, arguments, redacted_indexes)
        self._log_root.mkdir(parents=True, exist_ok=True)
        request = ProcessRequest(
            executable=self._executable,
            arguments=arguments,
            working_directory=self._working_directory,
            stdout_path=self._log_root / f"{command_digest}.stdout.log",
            stderr_path=self._log_root / f"{command_digest}.stderr.log",
            timeout_seconds=self._timeout_seconds,
            redacted_argument_indexes=redacted_indexes,
        )
        process_result = self._process_runner.run(request, cancellation)
        planned = BranchResult(
            mutation_id=plan.plan_id,
            status=BranchStatus.PLANNED,
            copies=plan.copies,
            property_changes=plan.property_changes,
        )
        if process_result.outcome is ProcessOutcome.COMPLETED and process_result.exit_code == 0:
            final_result = planned.transition(BranchStatus.APPLIED)
        else:
            final_result = planned.transition(
                BranchStatus.FAILED,
                self._failure_message(process_result),
            )
        return BranchApplyResult(final_result, process_result, command_digest)

    def _build_arguments(
        self,
        plan: BranchPlan,
        message: str,
    ) -> tuple[tuple[str, ...], frozenset[int]]:
        """按固定顺序构造 copy、属性变更和提交消息参数。"""
        arguments: list[str] = [
            "--non-interactive",
            "--no-auth-cache",
            "-r",
            str(plan.expected_repository_revision),
        ]
        redacted_indexes: set[int] = set()
        for copy in plan.copies:
            arguments.extend(
                (
                    "cp",
                    str(copy.source.revision),
                    _append_url(copy.source.url, copy.source_path),
                    _append_url(copy.target.url, copy.target_path),
                )
            )
        for change in sorted(
            plan.property_changes,
            key=lambda item: (item.path.encode("utf-8"), item.property_name.encode("utf-8")),
        ):
            target_url = _append_url(plan.target_root, change.path)
            if change.new_value is None:
                arguments.extend(("propdel", change.property_name, target_url))
                continue
            arguments.extend(("propset", change.property_name, change.new_value, target_url))
            redacted_indexes.add(len(arguments) - 2)
        arguments.extend(("-m", message))
        return tuple(arguments), frozenset(redacted_indexes)

    def _command_digest(
        self,
        plan: BranchPlan,
        arguments: tuple[str, ...],
        redacted_indexes: frozenset[int],
    ) -> str:
        """计算不暴露属性明文的确定性命令摘要。"""
        payload = {
            "plan_id": plan.plan_id,
            "arguments": _redacted_arguments(arguments, redacted_indexes),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _failure_message(self, process_result: ProcessResult) -> str:
        """把进程结果转换为不含秘密的稳定分支诊断。"""
        detail = process_result.diagnostic_message.strip()
        if process_result.outcome is ProcessOutcome.COMPLETED:
            prefix = f"svnmucc exit code {process_result.exit_code}"
        else:
            prefix = f"svnmucc outcome {process_result.outcome.value}"
        return f"{prefix}: {detail}" if detail else prefix
