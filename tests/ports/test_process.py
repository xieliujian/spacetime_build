"""验证进程端口值对象、取消并发和秘密元数据安全契约。"""

from __future__ import annotations

import math
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from observability import ErrorCode
from ports import (
    CancellationToken,
    ProcessOutcome,
    ProcessRequest,
    ProcessResult,
    ProcessTextSink,
    SecretBindingTarget,
    SecretProcessBinding,
)


def _request(**overrides: object) -> ProcessRequest:
    """创建可按字段覆盖的合法请求。"""
    values: dict[str, object] = {
        "executable": Path("C:/tools/tool.exe"),
        "arguments": ("a", "b"),
        "working_directory": Path("C:/work"),
        "stdout_path": Path("C:/logs/out.log"),
        "stderr_path": Path("C:/logs/err.log"),
    }
    values.update(overrides)
    return ProcessRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executable", "C:/tool.exe"),
        ("executable", Path("relative.exe")),
        ("arguments", ["a"]),
        ("arguments", ("a\0b",)),
        ("working_directory", Path("relative")),
        ("environment", (("", "v"),)),
        ("environment", (("A=B", "v"),)),
        ("environment", (("A", "x\0y"),)),
        ("environment", (("A", "1"), ("A", "2"))),
        ("timeout_seconds", 0.0),
        ("timeout_seconds", math.inf),
        ("redacted_argument_indexes", frozenset({2})),
    ],
)
def test_process_request_rejects_invalid_runtime_values(field: str, value: object) -> None:
    """验证请求在运行时拒绝错误类型、路径、NUL、重复键和数值边界。"""
    with pytest.raises((TypeError, ValueError)):
        _request(**{field: value})


def test_process_request_normalizes_environment_and_protects_secret_lease_repr() -> None:
    """验证环境按 UTF-8 排序，秘密租约不进入 repr 或值比较。"""

    class Lease:
        """仅用于确认租约身份不影响请求值语义。"""

        def resolve(self, binding_id: str) -> str:
            """返回测试秘密。"""
            return binding_id

        def close(self) -> None:
            """关闭测试租约。"""

    first = _request(environment=(("中", "2"), ("A", "1")), secret_lease=Lease())
    second = _request(environment=(("A", "1"), ("中", "2")), secret_lease=Lease())
    assert first.environment == (("A", "1"), ("中", "2"))
    assert first == second
    assert "Lease" not in repr(first)


@pytest.mark.parametrize(
    ("target", "slot", "valid"),
    [
        (SecretBindingTarget.ARGUMENT, "0", True),
        (SecretBindingTarget.ARGUMENT, "-1", False),
        (SecretBindingTarget.ENVIRONMENT, "TOKEN", True),
        (SecretBindingTarget.ENVIRONMENT, "A=B", False),
        (SecretBindingTarget.STDIN, "stdin", True),
        (SecretBindingTarget.STDIN, "other", False),
        (SecretBindingTarget.TEMP_FILE, "KEY_FILE", True),
    ],
)
def test_secret_binding_target_slot_contract(
    target: SecretBindingTarget,
    slot: str,
    valid: bool,
) -> None:
    """验证四类秘密目标的槽位约束且 repr 不含秘密值。"""
    if valid:
        binding = SecretProcessBinding("opaque-id", target, slot)
        assert "secret-value" not in repr(binding)
    else:
        with pytest.raises(ValueError):
            SecretProcessBinding("opaque-id", target, slot)


def test_request_rejects_duplicate_binding_id_and_target_slot() -> None:
    """验证秘密 binding ID 与目标槽位分别保持唯一。"""
    first = SecretProcessBinding("one", SecretBindingTarget.ENVIRONMENT, "TOKEN")
    with pytest.raises(ValueError, match="binding_id"):
        _request(
            secret_bindings=(first, SecretProcessBinding("one", SecretBindingTarget.STDIN, "stdin"))
        )
    with pytest.raises(ValueError, match="目标槽位"):
        _request(
            secret_bindings=(
                first,
                SecretProcessBinding("two", SecretBindingTarget.ENVIRONMENT, "TOKEN"),
            )
        )


