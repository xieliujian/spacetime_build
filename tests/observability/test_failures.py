"""验证稳定错误码、不可变失败结果、异常链转换与基础脱敏。

本模块覆盖正式发布错误模型的最小公开契约。测试不访问文件内容、不写日志，
也不调用任何外部系统。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from time import perf_counter

import pytest

from core.errors import ConfigurationError
from observability import (
    BuildFailure,
    ErrorCode,
    FailureCause,
    build_failure_from_exception,
    failure_cause_from_exception,
)


def test_error_code_matches_formal_design_exactly() -> None:
    """验证公开错误码精确覆盖正式设计，并额外保留兜底内部错误码。

    无参数和返回值；成员缺失、多余或不再兼容 ``str`` 时由断言失败。
    测试只读取枚举成员，无外部副作用。
    """
    expected_codes = {
        "CONFIG_VALIDATION_FAILED",
        "REQUEST_ID_CONFLICT",
        "BUILD_CONTEXT_CONFLICT",
        "SOURCE_REVISION_NOT_FOUND",
        "PROCESS_TIMEOUT",
        "PROCESS_CANCELLED",
        "UNITY_PROCESS_EXIT_NONZERO",
        "TASK_INPUT_MISSING",
        "TASK_OUTPUT_MISSING",
        "TASK_UNDECLARED_OUTPUT",
        "ARTIFACT_HASH_MISMATCH",
        "RESULT_PACKAGE_INVALID",
        "MANIFEST_AGGREGATION_FAILED",
        "RELEASE_TASK_SET_INCOMPLETE",
        "VERSION_ALLOCATION_FAILED",
        "VERSION_RESERVATION_CONFLICT",
        "OBJECT_KEY_INVALID",
        "OBJECT_UPLOAD_FAILED",
        "REMOTE_VERIFICATION_FAILED",
        "PUBLISH_CAS_CONFLICT",
        "INTERNAL_ERROR",
    }

    assert {code.name for code in ErrorCode} == expected_codes
    assert {code.value for code in ErrorCode} == expected_codes
    assert all(isinstance(code, str) for code in ErrorCode)


def test_build_failure_is_frozen_slotted_and_stably_normalized() -> None:
    """验证失败结果不可变，且诊断路径和上下文按 UTF-8 字节序稳定排序。

    无参数和返回值；修改字段应抛出冻结异常，对象不提供 ``__dict__``。
    测试只创建内存值，不访问诊断路径。
    """
    cause = FailureCause(exception_type="RuntimeError", message="失败")
    failure = BuildFailure(
        error_code=ErrorCode.TASK_OUTPUT_MISSING,
        message="任务没有生成声明的输出",
        build_id="build-1052",
        task_name="scene",
        step_name="verify",
        cause=cause,
        diagnostic_paths=(Path("中/日志.log"), Path("a/report.txt")),
        context=(("阶段", "校验"), ("artifact", "scene/a.assetbundle")),
    )

    assert failure.diagnostic_paths == (Path("a/report.txt"), Path("中/日志.log"))
    assert failure.context == (("artifact", "scene/a.assetbundle"), ("阶段", "校验"))
    assert failure.cause is cause
    assert not hasattr(failure, "__dict__")
    with pytest.raises(FrozenInstanceError):
        failure.message = "被修改"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"message": ""}, "message"),
        ({"message": "   "}, "message"),
        ({"build_id": ""}, "build_id"),
        ({"build_id": "\t"}, "build_id"),
        ({"task_name": " "}, "task_name"),
        ({"step_name": "\n"}, "step_name"),
    ],
)
def test_build_failure_rejects_invalid_empty_text_fields(
    overrides: dict[str, str],
    expected_message: str,
) -> None:
    """验证必填文本拒绝空白，可选名称只允许真正的空字符串。

    ``overrides`` 提供单个非法字段，``expected_message`` 指定预期字段名。
    非法输入应抛出 ``ValueError``；函数无返回值和外部副作用。
    """
    arguments = {
        "error_code": ErrorCode.INTERNAL_ERROR,
        "message": "未知错误",
        "build_id": "build-1",
        "task_name": "",
        "step_name": "",
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=expected_message):
        BuildFailure(**arguments)  # type: ignore[arg-type]


def test_build_failure_allows_empty_optional_names_and_rejects_unstable_collections() -> None:
    """验证可选名称空值合法，并拒绝重复路径、非法上下文键值。

    无参数和返回值；重复路径、空白或重复键、非字符串值均应抛出
    ``ValueError``。测试仅构造内存对象，无 I/O 副作用。
    """
    failure = BuildFailure(
        error_code=ErrorCode.INTERNAL_ERROR,
        message="未知错误",
        build_id="build-1",
    )
    assert failure.task_name == ""
    assert failure.step_name == ""
    assert failure.diagnostic_paths == ()
    assert failure.context == ()

    with pytest.raises(ValueError, match="diagnostic_paths.*重复"):
        BuildFailure(
            error_code=ErrorCode.INTERNAL_ERROR,
            message="未知错误",
            build_id="build-1",
            diagnostic_paths=(Path("logs/a.log"), Path("logs\\a.log")),
        )

    invalid_contexts: tuple[tuple[tuple[object, object], ...], ...] = (
        (("", "value"),),
        (("   ", "value"),),
        (("key", "one"), ("key", "two")),
        (("key", 1),),
    )
    for invalid_context in invalid_contexts:
        with pytest.raises(ValueError, match="context"):
            BuildFailure(
                error_code=ErrorCode.INTERNAL_ERROR,
                message="未知错误",
                build_id="build-1",
                context=invalid_context,  # type: ignore[arg-type]
            )


def test_build_failure_redacts_sensitive_context_values() -> None:
    """验证凭据键隐藏整个值，普通键仍对值文本执行基础脱敏。

    无参数和返回值；构造含 ``token`` 键及普通详情键的失败结果。秘密不得保留在
    规范上下文中，测试只处理内存字符串，无 I/O 或日志副作用。
    """
    failure = BuildFailure(
        error_code=ErrorCode.INTERNAL_ERROR,
        message="未知错误",
        build_id="build-1",
        context=(
            ("token", "context-secret"),
            ("detail", "password=context-password"),
        ),
    )

    assert failure.context == (
        ("detail", "password=<redacted>"),
        ("token", "<redacted>"),
    )


def test_failure_cause_is_frozen_slotted_and_rejects_empty_fields() -> None:
    """验证异常原因 DTO 不可变，且类型名与消息不得为空白。

    无参数和返回值；空字段抛出 ``ValueError``，字段修改抛出冻结异常。
    DTO 没有可变 ``__dict__``，测试无副作用。
    """
    cause = FailureCause(exception_type="ValueError", message="输入无效")
    assert not hasattr(cause, "__dict__")
    with pytest.raises(FrozenInstanceError):
        cause.message = "被修改"  # type: ignore[misc]

    for field_name in ("exception_type", "message"):
        with pytest.raises(ValueError, match=field_name):
            FailureCause(
                exception_type=" " if field_name == "exception_type" else "ValueError",
                message=" " if field_name == "message" else "输入无效",
            )


def _exception_chain(depth: int) -> BaseException:
    """构造指定层数的显式异常原因链供深度测试使用。

    ``depth`` 是正整数层数；返回最外层异常，每层 ``__cause__`` 指向下一层。
    非正输入抛出 ``ValueError``；只创建异常对象，不抛出且无 I/O。
    """
    if depth <= 0:
        raise ValueError("depth 必须为正数")
    current: BaseException = ValueError("第 1 层")
    for index in range(2, depth + 1):
        outer = RuntimeError(f"第 {index} 层")
        outer.__cause__ = current
        current = outer
    return current


class _UnprintableError(RuntimeError):
    """测试用异常，其字符串化过程会再次抛出异常。"""

    def __str__(self) -> str:
        """模拟不可靠异常的字符串化失败，且二次消息含敏感内容。"""
        raise LookupError("secondary-secret")


def test_exception_conversion_survives_unprintable_exception() -> None:
    """验证异常字符串化失败时返回稳定占位符且不泄漏二次异常消息。

    无参数和返回值；自定义 ``__str__`` 抛出异常，转换仍须成功。结果只含原异常
    类型名，不得包含二次异常文本；测试不执行 I/O 或日志。
    """
    converted = failure_cause_from_exception(_UnprintableError())

    assert converted.message == "<unprintable _UnprintableError>"
    assert "secondary-secret" not in converted.message


def test_exception_conversion_limits_depth_and_never_keeps_exception_objects() -> None:
    """验证异常链最多保留八层，且 DTO 只含字符串与下一层原因。

    无参数和返回值；``max_depth`` 越界应抛出 ``ValueError``。转换结果不保存
    traceback 或异常实例，也不产生 I/O 与日志副作用。
    """
    converted = failure_cause_from_exception(_exception_chain(10))

    observed: list[FailureCause] = []
    current: FailureCause | None = converted
    while current is not None:
        observed.append(current)
        assert isinstance(current.exception_type, str)
        assert isinstance(current.message, str)
        assert not isinstance(current.cause, BaseException)
        current = current.cause

    assert len(observed) == 8
    assert observed[0].message == "第 10 层"
    assert observed[-1].message == "第 3 层"

    for invalid_depth in (0, 9):
        with pytest.raises(ValueError, match="max_depth"):
            failure_cause_from_exception(ValueError("失败"), max_depth=invalid_depth)


def test_exception_conversion_prefers_explicit_cause_then_unsuppressed_context() -> None:
    """验证转换优先显式 cause，并仅在未抑制时退回隐式 context。

    无参数和返回值；手工建立标准异常链并断言下一层消息。测试仅读取异常链
    字段，不保存原异常或产生外部副作用。
    """
    outer = RuntimeError("外层")
    outer.__cause__ = ValueError("显式原因")
    outer.__context__ = LookupError("隐式上下文")
    converted = failure_cause_from_exception(outer)
    assert converted.cause is not None
    assert converted.cause.message == "显式原因"

    contextual = RuntimeError("上下文外层")
    contextual.__context__ = LookupError("隐式上下文")
    converted_context = failure_cause_from_exception(contextual)
    assert converted_context.cause is not None
    assert converted_context.cause.message == "隐式上下文"

    suppressed = RuntimeError("被抑制")
    suppressed.__context__ = LookupError("不得保留")
    suppressed.__suppress_context__ = True
    assert failure_cause_from_exception(suppressed).cause is None


def test_exception_conversion_truncates_self_and_two_node_cycles() -> None:
    """验证异常链遇到已访问对象立即截断，不以重复节点填满深度上限。

    无参数和返回值；分别构造自引用与双节点 ``__cause__`` 环。转换结果只保留
    每个异常对象一次，并以 ``None`` 结束；测试无 I/O 或日志副作用。
    """
    self_referencing = RuntimeError("自引用")
    self_referencing.__cause__ = self_referencing
    converted_self = failure_cause_from_exception(self_referencing)
    assert converted_self.message == "自引用"
    assert converted_self.cause is None

    first = RuntimeError("第一节点")
    second = ValueError("第二节点")
    first.__cause__ = second
    second.__cause__ = first
    converted_pair = failure_cause_from_exception(first)
    assert converted_pair.message == "第一节点"
    assert converted_pair.cause is not None
    assert converted_pair.cause.message == "第二节点"
    assert converted_pair.cause.cause is None


def test_exception_conversion_redacts_basic_credentials_urls_and_private_keys() -> None:
    """验证异常消息中的键值凭据、URL userinfo 与私钥块被基础脱敏。

    无参数和返回值；输入包含代表性秘密，原始秘密不得出现在 DTO 消息中。
    测试仅转换内存字符串，不写日志或文件。
    """
    message = "\n".join(
        (
            "password=hunter2",
            "TOKEN: abc123",
            "secret=value",
            "Authorization: Bearer credential",
            "api_key=key-value",
            "remote=https://alice:p4ss@example.com/path",
            "-----BEGIN RSA PRIVATE KEY-----",
            "private-material",
            "-----END RSA PRIVATE KEY-----",
        )
    )

    converted = failure_cause_from_exception(RuntimeError(message))

    for secret in (
        "hunter2",
        "abc123",
        "value",
        "Bearer credential",
        "key-value",
        "alice",
        "p4ss",
        "private-material",
    ):
        assert secret not in converted.message
    assert converted.message.count("<redacted>") >= 6
    assert "https://<redacted>@example.com/path" in converted.message
    assert "<redacted-private-key>" in converted.message


def test_exception_conversion_redacts_compound_keys_and_user_only_urls() -> None:
    """验证复合凭据键和只有用户名的 URL userinfo 仍被完整脱敏。

    无参数和返回值；消息包含 ``ACCESS_TOKEN`` 赋值与 ``user@host`` URL。转换后
    不得保留凭据值或用户名；测试只处理内存字符串，无外部副作用。
    """
    converted = failure_cause_from_exception(
        RuntimeError("ACCESS_TOKEN=compound-secret\nremote=https://token@example.com")
    )

    assert converted.message == ("ACCESS_TOKEN=<redacted>\nremote=https://<redacted>@example.com")
    assert "compound-secret" not in converted.message


def test_failure_text_redaction_handles_long_plain_text_with_linear_cost() -> None:
    """验证长无 URL 文本保持原样，且脱敏耗时不会呈二次方增长。

    无参数和返回值；输入至少 50,000 个连续 scheme 字符，要求当前机器在宽松的
    2 秒内处理完毕。超时或文本变化时断言失败；测试只计算内存字符串和耗时，
    不执行 I/O、日志或外部调用。
    """
    plain_text = "A" * 50_000

    started_at = perf_counter()
    redacted = failure_cause_from_exception(RuntimeError(plain_text)).message
    elapsed_seconds = perf_counter() - started_at

    assert redacted == plain_text
    assert elapsed_seconds < 2.0, (
        f"50,000 字符普通文本脱敏应近似线性，实际耗时 {elapsed_seconds:.6f} 秒"
    )


def test_build_failure_mapping_uses_configuration_default_explicit_code_and_fallback() -> None:
    """验证配置默认码、调用场景显式码优先级和内部错误兜底。

    无参数和返回值；转换不同异常并断言完整 ``BuildFailure`` 字段。映射为纯
    函数，不执行日志或 I/O；最外层消息与原因 DTO 都必须脱敏。
    """
    configuration = build_failure_from_exception(
        ConfigurationError("token=config-token"),
        build_id="build-config",
    )
    assert configuration.error_code is ErrorCode.CONFIG_VALIDATION_FAILED
    assert configuration.message == "token=<redacted>"
    configuration_cause = configuration.cause
    assert configuration_cause is not None
    assert configuration_cause.exception_type == "ConfigurationError"
    assert configuration_cause.message == configuration.message

    explicit = build_failure_from_exception(
        ConfigurationError("配置源不存在"),
        build_id="build-source",
        task_name="config",
        step_name="load",
        retryable=True,
        error_code=ErrorCode.SOURCE_REVISION_NOT_FOUND,
        diagnostic_paths=(Path("z.log"), Path("a.log")),
        context=(("revision", "r42"), ("provider", "svn")),
    )
    assert explicit.error_code is ErrorCode.SOURCE_REVISION_NOT_FOUND
    assert explicit.task_name == "config"
    assert explicit.step_name == "load"
    assert explicit.retryable is True
    assert explicit.diagnostic_paths == (Path("a.log"), Path("z.log"))
    assert explicit.context == (("provider", "svn"), ("revision", "r42"))

    fallback = build_failure_from_exception(RuntimeError("未知失败"), build_id="build-unknown")
    assert fallback.error_code is ErrorCode.INTERNAL_ERROR
    assert fallback.message == "未知失败"
    fallback_cause = fallback.cause
    assert fallback_cause is not None
    assert fallback_cause.exception_type == "RuntimeError"
