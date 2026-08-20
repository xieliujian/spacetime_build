"""把业务异常映射为稳定 CLI 退出码。"""

from core.errors import (
    ArtifactValidationError,
    BuildError,
    CompatibilityError,
    ConfigurationError,
    PlanningError,
    PublishError,
    SourceError,
    ToolExecutionError,
)
from application.operations import OperationError


def exit_code_for(error: BaseException) -> int:
    """返回计划定义的业务错误退出码。

    参数：
        error: 捕获的业务或未知异常。

    返回：
        配置 2、规划 3、源码 4、工具 5、产物 6、发布 7、兼容 8、取消 9，
        未分类异常 10。

    异常与副作用：
        不抛出原始异常、不输出 traceback、不读写外部状态。
    """
    if isinstance(error, ConfigurationError):
        return 2
    if isinstance(error, PlanningError):
        return 3
    if isinstance(error, SourceError):
        return 4
    if isinstance(error, ToolExecutionError):
        return 5
    if isinstance(error, ArtifactValidationError):
        return 6
    if isinstance(error, PublishError):
        return 7
    if isinstance(error, CompatibilityError):
        return 8
    if isinstance(error, OperationError):
        return 9
    if isinstance(error, BuildError):
        return 10
    return 10


__all__ = ["exit_code_for"]
