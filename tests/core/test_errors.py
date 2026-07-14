"""验证 ``st.build.core`` 公开异常继承契约。

本模块确认设计文档中的完整业务异常树：七个具体异常均直接或间接继承
``BuildError``，并通过标准 ``raise ... from`` 机制保留消息与 ``__cause__``。
测试不引入重试、日志或外部系统依赖。
"""

from __future__ import annotations

import pytest

from st.build.core import (
    ArtifactValidationError,
    BuildError,
    CompatibilityError,
    ConfigurationError,
    PlanningError,
    PublishError,
    SourceError,
    ToolExecutionError,
)


def test_error_hierarchy_matches_public_contract() -> None:
    """验证七个具体异常均继承 ``BuildError``，且消息与 cause 可保留。

    测试无参数和返回值。断言：

    - ``ConfigurationError``、``PlanningError``、``SourceError``、
      ``ToolExecutionError``、``ArtifactValidationError``、``PublishError``、
      ``CompatibilityError`` 均为 ``BuildError`` 的子类；
    - 通过 ``raise ... from`` 抛出时，异常消息与 ``__cause__`` 由标准异常机制保留。

    当 ``st.build.core`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError: No module named 'st.build.core'`` 失败。除 Python
    正常的模块导入缓存外，本测试不产生外部副作用。
    """
    concrete_errors = (
        ConfigurationError,
        PlanningError,
        SourceError,
        ToolExecutionError,
        ArtifactValidationError,
        PublishError,
        CompatibilityError,
    )
    for error_type in concrete_errors:
        assert issubclass(error_type, BuildError)
        assert issubclass(error_type, Exception)

    root_cause = ValueError("底层校验失败")
    message = "配置节缺失：toolchain"
    try:
        try:
            raise root_cause
        except ValueError as exc:
            # 使用标准异常链，确认业务异常不吞掉消息与 cause。
            raise ConfigurationError(message) from exc
    except ConfigurationError as caught:
        assert str(caught) == message
        assert caught.__cause__ is root_cause
        with pytest.raises(ConfigurationError, match="配置节缺失：toolchain"):
            raise caught
