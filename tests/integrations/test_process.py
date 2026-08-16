"""使用真实 fixture 验证本地进程执行、安全输出与取消终止。"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Callable, cast

import pytest

import integrations.process as process_module
from core.errors import ConfigurationError
from integrations import LocalProcessRunner
from observability import (
    ErrorCode,
    ExternalStreamWriter,
    LogContext,
    LogEvent,
    LogLevel,
    build_log_paths,
    open_run_handlers,
)
from ports import (
    CancellationToken,
    ProcessOutcome,
    ProcessRequest,
    ProcessResult,
    SecretBindingTarget,
    SecretProcessBinding,
)

FIXTURE = Path(__file__).parent / "fixtures" / "process_fixture.py"


class _TreeRecord:
    """保存一次真实 fixture 进程树的 nonce 与三层 PID。"""

    def __init__(self, nonce: str, root_pid: int, child_pid: int, grandchild_pid: int) -> None:
        """保存已校验的启动身份和正 PID。"""
        self.nonce = nonce
        self.root_pid = root_pid
        self.child_pid = child_pid
        self.grandchild_pid = grandchild_pid

    @property
    def pids(self) -> tuple[int, int, int]:
        """按根、子、孙顺序返回本次启动的全部 PID。"""
        return self.root_pid, self.child_pid, self.grandchild_pid


class _TrackedProcess:
    """持有真实进程身份；Windows 以打开句柄避免短测试窗口中的 PID 复用。"""

    def __init__(self, pid: int) -> None:
        """打开 Windows 查询句柄，POSIX 则保存由 nonce 记录关联的 PID。"""
        self.pid = pid
        self._handle: int | None = None
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            open_process.restype = ctypes.c_void_p
            handle = open_process(0x00100000 | 0x1000, 0, pid)
            if handle is None:
                raise OSError(ctypes.get_last_error(), f"无法打开测试进程 PID {pid}")
            self._handle = int(handle)

    def is_alive(self) -> bool:
        """查询所持身份是否仍为活动进程，不把同号新 PID 误认为原进程。"""
        if os.name == "nt":
            if self._handle is None:
                return False
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_exit_code = kernel32.GetExitCodeProcess
            get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            get_exit_code.restype = ctypes.c_int
            exit_code = ctypes.c_ulong()
            if not get_exit_code(ctypes.c_void_p(self._handle), ctypes.byref(exit_code)):
                raise OSError(ctypes.get_last_error(), f"无法查询测试进程 PID {self.pid}")
            return exit_code.value == 259
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def close(self) -> None:
        """幂等关闭 Windows 身份句柄；POSIX 没有额外资源。"""
        if self._handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = self._handle
        self._handle = None
        if not close_handle(ctypes.c_void_p(handle)):
            raise OSError(ctypes.get_last_error(), f"无法关闭测试进程 PID {self.pid} 的句柄")


def _wait_for_condition(
    condition: Callable[[], bool],
    *,
    timeout_seconds: float,
    description: str,
) -> None:
    """用 Event 条件轮询等待断言成立，超时携带具体目标说明。"""
    deadline = time.monotonic() + timeout_seconds
    changed = threading.Event()
    while not condition():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"等待{description}超时")
        changed.wait(min(0.02, remaining))


def _load_tree_record(path: Path, nonce: str) -> _TreeRecord | None:
    """读取原子发布的 PID 记录，只接受当前测试 nonce 和三个正整数 PID。"""
    try:
        payload = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    mapping = cast(dict[object, object], payload)
    if mapping.get("nonce") != nonce:
        return None
    pids = tuple(mapping.get(key) for key in ("root_pid", "child_pid", "grandchild_pid"))
    if any(not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 for pid in pids):
        return None
    root_pid, child_pid, grandchild_pid = cast(tuple[int, int, int], pids)
    return _TreeRecord(nonce, root_pid, child_pid, grandchild_pid)


def _wait_for_tree_record(path: Path, nonce: str) -> _TreeRecord:
    """有界等待 fixture 发布与当前 nonce 对应的完整三层 PID 记录。"""
    holder: list[_TreeRecord] = []

    def loaded() -> bool:
        """每轮重新读取原子记录，成功后保存唯一结果。"""
        record = _load_tree_record(path, nonce)
        if record is None:
            return False
        holder.append(record)
        return True

    _wait_for_condition(loaded, timeout_seconds=5.0, description="真实进程树 PID 记录就绪")
    return holder[0]


def _force_cleanup_tree(record: _TreeRecord) -> None:
    """测试失败时按固定系统 API 尽力清理精确记录的进程树与后代。"""
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot")
        if not system_root:
            return
        taskkill = Path(system_root) / "System32" / "taskkill.exe"
        for pid in record.pids:
            subprocess.run(
                [str(taskkill), "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
                timeout=5.0,
            )
        return
    try:
        os.killpg(record.root_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class _EventLogger:
    """记录真实事件并在进程启动事件到达时通知测试线程。"""

    def __init__(self, *, fail_on_emit: int | None = None) -> None:
        """保存可选失败序号并创建事件容器。"""
        self.events: list[LogEvent] = []
        self.started = threading.Event()
        self.fail_on_emit = fail_on_emit

    def emit(self, event: LogEvent) -> None:
        """记录事件，按配置注入 logger 边界失败。"""
        self.events.append(event)
        if "已启动" in event.message:
            self.started.set()
        if self.fail_on_emit == len(self.events):
            raise OSError("token=logger-secret")

    def close(self) -> None:
        """测试 logger 不拥有外部资源。"""


def _request(tmp_path: Path, mode: str, *arguments: str, timeout: float = 5.0) -> ProcessRequest:
    """创建执行当前 Python fixture 的合法请求。"""
    environment = [("ONLY_KEY", "visible")]
    if "SystemRoot" in os.environ:
        # Windows Python 初始化随机源需要系统根目录；仍由请求显式声明，不隐式继承。
        environment.append(("SystemRoot", os.environ["SystemRoot"]))
    return ProcessRequest(
        executable=Path(sys.executable),
        arguments=(str(FIXTURE), mode, *arguments),
        working_directory=tmp_path,
        environment=tuple(environment),
        timeout_seconds=timeout,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
    )


def _runner(logger: _EventLogger | None = None) -> LocalProcessRunner:
    """创建快速轮询且使用真实日志上下文的 runner。"""
    context = LogContext("build", "process", "run") if logger is not None else None
    return LocalProcessRunner(logger=logger, log_context=context, termination_grace_seconds=1.0)


def test_runner_preserves_argv_cwd_explicit_env_and_redacts_streams(tmp_path: Path) -> None:
    """验证 argv 不经 shell、cwd/env 显式传递且双流诊断脱敏。"""
    request = _request(tmp_path, "inspect", "a b", "&literal")
    result = _runner().run(request)
    payload = json.loads(request.stdout_path.read_text(encoding="utf-8"))
    assert result.outcome is ProcessOutcome.COMPLETED
    assert result.exit_code == 0
    assert payload == {"argv": ["a b", "&literal"], "cwd": str(tmp_path), "env": "visible"}
    assert "stderr-secret" not in request.stderr_path.read_text(encoding="utf-8")
    assert result.stdout_path == request.stdout_path
    assert result.stderr_path == request.stderr_path


def test_runner_accepts_open_run_handler_sinks_and_takes_close_ownership(tmp_path: Path) -> None:
    """验证真实 handlers writer 可直接交给 runner，完成后由 runner 幂等关闭。"""
    context = LogContext("build", "process", "sink-run")
    paths = build_log_paths(tmp_path / "logs", context, datetime(2026, 1, 2, tzinfo=timezone.utc))
    logger, stdout_writer, stderr_writer, unity_writer = open_run_handlers(
        paths,
        StringIO(),
        LogLevel.INFO,
        LogLevel.DEBUG,
    )
    request = replace(
        _request(tmp_path, "inspect", "with sink"),
        stdout_path=paths.stdout,
        stderr_path=paths.stderr,
        stdout_sink=stdout_writer,
        stderr_sink=stderr_writer,
    )

    try:
        result = LocalProcessRunner(logger=logger, log_context=context).run(request)
    finally:
        logger.close()
        # runner 已接管两个进程 writer；调用方防御性重复关闭必须保持幂等。
        assert stdout_writer.close() == ""
        assert stderr_writer.close() == ""
        unity_writer.close()

    assert result.outcome is ProcessOutcome.COMPLETED
    assert json.loads(paths.stdout.read_text(encoding="utf-8"))["argv"] == ["with sink"]
    assert paths.stderr.exists()


def test_runner_tail_uses_the_same_streaming_redaction_output_as_file(tmp_path: Path) -> None:
    """验证跨 reader chunk 的秘密不会从另一条 tail 脱敏路径泄漏。"""
    context = LogContext("build", "process", "split-secret")
    paths = build_log_paths(tmp_path / "logs", context, datetime(2026, 1, 2, tzinfo=timezone.utc))
    logger, stdout_writer, stderr_writer, unity_writer = open_run_handlers(
        paths,
        StringIO(),
        LogLevel.INFO,
        LogLevel.DEBUG,
    )
    request = replace(
        _request(tmp_path, "split_secret"),
        stdout_path=paths.stdout,
        stderr_path=paths.stderr,
        stdout_sink=stdout_writer,
        stderr_sink=stderr_writer,
    )

    try:
        result = LocalProcessRunner(logger=logger, log_context=context).run(request)
    finally:
        logger.close()
        stdout_writer.close()
        stderr_writer.close()
        unity_writer.close()

    file_tail = paths.stdout.read_text(encoding="utf-8")[-65_536:]
    assert result.outcome is ProcessOutcome.COMPLETED
    assert result.stdout_tail == file_tail
    assert "LEAKED_SECRET" not in result.stdout_tail
    assert "LEAKED_SECRET" not in paths.stdout.read_text(encoding="utf-8")


def test_runner_bounds_multibyte_tail_by_utf8_bytes(tmp_path: Path) -> None:
    """验证兼容参数名 tail_limit_chars 实际按 UTF-8 字节限制并保留合法边界。"""
    request = _request(tmp_path, "unicode_large")
    result = LocalProcessRunner(tail_limit_chars=10).run(request)
    assert result.outcome is ProcessOutcome.COMPLETED
    assert len(result.stdout_tail.encode("utf-8")) <= 10
    assert result.stdout_tail == "界" * 3


def test_runner_closes_provided_sinks_on_pre_cancel_without_unlinking(tmp_path: Path) -> None:
    """验证预取消仍释放已移交 sink，但绝不删除调用方预先创建的诊断文件。"""
    request = _request(tmp_path, "wait")
    stdout_writer = ExternalStreamWriter(request.stdout_path)
    stderr_writer = ExternalStreamWriter(request.stderr_path)
    owned_request = replace(request, stdout_sink=stdout_writer, stderr_sink=stderr_writer)
    cancellation = CancellationToken()
    cancellation.cancel()

    try:
        result = _runner().run(owned_request, cancellation)
        assert result.outcome is ProcessOutcome.CANCELLED
        assert request.stdout_path.exists()
        assert request.stderr_path.exists()
        with pytest.raises(RuntimeError):
            stdout_writer.write_text("late")
        with pytest.raises(RuntimeError):
            stderr_writer.write_text("late")
    finally:
        stdout_writer.close()
        stderr_writer.close()


def test_runner_closes_provided_sinks_when_secret_is_rejected(tmp_path: Path) -> None:
    """验证 spawn 前秘密拒绝不会访问租约，也不会遗留已移交的打开文件句柄。"""
    request = _request(tmp_path, "wait")
    stdout_writer = ExternalStreamWriter(request.stdout_path)
    stderr_writer = ExternalStreamWriter(request.stderr_path)
    secret_request = replace(
        request,
        stdout_sink=stdout_writer,
        stderr_sink=stderr_writer,
        secret_bindings=(SecretProcessBinding("id", SecretBindingTarget.STDIN, "stdin"),),
    )

    try:
        with pytest.raises(ConfigurationError):
            _runner().run(secret_request)
        assert request.stdout_path.exists()
        assert request.stderr_path.exists()
        with pytest.raises(RuntimeError):
            stdout_writer.write_text("late")
        with pytest.raises(RuntimeError):
            stderr_writer.write_text("late")
    finally:
        stdout_writer.close()
        stderr_writer.close()


def test_runner_preserves_provided_sink_files_when_spawn_fails(tmp_path: Path) -> None:
    """验证启动失败关闭调用方 sink 并保留其既有文件作为诊断定位器。"""
    request = ProcessRequest(
        executable=tmp_path / "missing.exe",
        arguments=(),
        working_directory=tmp_path,
        stdout_path=tmp_path / "provided-stdout.log",
        stderr_path=tmp_path / "provided-stderr.log",
    )
    stdout_writer = ExternalStreamWriter(request.stdout_path)
    stderr_writer = ExternalStreamWriter(request.stderr_path)
    owned_request = replace(request, stdout_sink=stdout_writer, stderr_sink=stderr_writer)

    try:
        result = _runner().run(owned_request)
        assert result.outcome is ProcessOutcome.START_FAILED
        assert request.stdout_path.exists()
        assert request.stderr_path.exists()
        with pytest.raises(RuntimeError):
            stdout_writer.write_text("late")
        with pytest.raises(RuntimeError):
            stderr_writer.write_text("late")
    finally:
        stdout_writer.close()
        stderr_writer.close()


def test_runner_drains_large_dual_streams_without_deadlock_and_bounds_tails(tmp_path: Path) -> None:
    """验证并发排空大双流，byte count 与有界 tail 均来自保留文件。"""
    result = _runner().run(_request(tmp_path, "large"))
    assert result.outcome is ProcessOutcome.COMPLETED
    assert result.stdout_bytes == 200_000
    assert result.stderr_bytes == 200_000
    assert len(result.stdout_tail) == 65_536
    assert len(result.stderr_tail) == 65_536


@pytest.mark.parametrize(
    ("mode", "argument", "expected"), [("invalid_utf8", "", 0), ("exit", "7", 7)]
)
def test_runner_replaces_invalid_utf8_and_treats_nonzero_as_completed(
    tmp_path: Path,
    mode: str,
    argument: str,
    expected: int,
) -> None:
    """验证非法 UTF-8 替换，且非零退出仍属于自然完成。"""
    arguments = (argument,) if argument else ()
    result = _runner().run(_request(tmp_path, mode, *arguments))
    assert result.outcome is ProcessOutcome.COMPLETED
    assert result.exit_code == expected
    if mode == "invalid_utf8":
        assert "before-�-after" in result.stdout_tail


def test_pre_cancel_and_secret_rejection_happen_before_output_creation(tmp_path: Path) -> None:
    """验证预取消不启动，秘密在 spawn 前拒绝且租约方法从未调用。"""
    token = CancellationToken()
    token.cancel()
    request = _request(tmp_path, "wait")
    result = _runner().run(request, token)
    assert result.outcome is ProcessOutcome.CANCELLED
    assert not request.stdout_path.exists()

    class Lease:
        """记录秘密租约是否被错误访问。"""

        calls = 0

        def resolve(self, binding_id: str) -> str:
            """记录错误解析并返回秘密。"""
            self.calls += 1
            return binding_id

        def close(self) -> None:
            """记录错误关闭。"""
            self.calls += 1

    lease = Lease()
    secret_request = replace(
        _request(tmp_path, "wait"),
        secret_lease=lease,
        secret_bindings=(SecretProcessBinding("id", SecretBindingTarget.STDIN, "stdin"),),
    )
    with pytest.raises(ConfigurationError):
        _runner().run(secret_request)
    assert lease.calls == 0
    assert not request.stdout_path.exists()


def test_timeout_and_event_driven_cancel_terminate_real_process(tmp_path: Path) -> None:
    """验证超时与由启动日志事件驱动的取消都终止真实阻塞进程。"""
    timeout_dir = tmp_path / "timeout"
    timeout_dir.mkdir()
    timeout_result = _runner().run(_request(timeout_dir, "wait", timeout=0.1))
    assert timeout_result.outcome is ProcessOutcome.TIMED_OUT
    assert timeout_result.error_code is ErrorCode.PROCESS_TIMEOUT

    cancel_dir = tmp_path / "cancel"
    cancel_dir.mkdir()
    logger = _EventLogger()
    token = CancellationToken()
    holder: list[object] = []

    def execute() -> None:
        """在测试线程中运行阻塞请求。"""
        holder.append(_runner(logger).run(_request(cancel_dir, "wait"), token))

    thread = threading.Thread(target=execute)
    thread.start()
    assert logger.started.wait(2.0)
    token.cancel()
    thread.join(5.0)
    assert not thread.is_alive()
    result = holder[0]
    assert getattr(result, "outcome") is ProcessOutcome.CANCELLED


@pytest.mark.parametrize(
    ("trigger", "expected_outcome"),
    [("timeout", ProcessOutcome.TIMED_OUT), ("cancel", ProcessOutcome.CANCELLED)],
)
def test_runner_terminates_real_parent_child_grandchild_tree(
    tmp_path: Path,
    trigger: str,
    expected_outcome: ProcessOutcome,
) -> None:
    """验证 timeout/cancel 都通过真实系统进程树终止根、子与忽略 TERM 的孙进程。"""
    run_directory = tmp_path / trigger
    run_directory.mkdir()
    pid_path = run_directory / "tree.json"
    nonce = uuid.uuid4().hex
    request = _request(
        run_directory,
        "tree",
        str(pid_path),
        nonce,
        timeout=2.0 if trigger == "timeout" else 10.0,
    )
    cancellation = CancellationToken()
    results: list[ProcessResult] = []
    thread = threading.Thread(
        target=lambda: results.append(_runner().run(request, cancellation)),
        name=f"real-tree-{trigger}",
    )
    record: _TreeRecord | None = None
    identities: list[_TrackedProcess] = []
    thread.start()
    try:
        record = _wait_for_tree_record(pid_path, nonce)
        for pid in record.pids:
            identities.append(_TrackedProcess(pid))
        assert all(identity.is_alive() for identity in identities)
        if trigger == "cancel":
            cancellation.cancel()

        thread.join(timeout=10.0)
        assert not thread.is_alive()
        assert len(results) == 1
        assert results[0].outcome is expected_outcome
        _wait_for_condition(
            lambda: not any(identity.is_alive() for identity in identities),
            timeout_seconds=5.0,
            description="真实父子孙进程全部退出",
        )
        assert all(not identity.is_alive() for identity in identities)
    finally:
        if thread.is_alive():
            cancellation.cancel()
        if record is not None and any(identity.is_alive() for identity in identities):
            _force_cleanup_tree(record)
        thread.join(timeout=5.0)
        for identity in identities:
            identity.close()


@pytest.mark.parametrize(
    ("fail_on_emit", "expected"),
    [(1, ProcessOutcome.START_FAILED), (2, ProcessOutcome.OUTPUT_FAILED)],
)
def test_logger_failures_map_by_spawn_boundary(
    tmp_path: Path,
    fail_on_emit: int,
    expected: ProcessOutcome,
) -> None:
    """验证 logger 失败按 spawn 前后映射且秘密诊断已脱敏。"""
    result = _runner(_EventLogger(fail_on_emit=fail_on_emit)).run(
        _request(tmp_path, "wait", timeout=0.2)
    )
    assert result.outcome is expected
    assert "logger-secret" not in result.diagnostic_message


@pytest.mark.parametrize(
    "arguments",
    [
        {"poll_interval_seconds": 0},
        {"poll_interval_seconds": float("inf")},
        {"tail_limit_chars": 0},
        {"tail_limit_chars": 65_537},
        {"tail_limit_chars": True},
        {"termination_grace_seconds": -1},
    ],
)
def test_runner_constructor_rejects_invalid_waiting_bounds(arguments: dict[str, object]) -> None:
    """验证 runner 构造器拒绝非有限、非正和越界等待参数。"""
    with pytest.raises((TypeError, ValueError)):
        LocalProcessRunner(**arguments)  # type: ignore[arg-type]


def test_runner_requires_logger_and_context_as_a_pair() -> None:
    """验证 logger 与真实日志上下文必须成对注入且类型正确。"""
    logger = _EventLogger()
    context = LogContext("build", "process", "run")
    with pytest.raises(ValueError):
        LocalProcessRunner(logger=logger)
    with pytest.raises(ValueError):
        LocalProcessRunner(log_context=context)
    with pytest.raises(TypeError):
        LocalProcessRunner(logger=object(), log_context=context)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        LocalProcessRunner(logger=logger, log_context=object())  # type: ignore[arg-type]


def test_runner_maps_real_start_and_output_initialization_failures(tmp_path: Path) -> None:
    """验证真实启动失败与输出文件碰撞分别映射稳定结果并精确清理。"""
    missing = ProcessRequest(
        executable=tmp_path / "missing.exe",
        arguments=(),
        working_directory=tmp_path,
        stdout_path=tmp_path / "start-out.log",
        stderr_path=tmp_path / "start-err.log",
    )
    started = _runner().run(missing)
    assert started.outcome is ProcessOutcome.START_FAILED
    assert started.error_code is ErrorCode.INTERNAL_ERROR
    assert missing.stdout_path.exists()
    assert missing.stderr_path.exists()

    collision_dir = tmp_path / "collision"
    collision_dir.mkdir()
    collision = _request(collision_dir, "exit", "0")
    collision.stderr_path.write_text("existing", encoding="utf-8")
    output = _runner().run(collision)
    assert output.outcome is ProcessOutcome.OUTPUT_FAILED
    assert not collision.stdout_path.exists()
    assert collision.stderr_path.read_text(encoding="utf-8") == "existing"


def test_result_logger_failure_after_natural_completion_maps_output_failed(tmp_path: Path) -> None:
    """验证自然退出后的结果日志失败转换为 OUTPUT_FAILED 而不向外抛出。"""
    result = _runner(_EventLogger(fail_on_emit=3)).run(_request(tmp_path, "exit", "0"))
    assert result.outcome is ProcessOutcome.OUTPUT_FAILED
    assert result.error_code is ErrorCode.INTERNAL_ERROR
    assert "logger-secret" not in result.diagnostic_message


def test_runner_rejects_invalid_public_arguments_before_side_effects(tmp_path: Path) -> None:
    """验证 run 拒绝错误请求与取消类型且不创建输出。"""
    request = _request(tmp_path, "exit", "0")
    with pytest.raises(TypeError):
        _runner().run(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        _runner().run(request, object())  # type: ignore[arg-type]
    assert not request.stdout_path.exists()


class _PosixGroupState:
    """用真实状态转移表达受控 POSIX 进程组，而非只断言 mock 调用次数。"""

    def __init__(
        self,
        *,
        disappears_on_term: bool = False,
        disappears_on_kill: bool = True,
        probe_error: BaseException | None = None,
        probe_error_after_signal: int | None = None,
        missing_on_signal: int | None = None,
        signal_error: BaseException | None = None,
    ) -> None:
        """保存信号后的组存活策略和可选探测错误。"""
        self.exists = True
        self.disappears_on_term = disappears_on_term
        self.disappears_on_kill = disappears_on_kill
        self.probe_error = probe_error
        self.probe_error_after_signal = probe_error_after_signal
        self.missing_on_signal = missing_on_signal
        self.signal_error = signal_error
        self.signals: list[int] = []
        self.process: _PosixProcess | None = None

    def killpg(self, pgid: int, value: int) -> None:
        """按信号更新组状态；信号 0 仅返回当前可观测存活状态。"""
        assert pgid == 42
        if value == 0:
            if self.probe_error is not None and (
                self.probe_error_after_signal is None
                or self.signals[-1:] == [self.probe_error_after_signal]
            ):
                raise self.probe_error
            if not self.exists:
                raise ProcessLookupError("进程组已消失")
            return
        if self.signal_error is not None:
            raise self.signal_error
        if value == self.missing_on_signal:
            self.exists = False
            assert self.process is not None
            self.process.returncode = -value
            raise ProcessLookupError("发送信号前进程组已消失")
        self.signals.append(value)
        if value == 15:
            assert self.process is not None
            self.process.returncode = -15
            if self.disappears_on_term:
                self.exists = False
        elif value == 9 and self.disappears_on_kill:
            self.exists = False


class _PosixProcess:
    """模拟父进程可被回收，但把完整组存活状态留给独立探测。"""

    pid = 42

    def __init__(self, group: _PosixGroupState) -> None:
        """关联受控进程组并初始化未退出父进程。"""
        self.returncode: int | None = None
        group.process = self

    def wait(self, timeout: float | None = None) -> int:
        """只模拟父进程回收，绝不代表其子孙所在进程组已消失。"""
        if self.returncode is None:
            raise process_module.subprocess.TimeoutExpired("fixture", timeout or 0.0)
        return self.returncode

    def poll(self) -> int | None:
        """返回父进程退出码，供结果对象记录。"""
        return self.returncode


class _MemorySink:
    """为终止结果选择提供无 I/O 的完整 ProcessTextSink。"""

    def __init__(self, path: Path) -> None:
        """保存端口要求的精确路径和零字节计数。"""
        self.path = path
        self.byte_count = 0

    def write_text(self, text: str) -> str:
        """返回输入文本；本组测试不产生实际进程输出。"""
        return text

    def close(self) -> str:
        """返回空 finalize 文本且无外部副作用。"""
        return ""


def _posix_termination_outcome(
    runner: LocalProcessRunner,
    request: ProcessRequest,
    process: _PosixProcess,
    termination_error: str | None,
) -> ProcessOutcome:
    """通过生产结果选择逻辑把终止诊断转换为最终 outcome。"""
    changed = threading.Event()
    stdout_capture = process_module._StreamCapture(  # pyright: ignore[reportPrivateUsage]
        _MemorySink(request.stdout_path),
        64,
        changed,
    )
    stderr_capture = process_module._StreamCapture(  # pyright: ignore[reportPrivateUsage]
        _MemorySink(request.stderr_path),
        64,
        changed,
    )
    result = runner._select_result(  # pyright: ignore[reportPrivateUsage]
        request,
        process_module.time.monotonic(),
        process,  # type: ignore[arg-type]
        stdout_capture,
        stderr_capture,
        "cancel",
        termination_error,
        None,
    )
    return result.outcome


def _run_posix_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    group: _PosixGroupState,
) -> tuple[ProcessOutcome, str | None]:
    """在短条件等待边界内运行生产 POSIX 终止与结果映射。"""
    runner = LocalProcessRunner(poll_interval_seconds=0.001, termination_grace_seconds=0.01)
    request = _request(tmp_path, "wait")
    process = _PosixProcess(group)
    with monkeypatch.context() as context:
        context.setattr(process_module.os, "name", "posix")
        context.setattr(process_module.os, "killpg", group.killpg, raising=False)
        context.setattr(process_module.signal, "SIGTERM", 15, raising=False)
        context.setattr(process_module.signal, "SIGKILL", 9, raising=False)
        error = runner._terminate_tree(process)  # type: ignore[arg-type]
    return _posix_termination_outcome(runner, request, process, error), error


def test_posix_parent_exit_does_not_hide_live_group_and_escalates_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 TERM 后父退出但组仍存活时必须升级 KILL，最终组消失才算成功。"""
    group = _PosixGroupState()
    outcome, error = _run_posix_termination(tmp_path, monkeypatch, group)
    assert error is None
    assert outcome is ProcessOutcome.CANCELLED
    assert group.exists is False
    assert group.signals == [15, 9]


