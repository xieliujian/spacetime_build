"""类型化 Unity 操作契约测试。"""

import pytest

from resource.unity_operations import (
    LegacyUnityFlagMapper,
    UnityOperation,
    UnityProjectRole,
)


def test_unity_operation_is_deterministic_and_maps_legacy_flag() -> None:
    """验证操作参数唯一、输出根固定，并集中映射旧 BUILD flag。"""
    operation = UnityOperation(
        name="build_scene",
        project_role=UnityProjectRole.RESOURCE,
        arguments=(("scene", "Town"), ("platform", "windows")),
        expected_output_roots=("scene", "manifest"),
    )
    assert operation.arguments == tuple(sorted(operation.arguments))
    assert LegacyUnityFlagMapper.arguments_for(operation) == (
        "-BUILD_SCENE",
        "-platform",
        "windows",
        "-scene",
        "Town",
    )


def test_unity_operation_rejects_duplicate_arguments_and_unsafe_roots() -> None:
    """验证重复参数与越界输出根在构造期失败。"""
    with pytest.raises(ValueError):
        UnityOperation(
            name="build",
            project_role=UnityProjectRole.RESOURCE,
            arguments=(("x", "1"), ("x", "2")),
            expected_output_roots=("out",),
        )
    with pytest.raises(ValueError):
        UnityOperation(
            name="build",
            project_role=UnityProjectRole.RESOURCE,
            arguments=(),
            expected_output_roots=("../out",),
        )
