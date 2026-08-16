"""外部进程执行端口的纯内存契约。

本模块定义进程请求、结果、取消令牌、秘密绑定描述和执行器协议。端口对象只保存
已经校验的公开元数据，不解析秘密、不启动外部程序、不读写文件，也不访问进程
环境；具体执行和资源清理由后续集成适配器负责。
"""

from __future__ import annotations

import math
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, cast, final, runtime_checkable

from observability.failures import ErrorCode
from observability.redaction import redact_text

_ARGUMENT_SLOT_PATTERN = re.compile(r"^[0-9]+$")
_MAX_TAIL_CHARACTERS = 65_536


def _validate_non_empty_control_free_string(value: object, *, field_name: str) -> str:
    """校验非空且不含 Unicode 控制字符的字符串。

    参数：
        value: 待校验对象。
        field_name: 用于错误消息的字段名。

    返回：
        通过校验的原字符串。

    异常：
        类型不是 ``str`` 时抛出 ``TypeError``；为空或包含控制字符时抛出
        ``ValueError``。

    约束与副作用：
        空格等可见分隔字符不属于控制字符；函数只检查内存数据，无外部副作用。
    """
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str")
    if value == "":
        raise ValueError(f"{field_name} 不得为空")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(f"{field_name} 不得包含控制字符")
    return value


def _validate_environment_key(value: object, *, field_name: str) -> str:
    """校验可用于进程环境映射的键名。

    参数：
        value: 待校验环境键对象。
        field_name: 用于错误消息的字段名。

    返回：
        通过校验的原字符串。

    异常：
        类型不是 ``str`` 时抛出 ``TypeError``；为空或含 ``=``、NUL 时抛出
        ``ValueError``。

    约束与副作用：
        仅执行跨平台最低公共约束，不读取当前进程环境，无外部副作用。
    """
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str")
    if value == "":
        raise ValueError(f"{field_name} 不得为空")
    if "=" in value or "\x00" in value:
        raise ValueError(f"{field_name} 不得包含 '=' 或 NUL")
    return value


def _validate_absolute_path(value: object, *, field_name: str) -> Path:
    """校验字段是绝对 ``Path``，但不访问对应文件系统对象。

    参数：
        value: 待校验路径对象。
        field_name: 用于错误消息的字段名。

    返回：
        通过校验的原 ``Path``。

    异常：
        类型不是 ``Path`` 时抛出 ``TypeError``；路径不是绝对路径时抛出
        ``ValueError``。

    约束与副作用：
        不解析符号链接，不判断路径是否存在，也不产生 I/O。
    """
    if not isinstance(value, Path):
        raise TypeError(f"{field_name} 必须是 Path")
    if not value.is_absolute():
        raise ValueError(f"{field_name} 必须是绝对路径")
    return value


