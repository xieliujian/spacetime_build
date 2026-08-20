"""将六字段文件列表 DTO 编码为旧客户端兼容文本 bytes。

Writer 不接受散装字段，只接受 ``file_list_dto`` 的不可变行对象；输出使用 UTF-8、
按文件名 UTF-8 bytes 排序，并显式采用调用方指定的 LF 或 CRLF。导入本模块不执行
文件写入。
"""

from __future__ import annotations

from collections.abc import Sequence

from core.errors import CompatibilityError

from compatibility.file_list_dto import FileListRow
from compatibility.line_endings import LineEnding


class LegacyFileListWriter:
    """生成旧客户端六字段文件列表的确定性 Writer。

    参数：
        line_ending: 明确指定的 ``LineEnding``。

    返回：
        ``write`` 返回 UTF-8 文件 bytes；空行集合返回 ``b""``。

    异常：
        换行策略、行类型或字段编码不合法时抛出 ``CompatibilityError``。

    约束与副作用：
        只做内存编码，不创建文件、不调用外部系统。
    """

    def __init__(self, line_ending: LineEnding) -> None:
        """绑定显式换行策略。

        参数：
            line_ending: 只能是 ``LineEnding`` 枚举成员。

        返回：
            无。

        异常：
            传入其他对象时抛出 ``CompatibilityError``。

        约束与副作用：
            只保存不可变枚举引用，不产生 I/O。
        """
        if not isinstance(line_ending, LineEnding):
            raise CompatibilityError("line_ending 必须是 LineEnding")
        self._line_ending = line_ending

    def write(self, rows: Sequence[FileListRow]) -> bytes:
        """按 UTF-8 文件名排序并生成六字段协议 bytes。

        参数：
            rows: 由 ``file_list_rows_from_manifest`` 生成的 DTO 序列。

        返回：
            每行五个 Tab、显式终止换行的 UTF-8 bytes；空序列返回空 bytes。

        异常：
            序列或任一元素不是 ``FileListRow``，或字段无法编码时抛出
            ``CompatibilityError``。

        约束与副作用：
            不依赖输入排列；排序键为 ``file_name.encode("utf-8")``，不写文件。
        """
        if not isinstance(rows, Sequence):
            raise CompatibilityError("rows 必须是文件列表 DTO 序列")
        for row in rows:
            if not isinstance(row, FileListRow):
                raise CompatibilityError("rows 的每一项必须是 FileListRow")
        ordered = sorted(rows, key=lambda row: row.file_name.encode("utf-8"))
        if not ordered:
            return b""
        lines = [
            "\t".join(
                (
                    row.file_name,
                    str(row.file_version),
                    str(row.file_size),
                    row.file_md5,
                    row.file_url,
                    str(row.subpackage_flag),
                )
            ).encode("utf-8")
            for row in ordered
        ]
        return self._line_ending.value.join(lines) + self._line_ending.value
