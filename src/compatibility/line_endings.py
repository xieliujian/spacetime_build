"""定义旧客户端协议使用的显式 UTF-8 换行字节策略。

协议文件不能依赖宿主平台的 ``os.linesep`` 或文本文件默认转换，因此 Writer 和
Parser 都必须显式接收本模块的 ``LineEnding``。导入本模块不执行任何 I/O。
"""

from __future__ import annotations

from enum import Enum


class LineEnding(Enum):
    """旧协议支持的精确换行字节。

    参数：
        枚举值分别为 LF 或 CRLF 的原始 bytes。

    返回：
        通过 ``LineEnding.LF.value`` 或 ``LineEnding.CRLF.value`` 读取 bytes。

    异常：
        无；非法枚举名称由 Python ``Enum`` 机制报告。

    约束与副作用：
        值固定为 UTF-8 协议中的 ``b"\\n"`` 或 ``b"\\r\\n"``，不随平台变化。
    """

    LF = b"\n"
    CRLF = b"\r\n"
