"""验证 ``core`` 和 ``release`` 顶级包的最小公共导入契约。

本模块只覆盖第一阶段工程骨架必须提供的直接子包可导入性，不验证尚未实现的构建、
发布或兼容协议能力。测试不访问外部系统、不写入业务文件，也不依赖旧构建目录。
"""

from pathlib import Path


def test_top_level_packages_are_directly_importable() -> None:
    """验证 ``core`` 和 ``release`` 可以作为顶级包直接导入。

    测试无参数和返回值；当任一包不存在、无法导入或仍位于旧命名空间下时由 pytest
    报告失败。除 Python 正常的模块导入缓存外，本测试不产生外部副作用。
    """
    import core
    import release

    assert core.__name__ == "core"
    assert release.__name__ == "release"
    source_root = Path(__file__).resolve().parents[1] / "src"
    assert Path(core.__file__).resolve() == source_root / "core" / "__init__.py"
    assert Path(release.__file__).resolve() == source_root / "release" / "__init__.py"
