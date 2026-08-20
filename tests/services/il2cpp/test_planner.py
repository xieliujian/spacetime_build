"""IL2CPP 执行计划的模板、工具链和缓存命中测试。"""

from pathlib import Path
from typing import cast

import pytest

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from services.il2cpp.model import Il2CppBuildRequest, Il2CppExecutionMode
from services.il2cpp.planner import Il2CppPlanner, Il2CppToolchain


def _request(mode: Il2CppExecutionMode) -> Il2CppBuildRequest:
    """构造固定测试请求。"""
    digest = "a" * 64
    return Il2CppBuildRequest(
        "request-1",
        BuildPlatform.ANDROID,
        "arm64-v8a",
        BlobRef(f"blobs/{digest}", digest, 10),
        "2022.3.62f2",
        "toolchain-digest",
        mode,
        None,
    )


def _toolchain(**overrides: object) -> Il2CppToolchain:
    """构造固定 Unity 工具链描述。"""
    values: dict[str, object] = {
        "unity_version": "2022.3.62f2",
        "unity_executable": "Unity",
        "command_template_version": "unity-il2cpp-v1",
        "environment": (("UNITY_LICENSE_MODE", "batch"),),
        "toolchain_versions": (("unity", "2022.3.62f2"), ("ndk", "25")),
    }
    values.update(overrides)
    return Il2CppToolchain(
        unity_version=cast(str, values["unity_version"]),
        unity_executable=cast(str, values["unity_executable"]),
        command_template_version=cast(str, values["command_template_version"]),
        environment=cast(tuple[tuple[str, str], ...], values["environment"]),
        toolchain_versions=cast(tuple[tuple[str, str], ...], values["toolchain_versions"]),
    )


def test_planner_generates_fixed_local_command_and_cache_identity(tmp_path: Path) -> None:
    """Given local 工具链，When plan，Then 参数、workspace 和 cache key 确定。"""
    plan = Il2CppPlanner.plan(
        _request(Il2CppExecutionMode.LOCAL),
        tmp_path,
        toolchain=_toolchain(),
        cache_hit=False,
    )

    assert plan.cache_hit is False
    assert plan.execution.workspace == tmp_path
    assert plan.arguments[:5] == ("Unity", "-batchmode", "-nographics", "-quit", "-executeMethod")
    assert "-architecture" in plan.arguments
    assert plan.execution.output_locator == "blobs/" + plan.execution.cache_key


def test_planner_keeps_remote_mode_and_cache_hit_explicit(tmp_path: Path) -> None:
    """Given remote cache 命中，When plan，Then 不改变请求模式且标出命中。"""
    plan = Il2CppPlanner.plan(
        _request(Il2CppExecutionMode.REMOTE),
        tmp_path,
        toolchain=_toolchain(),
        cache_hit=True,
    )

    assert plan.cache_hit is True
    assert plan.execution.request.mode is Il2CppExecutionMode.REMOTE


def test_planner_rejects_unknown_template_and_missing_unity_toolchain(tmp_path: Path) -> None:
    """验证未知模板和 Unity 版本不匹配不会生成可执行计划。"""
    with pytest.raises(ValueError, match="模板"):
        Il2CppPlanner.plan(
            _request(Il2CppExecutionMode.LOCAL),
            tmp_path,
            toolchain=_toolchain(command_template_version="unknown"),
        )
    with pytest.raises(ValueError, match="Unity"):
        Il2CppPlanner.plan(
            _request(Il2CppExecutionMode.LOCAL),
            tmp_path,
            toolchain=_toolchain(unity_version="2021.3.0f1"),
        )
