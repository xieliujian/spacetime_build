"""分包 bit flag 规划测试。"""

import pytest

from release.entries import ResourceVariant
from release.subpackage import SubpackagePlanner


def test_subpackage_install_and_base_have_priority() -> None:
    """验证 INSTALL 优先于 BASE，普通分包使用 1..31 bit。"""
    assert SubpackagePlanner.flag_for_membership(install=True, base=True, package_ids=(1,)) == 0
    assert SubpackagePlanner.flag_for_membership(install=False, base=True, package_ids=(1,)) == 0
    assert SubpackagePlanner.flag_for_membership(install=False, base=False, package_ids=(1, 3)) == 5
    assert SubpackagePlanner.flag_for_membership(
        install=False, base=False, package_ids=(30, 31)
    ) == (1 << 29) | (1 << 30)


def test_subpackage_rejects_invalid_ids_and_low_variant_is_explicit() -> None:
    """验证非法 bit 和低清不能通过隐式文件名猜测。"""
    with pytest.raises(ValueError):
        SubpackagePlanner.flag_for_membership(package_ids=(0,))
    with pytest.raises(ValueError):
        SubpackagePlanner.flag_for_membership(package_ids=(32,))
    assert SubpackagePlanner.flag_for_variant(ResourceVariant.LOW, package_ids=(2,)) == 2
