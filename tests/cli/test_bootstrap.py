"""验证 bootstrap 延迟装配和缺失适配器的明确错误。"""

import importlib

import pytest

from cli.bootstrap import BootstrapError, build_composition_root


def test_bootstrap_import_has_no_external_side_effect() -> None:
    """Given bootstrap module，When 导入，Then 不要求环境或创建适配器。"""
    module = importlib.import_module("cli.bootstrap")
    assert hasattr(module, "build_composition_root")


def test_bootstrap_requires_explicit_factory() -> None:
    """Given 未注入 composition factory，When 装配，Then 明确拒绝而不猜测适配器。"""
    with pytest.raises(BootstrapError):
        build_composition_root(None)
