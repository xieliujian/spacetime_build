"""IL2CPP 构建服务的不可变模型和后续执行入口。"""

from services.il2cpp.archive import (
    Il2CppArchive,
    Il2CppArchiveCodec,
    Il2CppArchiveEntry,
    Il2CppArchiveLimits,
)
from services.il2cpp.model import (
    Il2CppBuildRequest,
    Il2CppBuildResult,
    Il2CppExecutionMode,
    Il2CppExecutionPlan,
    Il2CppStatus,
)
from services.il2cpp.planner import Il2CppCommandPlan, Il2CppPlanner, Il2CppToolchain
from services.il2cpp.protection import Il2CppProtectionPlan, Il2CppProtectionPlanner
from services.il2cpp.validator import Il2CppOutputValidator, Il2CppValidationReport

__all__ = [
    "Il2CppArchive",
    "Il2CppArchiveCodec",
    "Il2CppArchiveEntry",
    "Il2CppArchiveLimits",
    "Il2CppBuildRequest",
    "Il2CppBuildResult",
    "Il2CppExecutionMode",
    "Il2CppExecutionPlan",
    "Il2CppStatus",
    "Il2CppCommandPlan",
    "Il2CppPlanner",
    "Il2CppToolchain",
    "Il2CppProtectionPlan",
    "Il2CppProtectionPlanner",
    "Il2CppOutputValidator",
    "Il2CppValidationReport",
]
