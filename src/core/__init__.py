"""核心领域公共导出。

本包汇聚构建系统领域层的公共类型与异常。当前阶段先导出完整业务异常体系；
产物、manifest、任务 DAG 与执行器等能力按实施计划逐步加入。导入本包不会执行
构建，也不会访问 SVN、Unity、Jenkins 或 CDN。
"""

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

__all__ = [
    "ArtifactValidationError",
    "BuildError",
    "CompatibilityError",
    "ConfigurationError",
    "PlanningError",
    "PublishError",
    "SourceError",
    "ToolExecutionError",
]
