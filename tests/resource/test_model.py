"""资源输入与类型模型的契约测试。"""

import pytest

from core.platforms import BuildPlatform
from release.entries import ResourceVariant
from resource.model import ResourceBuildInput, ResourceKind, ResourceProjectRole


def _input(**overrides: object) -> ResourceBuildInput:
    """构造测试用合法资源输入。"""
    values: dict[str, object] = {
        "source_snapshot_id": "source-1",
        "resource_snapshot_id": "resource-1",
        "platform": BuildPlatform.WINDOWS,
        "variant": ResourceVariant.MAIN,
        "rule_version": "rules-1",
        "baseline_manifest_id": None,
    }
    values.update(overrides)
    return ResourceBuildInput(**values)  # type: ignore[arg-type]


def test_resource_model_uses_release_variant_and_stable_kinds() -> None:
    """验证资源模型不重复声明 ResourceVariant，且资源种类值稳定。"""
    value = _input()
    assert value.variant is ResourceVariant.MAIN
    assert tuple(item.value for item in ResourceKind) == (
        "config",
        "shader_variant",
        "shader_bundle",
        "scene",
        "map",
        "character",
        "texture",
        "ui",
        "particle",
        "audio",
        "video",
        "lua",
    )
    assert ResourceProjectRole.RESOURCE.value == "resource"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_snapshot_id", ""),
        ("resource_snapshot_id", "resource/escape"),
        ("rule_version", "rules\n1"),
    ],
)
def test_resource_input_rejects_unstable_snapshot_identity(field: str, value: str) -> None:
    """验证快照与规则身份不能携带空值或路径/换行语义。"""
    with pytest.raises(ValueError):
        _input(**{field: value})


def test_resource_input_rejects_wrong_types_and_invalid_baseline() -> None:
    """验证资源输入的变体、平台和基线摘要均有运行时防御校验。"""
    with pytest.raises(TypeError):
        _input(platform="windows")
    with pytest.raises(TypeError):
        _input(variant="main")
    with pytest.raises(ValueError):
        _input(baseline_manifest_id="not-a-digest")
