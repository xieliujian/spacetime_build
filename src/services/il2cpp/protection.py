"""IL2CPP 可选保护工具的固定白名单计划。

本模块只生成受控工具请求，不实现任意 C++/metadata 重写。策略版本、工具标识、允许
处理的逻辑路径和报告路径都经过结构校验并进入不可变计划；备份、执行、变换后验证和
失败回滚必须由后续适配器通过 ProcessRunner 完成。
"""

from __future__ import annotations

from dataclasses import dataclass


def _validate_text(value: object, field_name: str) -> str:
    """校验工具计划文本非空且不含控制字符。"""
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 必须是非空且无控制字符字符串")
    return value


def _validate_member(value: object, field_name: str) -> str:
    """校验工具白名单中的相对逻辑路径。"""
    _validate_text(value, field_name)
    if not isinstance(value, str) or value.startswith("/") or "\\" in value:
        raise ValueError(f"{field_name} 必须是正斜杠相对路径")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError(f"{field_name} 含非法路径段")
    return value


@dataclass(frozen=True, slots=True)
class Il2CppProtectionPlan:
    """描述一次固定版本保护工具调用的白名单计划。

    参数：
        strategy_version: 受控保护工具策略版本。
        tool_executable: 由外部适配器解析的固定工具标识。
        allowed_files: 允许工具修改的逻辑路径，已按 UTF-8 排序。
        report_path: workspace 内的固定报告相对路径。
        arguments: 已展开的独立参数序列。

    返回：
        不可变保护计划。

    异常：
        字段类型或白名单路径不合法时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        计划不执行工具、不读取源文件、不保存秘密或机器绝对路径。
    """

    strategy_version: str
    tool_executable: str
    allowed_files: tuple[str, ...]
    report_path: str
    arguments: tuple[str, ...]

    def __post_init__(self) -> None:
        """校验保护计划字段和参数序列。"""
        _validate_text(self.strategy_version, "strategy_version")
        _validate_text(self.tool_executable, "tool_executable")
        if not isinstance(self.allowed_files, tuple) or not self.allowed_files:
            raise ValueError("allowed_files 必须是非空 tuple")
        normalized = tuple(sorted(self.allowed_files, key=lambda path: path.encode("utf-8")))
        if normalized != self.allowed_files:
            raise ValueError("allowed_files 必须按 UTF-8 路径排序")
        for path in self.allowed_files:
            _validate_member(path, "allowed_file")
        _validate_member(self.report_path, "report_path")
        if not isinstance(self.arguments, tuple) or not self.arguments:
            raise ValueError("arguments 必须是非空 tuple")
        for argument in self.arguments:
            _validate_text(argument, "argument")


class Il2CppProtectionPlanner:
    """生成固定版本保护工具调用计划，不执行保护操作。"""

    @staticmethod
    def plan(
        *,
        strategy_version: str,
        tool_executable: str,
        allowed_files: tuple[str, ...],
        report_path: str = "protection-report.json",
    ) -> Il2CppProtectionPlan:
        """校验白名单并生成固定参数数组。

        参数：
            strategy_version: 策略版本，参与后续缓存和审计身份。
            tool_executable: 固定工具标识，不得包含控制字符。
            allowed_files: 允许变换的 workspace 相对逻辑路径。
            report_path: 工具报告相对路径。

        返回：
            ``Il2CppProtectionPlan``。

        异常：
            空集合、重复路径、路径逃逸、绝对路径或文本控制字符非法时抛出
            ``TypeError`` 或 ``ValueError``。

        约束与副作用：
            纯内存操作；不启动工具、不写备份、不修改输入目录。
        """
        _validate_text(strategy_version, "strategy_version")
        _validate_text(tool_executable, "tool_executable")
        if not isinstance(allowed_files, tuple) or not allowed_files:
            raise ValueError("allowed_files 必须是非空 tuple")
        normalized: list[str] = []
        seen: set[str] = set()
        for path in allowed_files:
            normalized_path = _validate_member(path, "allowed_file")
            folded = normalized_path.casefold()
            if folded in seen:
                raise ValueError(f"allowed_files 存在重复路径: {normalized_path}")
            seen.add(folded)
            normalized.append(normalized_path)
        normalized.sort(key=lambda path: path.encode("utf-8"))
        report = _validate_member(report_path, "report_path")
        arguments = (
            tool_executable,
            "--strategy-version",
            strategy_version,
            "--files",
            *normalized,
            "--report",
            report,
        )
        return Il2CppProtectionPlan(
            strategy_version,
            tool_executable,
            tuple(normalized),
            report,
            arguments,
        )


__all__ = ["Il2CppProtectionPlan", "Il2CppProtectionPlanner"]