def _validate_finite_positive_number(value: object, *, field_name: str) -> float:
    """校验有限正数并返回规范化浮点值。

    参数：
        value: 待校验数值对象。
        field_name: 用于错误消息的字段名。

    返回：
        等值 ``float``。

    异常：
        非 ``int`` / ``float`` 或布尔值抛出 ``TypeError``；非有限或非正数抛出
        ``ValueError``。

    约束与副作用：
        仅作数值规范化，不执行等待或其他外部操作。
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{field_name} 必须是 int 或 float")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field_name} 必须是有限正数")
    return normalized


@final
class CancellationToken:
    """可由多个线程安全共享的协作式取消令牌。

    职责：
        使用 ``threading.Event`` 保存单向取消状态，并让调用方等待取消通知。

    参数：
        构造函数无参数；初始状态为未取消。

    返回：
        无；通过 ``cancel``、``is_cancelled`` 和 ``wait`` 操作实例。

    异常：
        仅 ``wait`` 的非法超时参数会抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        状态只能从未取消变为已取消；不启动线程或外部程序，不产生 I/O。
    """

    __slots__ = ("_cancel_lock", "_cancelled_event")

    def __init__(self) -> None:
        """创建尚未取消的令牌。

        参数与返回：
            无参数，无返回值。

        异常、约束与副作用：
            标准同步原语构造失败时沿用其异常；只分配内存同步对象，不启动线程。
        """
        self._cancelled_event = threading.Event()
        self._cancel_lock = threading.Lock()

    def cancel(self) -> bool:
        """原子地把令牌切换为已取消状态。

        参数：
            无。

        返回：
            本次首次设置取消状态时为 ``True``；状态此前已设置时为 ``False``。

        异常、约束与副作用：
            不抛出业务异常；唤醒正在 ``wait`` 的线程，不启动新线程或产生 I/O。
        """
        with self._cancel_lock:
            if self._cancelled_event.is_set():
                return False
            self._cancelled_event.set()
            return True

    @property
    def is_cancelled(self) -> bool:
        """返回当前是否已经收到取消请求。

        参数：
            无。

        返回：
            已取消为 ``True``，否则为 ``False``。

        异常、约束与副作用：
            不抛出业务异常；线程安全读取内存状态，无外部副作用。
        """
        return self._cancelled_event.is_set()

    def wait(self, timeout_seconds: float | None = None) -> bool:
        """等待取消状态，或在限定时间后返回。

        参数：
            timeout_seconds: 最长等待秒数；``None`` 表示无限等待，有限值必须非负。

        返回：
            返回时令牌已取消为 ``True``；等待超时为 ``False``。

        异常：
            超时不是数值或是布尔值时抛出 ``TypeError``；负数或非有限值时抛出
            ``ValueError``。

        约束与副作用：
            只阻塞当前调用线程；不启动线程或外部程序，不产生 I/O。
        """
        if timeout_seconds is None:
            return self._cancelled_event.wait()
        timeout_object = cast(object, timeout_seconds)
        if not isinstance(timeout_object, (int, float)) or isinstance(timeout_object, bool):
            raise TypeError("timeout_seconds 必须是 float 或 None")
        normalized_timeout = float(timeout_object)
        if not math.isfinite(normalized_timeout) or normalized_timeout < 0:
            raise ValueError("timeout_seconds 必须是有限非负数")
        return self._cancelled_event.wait(normalized_timeout)


class SecretBindingTarget(str, Enum):
    """秘密值在外部进程请求中的允许绑定目标。

    职责：
        以稳定枚举限制秘密只能绑定到参数、环境、标准输入或受控临时文件路径。

    参数与返回：
        枚举成员无额外参数；成员值是稳定的小写协议字符串。

    异常、约束与副作用：
        非法名称或值由 ``Enum`` 拒绝；枚举不持有秘密值且无外部副作用。
    """

    ARGUMENT = "argument"
    ENVIRONMENT = "environment"
    STDIN = "stdin"
    TEMP_FILE = "temp_file"


@dataclass(frozen=True, slots=True)
class SecretProcessBinding:
    """秘密 binding ID 到进程公开槽位的不可变映射。

    职责：
        描述未来执行器应把短期租约中的某个秘密绑定到哪个槽位，不保存秘密值。

    参数：
        binding_id: 租约内的不透明非空标识，不得包含控制字符。
        target: 允许的 ``SecretBindingTarget``。
        slot: 参数索引、环境键、固定 ``stdin`` 名称或临时文件路径环境键。

    返回：
        无；实例是不可变端口值对象。

    异常：
        类型非法时抛出 ``TypeError``；标识或槽位违反目标规则时抛出
        ``ValueError``。

    约束与副作用：
        只保存 binding ID 和公开目标，不解析租约、不保存秘密值且无 I/O。
    """

    binding_id: str
    target: SecretBindingTarget
    slot: str

    def __post_init__(self) -> None:
        """校验 binding ID、目标枚举和目标专属槽位规则。

        参数与返回：
            无显式参数，无返回值；读取当前实例字段。

        异常：
            字段类型非法时抛出 ``TypeError``；字段值不满足约束时抛出
            ``ValueError``。

        约束与副作用：
            仅校验公开元数据，不读取秘密租约或外部状态。
        """
        _validate_non_empty_control_free_string(self.binding_id, field_name="binding_id")
        target = cast(object, self.target)
        if not isinstance(target, SecretBindingTarget):
            raise TypeError("target 必须是 SecretBindingTarget")
        slot = _validate_non_empty_control_free_string(self.slot, field_name="slot")

        if target is SecretBindingTarget.ARGUMENT:
            if _ARGUMENT_SLOT_PATTERN.fullmatch(slot) is None:
                raise ValueError("ARGUMENT slot 必须是非负十进制索引")
        elif target is SecretBindingTarget.ENVIRONMENT:
            _validate_environment_key(slot, field_name="ENVIRONMENT slot")
        elif target is SecretBindingTarget.STDIN:
            if slot != "stdin":
                raise ValueError("STDIN slot 必须固定为 'stdin'")
        elif target is SecretBindingTarget.TEMP_FILE:
            _validate_environment_key(slot, field_name="TEMP_FILE slot")


class SecretLease(Protocol):
    """供未来秘密提供器实现的短期租约协议。

    职责：
        根据不透明 binding ID 临时解析秘密，并在使用结束后关闭租约。

    约束与副作用：
        本模块只固化接口，不实现解析或清理；调用方不得打印解析结果。
    """

    def resolve(self, binding_id: str) -> str:
        """解析租约内 binding ID 对应的短期秘密。

        参数：
            binding_id: ``SecretProcessBinding`` 携带的不透明标识。

        返回：
            仅供外部调用边界即时使用的秘密字符串。

        异常、约束与副作用：
            具体异常与副作用由未来实现声明；Task 7 不调用本方法。
        """
        ...

    def close(self) -> None:
        """关闭租约并清理短期秘密材料。

        参数与返回：
            无参数，无返回值。

        异常、约束与副作用：
            具体清理语义由未来实现声明；Task 7 不调用本方法。
        """
        ...


@runtime_checkable
class ProcessTextSink(Protocol):
    """进程文本输出接收器的所有权转移协议。

    职责：接收已解码文本并返回实际写出的统一脱敏文本；关闭时返回 finalize 文本。
    ``path`` 与 ``byte_count`` 分别公开精确文件位置和已写 UTF-8 字节数。
    实现可以产生文件 I/O；调用方把 sink 交给请求后由 runner 负责关闭。
    """

    @property
    def path(self) -> Path:
        """返回接收器拥有的精确输出路径；读取不得产生 I/O。"""
        ...

    @property
    def byte_count(self) -> int:
        """返回已成功写出的脱敏 UTF-8 字节数；必须为非负整数。"""
        ...

    def write_text(self, text: str) -> str:
        """写入文本并返回本次实际发出的统一脱敏文本。"""
        ...

    def close(self) -> str:
        """完成流式脱敏、关闭自有资源并返回最终发出的脱敏文本。"""
        ...


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    """一次外部进程执行的不可变请求。

    职责：
        保存绝对路径、参数、规范环境、超时、输出位置、显式脱敏索引和秘密绑定。

    参数：
        executable: 外部程序绝对 ``Path``。
        arguments: 参数字符串元组；每项不得包含 NUL。
        working_directory: 工作目录绝对 ``Path``。
        stdout_path: 标准输出目标绝对 ``Path``。
        stderr_path: 标准错误目标绝对 ``Path``，不得与标准输出路径相同。
        environment: 环境键值元组；键唯一，构造后按键 UTF-8 字节序排序。
        timeout_seconds: 有限正超时秒数。
        redacted_argument_indexes: 必须整项脱敏的有效参数索引。
        secret_bindings: 不含秘密值的绑定描述元组，ID 和目标槽位均唯一。
        secret_lease: 可选短期租约；不参与 repr 和值比较。

    返回：
        无；实例是经过校验的不可变端口值对象。

    异常：
        字段类型非法时抛出 ``TypeError``；任一不变量失败时抛出 ``ValueError``。

    约束与副作用：
        第一层允许携带秘密字段，后续执行器必须在启动前拒绝尚未支持的绑定；构造
        过程不调用租约、不访问路径、不修改环境且无 I/O。
    """

    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path
    stdout_path: Path
    stderr_path: Path
    environment: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 60.0
    redacted_argument_indexes: frozenset[int] = frozenset()
    secret_bindings: tuple[SecretProcessBinding, ...] = ()
    secret_lease: SecretLease | None = field(default=None, repr=False, compare=False)
    stdout_sink: ProcessTextSink | None = field(default=None, repr=False, compare=False)
    stderr_sink: ProcessTextSink | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """校验并规范化请求中的路径、参数、环境、超时和秘密绑定。

        参数与返回：
            无显式参数，无返回值；读取当前实例字段，并只规范化环境顺序与超时类型。

        异常：
            字段类型非法时抛出 ``TypeError``；路径、数值、重复项或索引非法时抛出
            ``ValueError``。

        约束与副作用：
            规范化通过 ``object.__setattr__`` 完成后对象保持冻结；不访问文件系统、
            不调用秘密租约且不修改真实进程环境。
        """
        _validate_absolute_path(self.executable, field_name="executable")
        _validate_absolute_path(self.working_directory, field_name="working_directory")
        stdout_path = _validate_absolute_path(self.stdout_path, field_name="stdout_path")
        stderr_path = _validate_absolute_path(self.stderr_path, field_name="stderr_path")
        if stdout_path.as_posix().casefold() == stderr_path.as_posix().casefold():
            raise ValueError("stdout_path 与 stderr_path 不得相同")

        arguments_object = cast(object, self.arguments)
        if not isinstance(arguments_object, tuple):
            raise TypeError("arguments 必须是 tuple[str, ...]")
        arguments = cast(tuple[object, ...], arguments_object)
        for argument_object in arguments:
            if not isinstance(argument_object, str):
                raise TypeError("arguments 的元素必须是 str")
            if "\x00" in argument_object:
                raise ValueError("arguments 的元素不得包含 NUL")

        environment_object = cast(object, self.environment)
        if not isinstance(environment_object, tuple):
            raise TypeError("environment 必须是键值元组")
        environment = cast(tuple[object, ...], environment_object)
        normalized_environment: list[tuple[str, str]] = []
        seen_environment_keys: set[str] = set()
        for entry_object in environment:
            if not isinstance(entry_object, tuple):
                raise TypeError("environment 的元素必须是二元 tuple")
            entry = cast(tuple[object, ...], entry_object)
            if len(entry) != 2:
                raise TypeError("environment 的元素必须是二元 tuple")
            key, value = entry
            key = _validate_environment_key(key, field_name="environment key")
            if not isinstance(value, str):
                raise TypeError("environment value 必须是 str")
            if "\x00" in value:
                raise ValueError("environment value 不得包含 NUL")
            normalized_key = key.casefold()
            if normalized_key in seen_environment_keys:
                raise ValueError(f"environment 存在重复键: {key!r}")
            seen_environment_keys.add(normalized_key)
            normalized_environment.append((key, value))
        normalized_environment.sort(key=lambda item: item[0].encode("utf-8"))
        object.__setattr__(self, "environment", tuple(normalized_environment))

        normalized_timeout = _validate_finite_positive_number(
            self.timeout_seconds,
            field_name="timeout_seconds",
        )
        object.__setattr__(self, "timeout_seconds", normalized_timeout)

        indexes_object = cast(object, self.redacted_argument_indexes)
        if not isinstance(indexes_object, frozenset):
            raise TypeError("redacted_argument_indexes 必须是 frozenset[int]")
        indexes = cast(frozenset[object], indexes_object)
        for index_object in indexes:
            if not isinstance(index_object, int) or isinstance(index_object, bool):
                raise TypeError("redacted_argument_indexes 的元素必须是 int")
            if index_object < 0 or index_object >= len(arguments):
                raise ValueError(f"redacted_argument_indexes 包含越界索引: {index_object}")

        bindings_object = cast(object, self.secret_bindings)
        if not isinstance(bindings_object, tuple):
            raise TypeError("secret_bindings 必须是 SecretProcessBinding 元组")
        bindings = cast(tuple[object, ...], bindings_object)
        seen_binding_ids: set[str] = set()
        seen_binding_slots: set[tuple[SecretBindingTarget, str]] = set()
        for binding_object in bindings:
            if not isinstance(binding_object, SecretProcessBinding):
                raise TypeError("secret_bindings 的元素必须是 SecretProcessBinding")
            binding = binding_object
            if binding.binding_id in seen_binding_ids:
                raise ValueError(f"secret_bindings 存在重复 binding_id: {binding.binding_id!r}")
            binding_slot = (binding.target, binding.slot)
            if binding_slot in seen_binding_slots:
                raise ValueError(
                    f"secret_bindings 存在重复目标槽位: {binding.target.value}:{binding.slot}"
                )
            seen_binding_ids.add(binding.binding_id)
            seen_binding_slots.add(binding_slot)

        stdout_sink = cast(object, self.stdout_sink)
        stderr_sink = cast(object, self.stderr_sink)
        if (stdout_sink is None) != (stderr_sink is None):
            raise ValueError("stdout_sink 与 stderr_sink 必须同时提供或同时为 None")
        if stdout_sink is not None and stderr_sink is not None:
            if not isinstance(stdout_sink, ProcessTextSink):
                raise TypeError("stdout_sink 必须实现 ProcessTextSink")
            if not isinstance(stderr_sink, ProcessTextSink):
                raise TypeError("stderr_sink 必须实现 ProcessTextSink")
            if stdout_sink is stderr_sink:
                raise ValueError("stdout_sink 与 stderr_sink 不得是同一对象")
            stdout_sink_path = cast(object, stdout_sink.path)
            stderr_sink_path = cast(object, stderr_sink.path)
            if not isinstance(stdout_sink_path, Path) or stdout_sink_path != self.stdout_path:
                raise ValueError("stdout_sink.path 必须精确等于 stdout_path")
            if not isinstance(stderr_sink_path, Path) or stderr_sink_path != self.stderr_path:
                raise ValueError("stderr_sink.path 必须精确等于 stderr_path")


class ProcessOutcome(str, Enum):
    """外部进程执行的稳定结果类别。

    职责：
        区分正常完成、超时、取消、终止失败、启动失败和输出处理失败。

    参数与返回：
        枚举成员无额外参数；成员值是稳定的小写协议字符串。

    异常、约束与副作用：
        非法名称或值由 ``Enum`` 拒绝；枚举声明无外部副作用。
    """

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    TERMINATION_FAILED = "termination_failed"
    START_FAILED = "start_failed"
    OUTPUT_FAILED = "output_failed"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """一次外部进程执行的不可变结果摘要。

    职责：
        保存稳定结果类别、退出码、耗时、输出位置和大小、有界尾部及脱敏诊断。

    参数：
        outcome: ``ProcessOutcome``。
        exit_code: 可选进程退出码；完成时必须存在。
        duration_seconds: 有限非负耗时秒数。
        stdout_path: 标准输出 ``Path``。
        stderr_path: 标准错误 ``Path``。
        stdout_bytes: 标准输出非负字节数。
        stderr_bytes: 标准错误非负字节数。
        stdout_tail: 最多 65536 个字符的标准输出尾部。
        stderr_tail: 最多 65536 个字符的标准错误尾部。
        error_code: 与结果类别严格对应的稳定 ``ErrorCode``。
        diagnostic_message: 失败时必填的诊断文本，构造时统一脱敏。

    返回：
        无；实例是经过校验的不可变结果值对象。

    异常：
        字段类型非法时抛出 ``TypeError``；数值、长度或结果映射非法时抛出
        ``ValueError``。

    约束与副作用：
        尾部文本不在本层解析；诊断文本不保留常见凭据形式。构造过程无 I/O。
    """

    outcome: ProcessOutcome
    exit_code: int | None
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path
    stdout_bytes: int
    stderr_bytes: int
    stdout_tail: str = ""
    stderr_tail: str = ""
    error_code: ErrorCode | None = None
    diagnostic_message: str = ""

    def __post_init__(self) -> None:
        """校验结果数值、尾部边界、结果映射并脱敏诊断文本。

        参数与返回：
            无显式参数，无返回值；读取当前实例字段并规范化耗时和诊断文本。

        异常：
            字段类型非法时抛出 ``TypeError``；边界或 outcome/error_code 映射不合法
            时抛出 ``ValueError``。

        约束与副作用：
            只处理内存数据，不读取输出文件、不记录日志且无 I/O。
        """
        outcome = cast(object, self.outcome)
        if not isinstance(outcome, ProcessOutcome):
            raise TypeError("outcome 必须是 ProcessOutcome")
        exit_code = cast(object, self.exit_code)
        if exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool)
        ):
            raise TypeError("exit_code 必须是 int 或 None")
        duration_object = cast(object, self.duration_seconds)
        if not isinstance(duration_object, (int, float)) or isinstance(duration_object, bool):
            raise TypeError("duration_seconds 必须是 int 或 float")
        normalized_duration = float(duration_object)
        if not math.isfinite(normalized_duration) or normalized_duration < 0:
            raise ValueError("duration_seconds 必须是有限非负数")
        object.__setattr__(self, "duration_seconds", normalized_duration)

        stdout_path = cast(object, self.stdout_path)
        if not isinstance(stdout_path, Path):
            raise TypeError("stdout_path 必须是 Path")
        stderr_path = cast(object, self.stderr_path)
        if not isinstance(stderr_path, Path):
            raise TypeError("stderr_path 必须是 Path")
        for field_name, byte_count in (
            ("stdout_bytes", cast(object, self.stdout_bytes)),
            ("stderr_bytes", cast(object, self.stderr_bytes)),
        ):
            if not isinstance(byte_count, int) or isinstance(byte_count, bool):
                raise TypeError(f"{field_name} 必须是 int")
            if byte_count < 0:
                raise ValueError(f"{field_name} 必须是非负整数")

        for field_name, tail in (
            ("stdout_tail", cast(object, self.stdout_tail)),
            ("stderr_tail", cast(object, self.stderr_tail)),
        ):
            if not isinstance(tail, str):
                raise TypeError(f"{field_name} 必须是 str")
            if len(tail.encode("utf-8")) > _MAX_TAIL_CHARACTERS:
                raise ValueError(f"{field_name} UTF-8 编码后最多 {_MAX_TAIL_CHARACTERS} 字节")

        error_code = cast(object, self.error_code)
        if error_code is not None and not isinstance(error_code, ErrorCode):
            raise TypeError("error_code 必须是 ErrorCode 或 None")
        diagnostic_object = cast(object, self.diagnostic_message)
        if not isinstance(diagnostic_object, str):
            raise TypeError("diagnostic_message 必须是 str")
        redacted_diagnostic = redact_text(diagnostic_object)
        object.__setattr__(self, "diagnostic_message", redacted_diagnostic)

        if outcome is ProcessOutcome.COMPLETED:
            if exit_code is None:
                raise ValueError("COMPLETED 必须提供 exit_code")
            if error_code is not None:
                raise ValueError("COMPLETED 不得提供 error_code")
            return

        if redacted_diagnostic.strip() == "":
            raise ValueError("失败结果必须提供非空 diagnostic_message")

        expected_error_code = ErrorCode.INTERNAL_ERROR
        if outcome is ProcessOutcome.TIMED_OUT:
            expected_error_code = ErrorCode.PROCESS_TIMEOUT
        elif outcome is ProcessOutcome.CANCELLED:
            expected_error_code = ErrorCode.PROCESS_CANCELLED
        if error_code is not expected_error_code:
            raise ValueError(f"{outcome.value} 必须使用 error_code={expected_error_code.value}")


class ProcessRunner(Protocol):
    """外部进程执行器的稳定端口协议。

    职责：
        接收已校验请求和可选取消令牌，返回类型化且诊断已脱敏的执行结果。

    约束与副作用：
        具体外部副作用由集成适配器声明；本协议自身不执行任何操作。
    """

    def run(
        self,
        request: ProcessRequest,
        cancellation: CancellationToken | None = None,
    ) -> ProcessResult:
        """执行一次请求并返回类型化结果。

        参数：
            request: 已完成内存校验的进程请求。
            cancellation: 可选协作式取消令牌。

        返回：
            ``ProcessResult`` 执行结果。

        异常、约束与副作用：
            具体异常和资源管理由实现声明；Task 7 只定义接口，不执行请求。
        """
        ...
