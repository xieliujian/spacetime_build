"""IL2CPP 可选保护策略计划测试。"""

import pytest

from services.il2cpp.protection import Il2CppProtectionPlanner


def test_protection_plan_keeps_fixed_tool_and_allowed_files_deterministic() -> None:
    """Given 白名单文件，When plan，Then 参数按路径排序且不含任意脚本。"""
    plan = Il2CppProtectionPlanner.plan(
        strategy_version="protect-v1",
        tool_executable="il2cpp-protect",
        allowed_files=("metadata/global-metadata.dat", "lib/arm64-v8a/libil2cpp.so"),
    )

    assert plan.allowed_files == (
        "lib/arm64-v8a/libil2cpp.so",
        "metadata/global-metadata.dat",
    )
    assert plan.arguments == (
        "il2cpp-protect",
        "--strategy-version",
        "protect-v1",
        "--files",
        "lib/arm64-v8a/libil2cpp.so",
        "metadata/global-metadata.dat",
        "--report",
        "protection-report.json",
    )


def test_protection_plan_rejects_unsafe_or_duplicate_paths() -> None:
    """验证保护工具不会接收绝对路径、路径逃逸和大小写重复成员。"""
    with pytest.raises(ValueError):
        Il2CppProtectionPlanner.plan(
            strategy_version="protect-v1",
            tool_executable="tool",
            allowed_files=("../secret.bin",),
        )
    with pytest.raises(ValueError):
        Il2CppProtectionPlanner.plan(
            strategy_version="protect-v1",
            tool_executable="tool",
            allowed_files=("A.bin", "a.bin"),
        )
