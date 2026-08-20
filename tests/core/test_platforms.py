"""BuildPlatform 公共平台枚举的契约测试。"""

from enum import Enum

from core.platforms import BuildPlatform


def test_build_platform_is_the_single_shared_platform_enum() -> None:
    """验证资源和客户端包体共用稳定的平台值。"""
    assert issubclass(BuildPlatform, Enum)
    assert tuple(item.value for item in BuildPlatform) == ("android", "ios", "windows")
