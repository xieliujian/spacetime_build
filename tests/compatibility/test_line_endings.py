"""验证兼容协议显式 LF/CRLF 字节策略。"""

from compatibility.line_endings import LineEnding


def test_line_ending_exposes_exact_lf_and_crlf_bytes() -> None:
    """验证换行枚举只暴露精确的 LF 与 CRLF bytes。"""
    assert LineEnding.LF.value == b"\n"
    assert LineEnding.CRLF.value == b"\r\n"