def test_posix_group_alive_after_kill_maps_termination_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 KILL 后完整组仍存在时返回 TERMINATION_FAILED，而非相信父退出。"""
    group = _PosixGroupState(disappears_on_kill=False)
    outcome, error = _run_posix_termination(tmp_path, monkeypatch, group)
    assert error is not None
    assert outcome is ProcessOutcome.TERMINATION_FAILED
    assert group.exists is True
    assert group.signals == [15, 9]


def test_posix_group_disappearing_after_term_needs_no_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 TERM 后进程组立即消失时成功结束且不发送多余 KILL。"""
    group = _PosixGroupState(disappears_on_term=True)
    outcome, error = _run_posix_termination(tmp_path, monkeypatch, group)
    assert error is None
    assert outcome is ProcessOutcome.CANCELLED
    assert group.exists is False
    assert group.signals == [15]


@pytest.mark.parametrize("probe_error", [PermissionError("denied"), OSError("broken probe")])
def test_posix_group_probe_errors_map_termination_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_error: BaseException,
) -> None:
    """验证权限或其他 OS 探测错误均保守映射为 TERMINATION_FAILED。"""
    group = _PosixGroupState(probe_error=probe_error)
    outcome, error = _run_posix_termination(tmp_path, monkeypatch, group)
    assert error is not None
    assert outcome is ProcessOutcome.TERMINATION_FAILED
    assert group.exists is True
    assert group.signals == [15]


