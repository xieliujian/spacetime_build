"""验证业务异常到稳定 CLI 退出码的映射。"""

from application.operations import OperationError
from cli.exit_codes import exit_code_for
from core.errors import (
    ArtifactValidationError,
    CompatibilityError,
    ConfigurationError,
    PlanningError,
    PublishError,
    SourceError,
    ToolExecutionError,
)


def test_exit_code_mapping_keeps_public_contract() -> None:
    """Given 各层 BuildError，Then 映射为计划定义的 2..10。"""
    cases = {
        ConfigurationError("x"): 2,
        PlanningError("x"): 3,
        SourceError("x"): 4,
        ToolExecutionError("x"): 5,
        ArtifactValidationError("x"): 6,
        PublishError("x"): 7,
        CompatibilityError("x"): 8,
        OperationError("x"): 9,
        RuntimeError("x"): 10,
    }
    for error, expected in cases.items():
        assert exit_code_for(error) == expected
