"""验证源码与测试符号均具备含中文的 docstring。

本模块提供临时样例扫描与全仓门禁两类用例，确保模块、类、函数、异步函数和方法
不会遗漏中文 docstring。测试只读取本地 Python 文件，不访问外部系统，也不写入
业务产物。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 覆盖常用汉字区；用于判定 docstring 是否包含至少一个中文字符。
_CHINESE_CHARACTER_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _docstring_contains_chinese(docstring: str | None) -> bool:
    """判断 docstring 是否非空且至少包含一个中文字符。

    参数：
        docstring: ``ast.get_docstring`` 返回的文档字符串；缺失时为 ``None``。

    返回：
        文档非空且匹配到至少一个汉字时返回 ``True``，否则返回 ``False``。

    异常：
        无；输入为 ``None`` 或空串时直接返回 ``False``，不抛出异常。

    约束与副作用：
        无副作用；只做只读正则匹配，不修改入参。
    """
    if not docstring:
        return False
    return _CHINESE_CHARACTER_PATTERN.search(docstring) is not None


def _find_chinese_documentation_violations(
    paths: tuple[Path, ...],
) -> list[tuple[Path, str, int]]:
    """扫描给定 Python 文件，报告缺少中文 docstring 的符号。

    参数：
        paths: 待扫描的 ``.py`` 文件路径元组；允许为空。

    返回：
        违规列表，每项为 ``(文件路径, 符号名, 行号)``。模块符号名为 ``<module>``，
        行号取 ``1``；类、函数、异步函数和方法使用 AST 节点名与 ``lineno``。
        结果按扫描顺序追加，同一文件内按 AST 遍历顺序排列。

    异常：
        文件无法按 UTF-8 读取或语法无法解析时，直接抛出底层 ``OSError`` /
        ``UnicodeDecodeError`` / ``SyntaxError``，由调用方或 pytest 暴露。

    约束与副作用：
        使用 ``ast.parse`` 与 ``ast.get_docstring``，通过 ``ast.walk`` 覆盖嵌套
        方法与嵌套函数，避免漏检。只读取文件内容，不写入磁盘。
    """
    violations: list[tuple[Path, str, int]] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        # 模块节点没有 lineno，统一以文件首行作为报告位置。
        if not _docstring_contains_chinese(ast.get_docstring(tree)):
            violations.append((path, "<module>", 1))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if not _docstring_contains_chinese(ast.get_docstring(node)):
                    violations.append((path, node.name, node.lineno))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not _docstring_contains_chinese(ast.get_docstring(node)):
                    violations.append((path, node.name, node.lineno))
    return violations


def test_scanner_reports_module_class_function_and_method_without_chinese_docstrings(
    tmp_path: Path,
) -> None:
    """验证扫描器能报告缺失中文 docstring 的模块、类、函数和方法。

    参数：
        tmp_path: pytest 提供的临时目录，用于写入故意缺少中文 docstring 的样例文件。

    返回：
        无返回值；通过断言确认违规项以文件、符号和行号形式报告。

    异常：
        扫描器未实现、遗漏嵌套方法或报告格式不符合约定时由断言或 NameError 失败。

    约束与副作用：
        仅向临时目录写入样例文件，不修改仓库源码树。
    """
    sample_path = tmp_path / "sample_missing_docs.py"
    sample_path.write_text(
        "\n".join(
            [
                '"""English only module docstring."""',
                "",
                "",
                "class SampleClass:",
                '    """English only class docstring."""',
                "",
                "    def method_without_chinese(self) -> None:",
                '        """English only method docstring."""',
                "        return None",
                "",
                "",
                "def function_without_chinese() -> None:",
                '    """English only function docstring."""',
                "    return None",
                "",
                "",
                "async def async_function_without_chinese() -> None:",
                '    """English only async function docstring."""',
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )

    violations = _find_chinese_documentation_violations((sample_path,))

    reported_symbols = {symbol for _path, symbol, _lineno in violations}
    assert "<module>" in reported_symbols
    assert "SampleClass" in reported_symbols
    assert "method_without_chinese" in reported_symbols
    assert "function_without_chinese" in reported_symbols
    assert "async_function_without_chinese" in reported_symbols

    for path, symbol, lineno in violations:
        assert path == sample_path
        assert isinstance(symbol, str) and symbol
        assert isinstance(lineno, int) and lineno >= 1


def test_all_source_and_test_symbols_have_chinese_docstrings() -> None:
    """验证仓库内全部源码与测试符号均有含中文的非空 docstring。

    参数：
        无。

    返回：
        无返回值；扫描结果为空列表即表示门禁通过。

    异常：
        任一模块、类、函数、异步函数或方法缺少含中文的 docstring 时断言失败，
        失败信息列出文件、符号与行号。

    约束与副作用：
        只读取 ``src/**/*.py`` 与 ``tests/**/*.py``，不修改任何文件。
    """
    source_paths = tuple(sorted((PROJECT_ROOT / "src").rglob("*.py")))
    test_paths = tuple(sorted((PROJECT_ROOT / "tests").rglob("*.py")))
    paths = source_paths + test_paths
    assert paths, "预期至少发现一个待扫描的 Python 文件"

    violations = _find_chinese_documentation_violations(paths)
    assert violations == [], "以下符号缺少含中文的 docstring：\n" + "\n".join(
        f"{path}:{lineno}: {symbol}" for path, symbol, lineno in violations
    )