@pytest.mark.parametrize("missing_signal", [15, 9])
def test_posix_signal_race_disappearance_is_success_without_later_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_signal: int,
) -> None:
    """验证 TERM/KILL 发送竞态发现组已消失时成功，且不再触碰固定 pgid。"""
    group = _PosixGroupState(missing_on_signal=missing_signal)
    outcome, error = _run_posix_termination(tmp_path, monkeypatch, group)
    assert error is None
    assert outcome is ProcessOutcome.CANCELLED
    assert group.exists is False
    assert group.signals == ([] if missing_signal == 15 else [15])


def test_posix_probe_error_after_kill_maps_termination_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 KILL 后探测失败不会被当成组消失成功。"""
    group = _PosixGroupState(
        probe_error=PermissionError("denied after kill"),
        probe_error_after_signal=9,
    )
    outcome, error = _run_posix_termination(tmp_path, monkeypatch, group)
    assert error is not None
    assert outcome is ProcessOutcome.TERMINATION_FAILED
    assert group.exists is False
    assert group.signals == [15, 9]


def test_posix_term_signal_error_maps_termination_failed_and_keeps_group_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 TERM 自身失败时返回安全终止失败，不伪造组已退出状态。"""
    group = _PosixGroupState(signal_error=OSError("token=term-secret"))
    outcome, error = _run_posix_termination(tmp_path, monkeypatch, group)
    assert error is not None and "term-secret" not in error
    assert outcome is ProcessOutcome.TERMINATION_FAILED
    assert group.exists is True
    assert group.signals == []


