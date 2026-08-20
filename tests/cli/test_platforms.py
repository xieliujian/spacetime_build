"""验证 CLI 平台文本到领域枚举的严格转换契约。"""

import pytest

from core.errors import ConfigurationError
from core.platforms import BuildPlatform
from cli.platforms import parse_build_platform


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("android", BuildPlatform.ANDROID),
        ("ios", BuildPlatform.IOS),
        ("windows", BuildPlatform.WINDOWS),
    ),
)
def test_parse_build_platform_accepts_only_canonical_values(
    value: str,
    expected: BuildPlatform,
) -> None:
    """Given 规范小写平台值，When 转换，Then 返回唯一 core 枚举。"""
    assert parse_build_platform(value) is expected


@pytest.mark.parametrize("value", ("Android", "ANDROID", "win", "macos", ""))
def test_parse_build_platform_rejects_aliases_and_case_drift(value: str) -> None:
    """Given 别名或大小写漂移，When 转换，Then 在 CLI 边界报告配置错误。"""
    with pytest.raises(ConfigurationError):
        parse_build_platform(value)


def test_parse_build_platform_does_not_declare_a_second_platform_enum() -> None:
    """Given converter module，Then 转换结果的枚举类型必须来自 core。"""
    assert parse_build_platform("android").__class__ is BuildPlatform
