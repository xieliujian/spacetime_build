"""声明式 SDK 扩展描述、catalog 和平台计划的顶级包。

SDK 包只提供不可变数据和结构化计划，不导入或执行渠道脚本、shell、Gradle、Ruby 或
签名工具；具体平台 apply 由受控适配器消费公开计划。
"""

from sdk.model import SdkDescriptor, SdkOperation, SdkOperationKind, SdkStage
from sdk.catalog import SdkCatalog
from sdk.planner import SdkHookPlan, SdkHookPlanner

__all__ = [
    "SdkCatalog",
    "SdkDescriptor",
    "SdkHookPlan",
    "SdkHookPlanner",
    "SdkOperation",
    "SdkOperationKind",
    "SdkStage",
]