def test_internal_safety_helpers_redact_unprintable_and_continue_cleanup() -> None:
    """验证异常无法字符串化仍脱敏，writer 关闭失败不阻止其余资源清理。"""

    class Unprintable(RuntimeError):
        """字符串化会失败的边界异常。"""

        def __str__(self) -> str:
            """模拟不可靠第三方异常。"""
            raise LookupError("token=secondary")

    class Writer:
        """记录关闭并可注入单个失败。"""

        def __init__(self, fail: bool) -> None:
            """保存失败开关。"""
            self.fail = fail
            self.closed = False

        def close(self) -> None:
            """记录关闭并按配置失败。"""
            self.closed = True
            if self.fail:
                raise OSError("password=writer-secret")

    assert "secondary" not in process_module._safe_error_text(Unprintable())  # pyright: ignore[reportPrivateUsage]
    first = Writer(True)
    second = Writer(False)
    error = LocalProcessRunner._close_writers([first, second])  # type: ignore[list-item]
    assert first.closed and second.closed
    assert error is not None and "writer-secret" not in error


def test_capture_and_reader_preserve_first_safe_error() -> None:
    """验证捕获器只保留首错，reader 读取失败通过事件传回而不跨线程抛出。"""

    class Writer:
        """提供捕获器所需的最小 writer 契约。"""

        byte_count = 0

        def write_text(self, text: str) -> None:
            """本用例不执行成功写入。"""

    class BrokenPipe:
        """读取时抛出含秘密异常的二进制 pipe。"""

        def read(self, size: int = -1) -> bytes:
            """模拟底层 pipe 读取失败。"""
            raise OSError("token=pipe-secret")

    changed = threading.Event()
    capture = process_module._StreamCapture(Writer(), 32, changed)  # type: ignore[arg-type]
    first = OSError("first")
    capture.fail(first)
    capture.fail(OSError("second"))
    assert capture.error is first
    assert changed.is_set()

    changed.clear()
    read_capture = process_module._StreamCapture(Writer(), 32, changed)  # type: ignore[arg-type]
    process_module._read_stream(BrokenPipe(), read_capture, changed)  # type: ignore[arg-type]
    assert isinstance(read_capture.error, OSError)
    assert changed.is_set()
    safe_error = LocalProcessRunner._first_capture_error(  # pyright: ignore[reportPrivateUsage]
        (capture, read_capture)
    )
    assert safe_error == "first"