def test_cancellation_token_is_thread_safe_and_waits_for_real_event() -> None:
    """验证并发 cancel 只有一次成功，wait 由实际事件唤醒。"""
    token = CancellationToken()
    barrier = threading.Barrier(9)
    results: list[bool] = []

    def cancel() -> None:
        """等待统一起点后提交取消。"""
        barrier.wait()
        results.append(token.cancel())

    threads = [threading.Thread(target=cancel) for _ in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait()
    assert token.wait(2.0)
    for thread in threads:
        thread.join(2.0)
    assert results.count(True) == 1
    assert results.count(False) == 7
    assert token.is_cancelled
    assert token.cancel() is False


def test_process_result_enforces_outcome_mapping_bounds_and_redaction() -> None:
    """验证结果映射、数值与 tail 边界，并对失败诊断脱敏。"""
    common = dict(
        exit_code=None,
        duration_seconds=0.0,
        stdout_path=Path("out"),
        stderr_path=Path("err"),
        stdout_bytes=0,
        stderr_bytes=0,
        diagnostic_message="token=top-secret",
    )
    result = ProcessResult(
        outcome=ProcessOutcome.TIMED_OUT,
        error_code=ErrorCode.PROCESS_TIMEOUT,
        **common,  # pyright: ignore[reportArgumentType]
    )
    assert "top-secret" not in result.diagnostic_message
    with pytest.raises(ValueError):
        ProcessResult(
            outcome=ProcessOutcome.CANCELLED,
            error_code=ErrorCode.INTERNAL_ERROR,
            **common,  # pyright: ignore[reportArgumentType]
        )
    with pytest.raises(ValueError):
        ProcessResult(
            outcome=ProcessOutcome.OUTPUT_FAILED,
            error_code=ErrorCode.INTERNAL_ERROR,
            stdout_tail="x" * 65_537,
            **common,  # pyright: ignore[reportArgumentType]
        )
    with pytest.raises(FrozenInstanceError):
        result.stdout_tail = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("binding_id", "target", "slot"),
    [
        (None, SecretBindingTarget.STDIN, "stdin"),
        ("", SecretBindingTarget.STDIN, "stdin"),
        ("bad\n", SecretBindingTarget.STDIN, "stdin"),
        ("id", "stdin", "stdin"),
        ("id", SecretBindingTarget.STDIN, ""),
        ("id", SecretBindingTarget.ENVIRONMENT, "BAD\nKEY"),
        ("id", SecretBindingTarget.TEMP_FILE, "A=B"),
    ],
)
def test_secret_binding_rejects_invalid_runtime_metadata(
    binding_id: object,
    target: object,
    slot: object,
) -> None:
    """验证秘密绑定拒绝空值、控制字符、错误枚举和非法环境槽位。"""
    with pytest.raises((TypeError, ValueError)):
        SecretProcessBinding(binding_id, target, slot)  # type: ignore[arg-type]


def test_cancellation_wait_validates_boundaries_and_none_observes_event() -> None:
    """验证 wait 拒绝非法超时，且 None 在真实已设置事件上立即成功。"""
    token = CancellationToken()
    for invalid in ("1", True, -0.1, math.inf):
        with pytest.raises((TypeError, ValueError)):
            token.wait(invalid)  # type: ignore[arg-type]
    token.cancel()
    assert token.wait(None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stdout_path", Path("C:/logs/SAME.log")),
        ("arguments", (1,)),
        ("environment", []),
        ("environment", (("A",),)),
        ("environment", (("A", "1", "2"),)),
        ("environment", (("A", 1),)),
        ("timeout_seconds", "1"),
        ("redacted_argument_indexes", {0}),
        ("redacted_argument_indexes", frozenset({True})),
        ("secret_bindings", []),
        ("secret_bindings", ("binding",)),
    ],
)
def test_process_request_rejects_additional_container_and_member_types(
    field: str,
    value: object,
) -> None:
    """验证请求拒绝容器、成员、索引和秘密绑定的运行时类型伪装。"""
    overrides = {field: value}
    if field == "stdout_path":
        overrides["stderr_path"] = Path("C:/logs/same.LOG")
    with pytest.raises((TypeError, ValueError)):
        _request(**overrides)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "completed"),
        ("exit_code", True),
        ("duration_seconds", "0"),
        ("duration_seconds", -1.0),
        ("duration_seconds", math.nan),
        ("stdout_path", "out"),
        ("stderr_path", "err"),
        ("stdout_bytes", True),
        ("stderr_bytes", -1),
        ("stdout_tail", 1),
        ("stderr_tail", "x" * 65_537),
        ("error_code", "INTERNAL_ERROR"),
        ("diagnostic_message", 1),
    ],
    ids=(
        "outcome-type",
        "exit-code-bool",
        "duration-type",
        "duration-negative",
        "duration-nan",
        "stdout-path-type",
        "stderr-path-type",
        "stdout-bytes-bool",
        "stderr-bytes-negative",
        "stdout-tail-type",
        "stderr-tail-too-long",
        "error-code-type",
        "diagnostic-type",
    ),
)
def test_process_result_rejects_runtime_types_and_numeric_bounds(
    field: str,
    value: object,
) -> None:
    """验证结果拒绝错误运行时类型、非有限耗时、负字节和过长 tail。"""
    values: dict[str, object] = {
        "outcome": ProcessOutcome.OUTPUT_FAILED,
        "exit_code": None,
        "duration_seconds": 0.0,
        "stdout_path": Path("out"),
        "stderr_path": Path("err"),
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "error_code": ErrorCode.INTERNAL_ERROR,
        "diagnostic_message": "失败",
    }
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        ProcessResult(**values)  # type: ignore[arg-type]


