"""声明式 SDK 扩展描述、catalog、平台计划和受控 apply/验证入口的顶级包。

SDK 包不导入或执行渠道脚本、shell、Gradle、Ruby 或签名工具；具体平台 apply 只消费
公开计划，在隔离工作区执行白名单文件操作并用 validator 检查普通产物。
"""

from sdk.model import SdkDescriptor, SdkOperation, SdkOperationKind, SdkStage
from sdk.catalog import SdkCatalog
from sdk.planner import SdkHookPlan, SdkHookPlanner
from sdk.apply import SdkApplyResult, SdkHookApplier
from sdk.android import AndroidSdkMapper, AndroidSdkPlan
from sdk.ios import IosSdkMapper, IosSdkPlan
from sdk.validator import SdkHookValidator, SdkValidationReport
from sdk.windows import WindowsSdkMapper, WindowsSdkPlan

__all__ = [
    "SdkCatalog",
    "SdkDescriptor",
    "SdkHookPlan",
    "SdkHookPlanner",
    "SdkApplyResult",
    "SdkHookApplier",
    "AndroidSdkMapper",
    "AndroidSdkPlan",
    "IosSdkMapper",
    "IosSdkPlan",
    "SdkHookValidator",
    "SdkValidationReport",
    "WindowsSdkMapper",
    "WindowsSdkPlan",
    "SdkOperation",
    "SdkOperationKind",
    "SdkStage",
]
