"""Android native symbols 确定性归档测试。"""

from pathlib import Path

from package.platforms.android.symbols import AndroidSymbolCollector


def test_symbol_collector_archives_expected_abi_files_deterministically(tmp_path: Path) -> None:
    """验证 ABI 符号清单和 ZIP 字节稳定。"""
    root = tmp_path / "symbols"
    (root / "arm64-v8a").mkdir(parents=True)
    (root / "arm64-v8a" / "libil2cpp.so").write_bytes(b"so")
    first = AndroidSymbolCollector.collect(root, ("arm64-v8a",))
    second = AndroidSymbolCollector.collect(root, ("arm64-v8a",))
    assert first.content == second.content
    assert "arm64-v8a/libil2cpp.so" in first.files
