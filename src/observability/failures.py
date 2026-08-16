"""稳定错误码、不可变失败结果和异常链安全转换。

本模块把业务或外部异常转换为不持有异常对象与 traceback 的纯数据结构，供后续
日志和应用边界使用。转换过程只进行内存校验、确定排序和基础凭据脱敏，不执行
I/O、日志记录、重试或任何外部副作用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import cast

from core.errors import ConfigurationError

_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----"
    r".*?"
    r"-----END (?P=label)-----",
    flags=re.IGNORECASE | re.DOTALL,
)
_URL_USERINFO_PATTERN = re.compile(
    r"(?<![A-Z0-9+.-])"
    r"(?P<scheme>[A-Z][A-Z0-9+.-]*://)[^/\s:@]+(?::[^@\s/]+)?@",
    flags=re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<name>\b[A-Z0-9_]*(?:password|token|secret|authorization|api_key)"
    r"[A-Z0-9_]*\b)"
    r"(?P<separator>\s*[=:]\s*)[^\r\n]*",
    flags=re.IGNORECASE,
)
_SENSITIVE_CONTEXT_KEY_PATTERN = re.compile(
    r"(?:password|token|secret|authorization|api_key)",
    flags=re.IGNORECASE,
)


class ErrorCode(str, Enum):
    """正式发布流程对外稳定的错误码。

    职责：
        固定正式设计第 12 节的全部业务错误码，并提供 ``INTERNAL_ERROR`` 作为
        调用场景未显式映射未知异常时的安全兜底。

    参数与返回：
        枚举成员无额外构造参数；成员值为与成员名相同的稳定字符串。

    异常、约束与副作用：
        非法名称或值由 ``Enum`` 标准机制拒绝。错误码集合属于公开协议，不能随意
        改名或增加；枚举声明不产生外部副作用。
    """

    CONFIG_VALIDATION_FAILED = "CONFIG_VALIDATION_FAILED"
    REQUEST_ID_CONFLICT = "REQUEST_ID_CONFLICT"
    BUILD_CONTEXT_CONFLICT = "BUILD_CONTEXT_CONFLICT"
    SOURCE_REVISION_NOT_FOUND = "SOURCE_REVISION_NOT_FOUND"
    PROCESS_TIMEOUT = "PROCESS_TIMEOUT"
    PROCESS_CANCELLED = "PROCESS_CANCELLED"
    UNITY_PROCESS_EXIT_NONZERO = "UNITY_PROCESS_EXIT_NONZERO"
    TASK_INPUT_MISSING = "TASK_INPUT_MISSING"
    TASK_OUTPUT_MISSING = "TASK_OUTPUT_MISSING"
    TASK_UNDECLARED_OUTPUT = "TASK_UNDECLARED_OUTPUT"
    ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"
    RESULT_PACKAGE_INVALID = "RESULT_PACKAGE_INVALID"
    MANIFEST_AGGREGATION_FAILED = "MANIFEST_AGGREGATION_FAILED"
    RELEASE_TASK_SET_INCOMPLETE = "RELEASE_TASK_SET_INCOMPLETE"
    VERSION_ALLOCATION_FAILED = "VERSION_ALLOCATION_FAILED"
    VERSION_RESERVATION_CONFLICT = "VERSION_RESERVATION_CONFLICT"
    OBJECT_KEY_INVALID = "OBJECT_KEY_INVALID"
    OBJECT_UPLOAD_FAILED = "OBJECT_UPLOAD_FAILED"
    REMOTE_VERIFICATION_FAILED = "REMOTE_VERIFICATION_FAILED"
    PUBLISH_CAS_CONFLICT = "PUBLISH_CAS_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class FailureCause:
    """不持有原异常对象的单层失败原因 DTO。

    职责：
        保存异常类型名、已经基础脱敏的消息和下一层原因，形成有限深度的不可变链。

    参数：
        exception_type: 非空白异常类型名，例如 ``ConfigurationError``。
        message: 非空白且已经脱敏的异常消息。
        cause: 下一层原因；没有或达到深度上限时为 ``None``。

    返回：
        无；构造成功后得到冻结且使用 slots 的纯数据对象。

    异常、约束与副作用：
        文本为空白或 ``cause`` 类型错误时抛出 ``ValueError``。对象不得保存异常
        实例、traceback 或其他可变上下文；构造只做内存校验。
    """

    exception_type: str
    message: str
    cause: FailureCause | None = None

    def __post_init__(self) -> None:
        """校验原因类型名、消息和下一层 DTO 类型。

        无参数和返回值；读取实例字段。空白文本或非法下一层类型抛出
        ``ValueError``。方法只做内存校验，不修改字段或产生外部副作用。
        """
        exception_type = cast(object, self.exception_type)
        if not isinstance(exception_type, str) or not exception_type.strip():
            raise ValueError("exception_type 不得为空或仅空白")
        message = cast(object, self.message)
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message 不得为空或仅空白")
        cause = cast(object, self.cause)
        if cause is not None and not isinstance(cause, FailureCause):
            raise ValueError("cause 必须是 FailureCause 或 None")


@dataclass(frozen=True, slots=True)
class BuildFailure:
    """应用边界可安全传递的不可变构建失败结果。

    职责：
        聚合稳定错误码、脱敏消息、构建定位字段、重试语义、有限原因链、诊断路径
        和字符串上下文；集合字段在构造期转为确定排序的元组。

    参数：
        error_code: 正式稳定 ``ErrorCode``。
        message: 非空白脱敏失败摘要。
        build_id: 非空白构建标识。
        task_name: 可为空字符串的任务名；纯空白非法。
        step_name: 可为空字符串的步骤名；纯空白非法。
        retryable: 是否允许调用方按明确策略重试。
        cause: 可选的不可变 ``FailureCause`` 链。
        diagnostic_paths: ``Path`` 元组；按 POSIX 文本的 UTF-8 字节序排序。
        context: 字符串键值元组；按键的 UTF-8 字节序排序。

    返回：
        无；构造成功后得到冻结且使用 slots 的纯数据对象。

    异常、约束与副作用：
        空白字段、非法类型、重复规范路径或重复上下文键抛出 ``ValueError``。
        构造不访问路径内容、不写日志，也不产生外部副作用。
    """

    error_code: ErrorCode
    message: str
    build_id: str
    task_name: str = ""
    step_name: str = ""
    retryable: bool = False
    cause: FailureCause | None = None
    diagnostic_paths: tuple[Path, ...] = ()
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """校验字段并冻结诊断路径和上下文的确定顺序。

        无参数和返回值；读取构造字段，并仅通过冻结 dataclass 的初始化通道写回
        规范元组。字段非法或重复时抛出 ``ValueError``，不执行 I/O 或日志。
        """
        if not isinstance(cast(object, self.error_code), ErrorCode):
            raise ValueError("error_code 必须是 ErrorCode")
        self._validate_required_text(self.message, field_name="message")
        self._validate_required_text(self.build_id, field_name="build_id")
        self._validate_optional_name(self.task_name, field_name="task_name")
        self._validate_optional_name(self.step_name, field_name="step_name")
        if type(cast(object, self.retryable)) is not bool:
            raise ValueError("retryable 必须是 bool")
        cause = cast(object, self.cause)
        if cause is not None and not isinstance(cause, FailureCause):
            raise ValueError("cause 必须是 FailureCause 或 None")

        normalized_paths = self._normalize_diagnostic_paths(self.diagnostic_paths)
        normalized_context = self._normalize_context(self.context)
        object.__setattr__(self, "diagnostic_paths", normalized_paths)
        object.__setattr__(self, "context", normalized_context)

    @staticmethod
    def _validate_required_text(value: str, *, field_name: str) -> None:
        """校验必填字符串非空白。

        ``value`` 是待校验值，``field_name`` 用于错误消息。校验成功返回
        ``None``；非字符串或空白值抛出 ``ValueError``。函数无副作用。
        """
        value_object = cast(object, value)
        if not isinstance(value_object, str) or not value_object.strip():
            raise ValueError(f"{field_name} 不得为空或仅空白")

    @staticmethod
    def _validate_optional_name(value: str, *, field_name: str) -> None:
        """校验可选名称为字符串，并区分空字符串与纯空白。

        ``value`` 是任务或步骤名，``field_name`` 用于错误消息。空字符串表示不
        适用；非空纯空白或非字符串抛出 ``ValueError``。函数无副作用。
        """
        value_object = cast(object, value)
        if not isinstance(value_object, str):
            raise ValueError(f"{field_name} 必须是 str")
        if value_object != "" and not value_object.strip():
            raise ValueError(f"{field_name} 不得为纯空白")

    @staticmethod
    def _normalize_diagnostic_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
        """校验并按 POSIX 文本 UTF-8 字节序规范诊断路径。

        ``paths`` 必须是 ``Path`` 元组。返回稳定排序的新元组；规范 POSIX 文本
        重复或输入类型非法时抛出 ``ValueError``。不读取任何路径内容。
        """
        paths_object = cast(object, paths)
        if not isinstance(paths_object, tuple):
            raise ValueError("diagnostic_paths 必须是 Path 元组")

        path_values = cast(tuple[object, ...], paths_object)
        normalized: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for path_object in path_values:
            if not isinstance(path_object, Path):
                raise ValueError("diagnostic_paths 的元素必须是 Path")
            path_text = path_object.as_posix()
            if path_text in seen:
                raise ValueError(f"diagnostic_paths 存在重复规范路径: {path_text!r}")
            seen.add(path_text)
            normalized.append((path_text, path_object))
        normalized.sort(key=lambda item: item[0].encode("utf-8"))
        return tuple(path for _path_text, path in normalized)

    @staticmethod
    def _normalize_context(
        context: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        """校验上下文字符串键值并按键的 UTF-8 字节序规范排序。

        ``context`` 必须是二元字符串元组的元组。凭据键的值整体隐藏，其他值执行
        基础文本脱敏后，返回按键稳定排序的新元组；空白键、重复键、非字符串值或
        结构非法时抛出 ``ValueError``。函数无副作用。
        """
        context_object = cast(object, context)
        if not isinstance(context_object, tuple):
            raise ValueError("context 必须是字符串键值元组")

        entry_values = cast(tuple[object, ...], context_object)
        normalized: list[tuple[str, str]] = []
        seen_keys: set[str] = set()
        for entry_object in entry_values:
            if not isinstance(entry_object, tuple):
                raise ValueError("context 的元素必须是二元元组")
            entry_tuple = cast(tuple[object, ...], entry_object)
            if len(entry_tuple) != 2:
                raise ValueError("context 的元素必须是二元元组")
            key_object, value_object = entry_tuple
            if not isinstance(key_object, str) or not key_object.strip():
                raise ValueError("context 键不得为空或仅空白")
            if not isinstance(value_object, str):
                raise ValueError("context 值必须是 str")
            if key_object in seen_keys:
                raise ValueError(f"context 存在重复键: {key_object!r}")
            seen_keys.add(key_object)
            if _SENSITIVE_CONTEXT_KEY_PATTERN.search(key_object) is not None:
                redacted_value = "<redacted>"
            else:
                redacted_value = _redact_failure_text(value_object)
            normalized.append((key_object, redacted_value))
        normalized.sort(key=lambda item: item[0].encode("utf-8"))
        return tuple(normalized)


def _redact_failure_text(text: str) -> str:
    """对异常文本执行 Task 1 范围内的基础凭据脱敏。

    参数：
        text: 待处理的异常消息。

    返回：
        替换凭据键值、URL userinfo 和 PEM 私钥块后的新字符串。

    异常、约束与副作用：
        非字符串输入抛出 ``ValueError``。本函数只处理补充契约列出的三类模式；
        完整 argv、环境变量和 Header 脱敏留给后续任务。纯函数，无外部副作用。
    """
    text_object = cast(object, text)
    if not isinstance(text_object, str):
        raise ValueError("text 必须是 str")
    redacted = _PRIVATE_KEY_PATTERN.sub("<redacted-private-key>", text_object)
    redacted = _URL_USERINFO_PATTERN.sub(
        lambda match: f"{match.group('scheme')}<redacted>@",
        redacted,
    )
    return _CREDENTIAL_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}<redacted>",
        redacted,
    )


def _safe_exception_text(exc: BaseException) -> str:
    """安全取得异常文本，避免异常自身的 ``__str__`` 破坏失败转换。

    参数：
        exc: 待字符串化的异常对象。

    返回：
        正常情况下返回 ``str(exc)``；字符串化抛出任意 ``BaseException`` 时返回
        稳定的 ``<unprintable 异常类型名>``，不包含二次异常消息。

    异常、约束与副作用：
        本函数吞掉的仅是字符串化过程产生的二次异常，不改变原异常链。纯函数，
        不执行 I/O 或日志。
    """
    try:
        return str(exc)
    except BaseException:
        return f"<unprintable {type(exc).__name__}>"


def failure_cause_from_exception(
    exc: BaseException,
    *,
    max_depth: int = 8,
) -> FailureCause:
    """把标准异常链转换为有限深度的脱敏不可变 DTO。

    参数：
        exc: 最外层异常；转换从该异常本身开始。
        max_depth: 最大保留层数，范围为 ``1..8``，默认 ``8``。

    返回：
        最外层 ``FailureCause``；达到上限时最后一层 ``cause`` 为 ``None``。

    异常、约束与副作用：
        ``exc`` 类型错误或深度越界时抛出 ``ValueError``。每层优先沿
        ``__cause__``，没有显式原因时才读取未抑制 ``__context__``；遇到已访问
        异常对象立即截断循环。转换不保存异常实例或 traceback，不执行 I/O 和日志。
    """
    if not isinstance(cast(object, exc), BaseException):
        raise ValueError("exc 必须是 BaseException")
    depth_object = cast(object, max_depth)
    if not isinstance(depth_object, int) or isinstance(depth_object, bool):
        raise ValueError("max_depth 必须是 1..8 的整数")
    if max_depth < 1 or max_depth > 8:
        raise ValueError("max_depth 必须位于 1..8")

    serialized: list[tuple[str, str]] = []
    visited_exception_ids: set[int] = set()
    current: BaseException | None = exc
    while current is not None and len(serialized) < max_depth:
        current_id = id(current)
        if current_id in visited_exception_ids:
            break
        visited_exception_ids.add(current_id)
        exception_type = type(current).__name__
        message = _redact_failure_text(_safe_exception_text(current))
        # 无消息异常仍须生成满足 DTO 非空不变量的稳定摘要。
        if not message.strip():
            message = exception_type
        serialized.append((exception_type, message))
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None

    result: FailureCause | None = None
    for exception_type, message in reversed(serialized):
        result = FailureCause(exception_type=exception_type, message=message, cause=result)
    if result is None:
        raise ValueError("exc 必须产生至少一层失败原因")
    return result


def build_failure_from_exception(
    exc: BaseException,
    *,
    build_id: str,
    task_name: str = "",
    step_name: str = "",
    retryable: bool = False,
    error_code: ErrorCode | None = None,
    diagnostic_paths: tuple[Path, ...] = (),
    context: tuple[tuple[str, str], ...] = (),
) -> BuildFailure:
    """把异常和调用场景转换为稳定的 ``BuildFailure``。

    参数：
        exc: 最外层异常。
        build_id: 非空白构建标识。
        task_name: 可为空字符串的任务名。
        step_name: 可为空字符串的步骤名。
        retryable: 调用方声明的重试语义。
        error_code: 调用场景显式错误码；提供时优先于异常默认映射。
        diagnostic_paths: 待规范排序的诊断路径元组。
        context: 待规范排序的字符串上下文键值元组。

    返回：
        完整、脱敏且不可变的失败结果。

    异常、约束与副作用：
        字段非法时由转换函数或 ``BuildFailure`` 抛出 ``ValueError``。未显式映射时
        ``ConfigurationError`` 使用 ``CONFIG_VALIDATION_FAILED``，其余异常使用
        ``INTERNAL_ERROR``。纯函数，不执行 I/O、日志或重试。
    """
    cause = failure_cause_from_exception(exc)
    selected_code = error_code
    if selected_code is None:
        if isinstance(exc, ConfigurationError):
            selected_code = ErrorCode.CONFIG_VALIDATION_FAILED
        else:
            selected_code = ErrorCode.INTERNAL_ERROR
    return BuildFailure(
        error_code=selected_code,
        message=cause.message,
        build_id=build_id,
        task_name=task_name,
        step_name=step_name,
        retryable=retryable,
        cause=cause,
        diagnostic_paths=diagnostic_paths,
        context=context,
    )