def test_empty_diagnostic_and_numeric_type_fallbacks_are_stable() -> None:
    """验证空诊断使用稳定兜底，构造器拒绝非数值轮询间隔。"""
    assert process_module._combine_diagnostics("", "   ") == "进程执行失败"  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(TypeError):
        LocalProcessRunner(poll_interval_seconds="fast")  # type: ignore[arg-type]


def test_windows_termination_reports_missing_tool_root_and_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Windows 终止缺少 SystemRoot 或 taskkill 非零时返回稳定失败诊断。"""

    class Process:
        """提供终止路径所需 pid 与等待接口。"""

        pid = 42

        def wait(self, timeout: float | None = None) -> int:
            """模拟父进程仍可等待。"""
            return 0

        def poll(self) -> int | None:
            """模拟尚未退出。"""
            return None

    with monkeypatch.context() as context:
        context.delenv("SystemRoot", raising=False)
        missing = _runner()._terminate_tree(Process())  # type: ignore[arg-type]
    assert missing is not None and "SystemRoot" in missing

    class Completed:
        """模拟 taskkill 非零结果。"""

        returncode = 5

    def fake_run(*args: object, **kwargs: object) -> Completed:
        """返回受控 taskkill 非零结果。"""
        return Completed()

    with monkeypatch.context() as context:
        context.setenv("SystemRoot", "C:/Windows")
        context.setattr(process_module.subprocess, "run", fake_run)
        nonzero = _runner()._terminate_tree(Process())  # type: ignore[arg-type]
    assert nonzero is not None and "5" in nonzero


def test_reader_close_and_partial_rollback_failures_return_safe_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 pipe 关闭与精确文件删除失败均转换为安全诊断而非逸出。"""

    class BrokenPipe:
        """关闭时失败的 pipe。"""

        def close(self) -> None:
            """模拟关闭失败。"""
            raise OSError("password=close-secret")

    error = _runner()._finish_readers([], [BrokenPipe()], threading.Event())  # type: ignore[list-item]
    assert error is not None and "close-secret" not in error

    path = tmp_path / "created.log"
    path.write_text("diagnostic", encoding="utf-8")

    def fail_unlink(self: Path, missing_ok: bool = False) -> None:
        """模拟精确文件删除失败。"""
        raise OSError("token=unlink-secret")

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", fail_unlink)
        rollback = LocalProcessRunner._rollback_unpreserved([], [path])  # pyright: ignore[reportPrivateUsage]
    assert rollback is not None and "unlink-secret" not in rollback
