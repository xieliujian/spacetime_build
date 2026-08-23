"""SDK hook 结果的增量声明输出验证。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sdk.planner import SdkHookPlan


@dataclass(frozen=True, slots=True)
class SdkValidationReport:
    """保存计划身份、输出检查结果和稳定错误。"""

    plan_id: str
    valid: bool
    errors: tuple[str, ...]


class SdkHookValidator:
    """验证 SDK 计划声明的所有普通输出文件。"""

    def validate(self, plan: SdkHookPlan, workspace: Path) -> SdkValidationReport:
        """检查输出存在、不是链接且没有越出 workspace。"""
        if not isinstance(plan, SdkHookPlan):
            raise TypeError("plan 必须是 SdkHookPlan")
        if not isinstance(workspace, Path) or not workspace.is_absolute():
            raise ValueError("workspace 必须是绝对 Path")
        root = workspace.resolve(strict=False)
        errors: list[str] = []
        for logical_path in plan.outputs:
            path = (workspace / logical_path).resolve(strict=False)
            if root not in path.parents or path == root:
                errors.append(f"SDK 输出越出 workspace: {logical_path}")
            elif path.is_symlink() or not path.is_file():
                errors.append(f"缺少 SDK 输出: {logical_path}")
        normalized = tuple(sorted(set(errors), key=lambda item: item.encode("utf-8")))
        return SdkValidationReport(plan.plan_id, not normalized, normalized)


__all__ = ["SdkHookValidator", "SdkValidationReport"]