def test_process_result_enforces_completed_and_failure_diagnostics() -> None:
    """验证完成结果必须有退出码且无错误码，所有失败必须有非空诊断。"""
    base = dict(
        duration_seconds=0.0,
        stdout_path=Path("out"),
        stderr_path=Path("err"),
        stdout_bytes=0,
        stderr_bytes=0,
    )
    with pytest.raises(ValueError, match="exit_code"):
        ProcessResult(outcome=ProcessOutcome.COMPLETED, exit_code=None, **base)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="error_code"):
        ProcessResult(
            outcome=ProcessOutcome.COMPLETED,
            exit_code=0,
            error_code=ErrorCode.INTERNAL_ERROR,
            **base,  # pyright: ignore[reportArgumentType]
        )


def test_process_text_sink_protocol_and_request_ownership_contract() -> None:
    """验证 sink 结构协议、成对注入、路径匹配、身份隔离及 repr/比较安全。"""

    class Sink:
        """完整实现进程文本 sink 的内存 fake。"""

        def __init__(self, path: Path) -> None:
            """保存精确路径与关闭计数。"""
            self.path = path
            self.byte_count = 0
            self.closed = 0

        def write_text(self, text: str) -> str:
            """原样返回并累计 UTF-8 字节。"""
            self.byte_count += len(text.encode("utf-8"))
            return text

        def close(self) -> str:
            """记录关闭并返回空 finalize 文本。"""
            self.closed += 1
            return ""

    stdout = Sink(Path("C:/logs/out.log"))
    stderr = Sink(Path("C:/logs/err.log"))
    assert isinstance(stdout, ProcessTextSink)
    request = _request(stdout_sink=stdout, stderr_sink=stderr)
    assert stdout.closed == stderr.closed == 0
    assert "Sink" not in repr(request)
    assert request == _request(stdout_sink=Sink(stdout.path), stderr_sink=Sink(stderr.path))
    with pytest.raises(ValueError, match="同时提供"):
        _request(stdout_sink=stdout)
    with pytest.raises(ValueError, match="同一对象"):
        _request(stdout_sink=stdout, stderr_sink=stdout)
    with pytest.raises(ValueError, match="stdout_sink.path"):
        _request(stdout_sink=Sink(Path("C:/wrong")), stderr_sink=stderr)


def test_environment_keys_are_casefold_unique_and_still_stably_sorted() -> None:
    """验证 Path/PATH 在所有平台均视为重复，非重复键仍按原文本 UTF-8 排序。"""
    with pytest.raises(ValueError, match="重复键"):
        _request(environment=(("Path", "one"), ("PATH", "two")))
    assert _request(environment=(("b", "2"), ("A", "1"))).environment == (
        ("A", "1"),
        ("b", "2"),
    )


def test_result_tail_limit_counts_utf8_bytes_not_characters() -> None:
    """验证中文 tail 按 UTF-8 字节限制，边界合法且超一字立即拒绝。"""
    base = dict(
        outcome=ProcessOutcome.COMPLETED,
        exit_code=0,
        duration_seconds=0.0,
        stdout_path=Path("out"),
        stderr_path=Path("err"),
        stdout_bytes=0,
        stderr_bytes=0,
    )
    allowed = "中" * 21_845 + "a"
    assert len(allowed.encode("utf-8")) == 65_536
    assert ProcessResult(stdout_tail=allowed, **base).stdout_tail == allowed  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="UTF-8"):
        ProcessResult(stdout_tail=allowed + "a", **base)  # type: ignore[arg-type]
