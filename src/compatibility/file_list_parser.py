"""严格解析旧客户端六字段文件列表文本。

Parser 与 Writer 使用同一显式换行策略，但返回独立的解析视图，不调用或暴露
``FileListRow`` 的私有工厂。所有 malformed bytes、字段、路径和整数错误都统一为
``CompatibilityError``，便于迁移探针区分协议失败与领域失败。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.errors import CompatibilityError

from compatibility.file_list_dto import _validate_path, _validate_text
from compatibility.line_endings import LineEnding

_INT32_MAX = 2**31 - 1
_INTEGER_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)$")
_MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class ParsedFileListRow:
    """六字段 Parser 返回的独立只读行视图。

    参数：
        file_name、file_version、file_size、file_md5、file_url、subpackage_flag：
            对应旧协议六列，均已完成严格输入校验。

    返回：
        无；本类是解析结果数据载体。

    异常：
        解析错误不在本类构造时产生，而由 ``LegacyFileListParser`` 统一抛出。

    约束与副作用：
        不依赖领域 DTO，不访问文件系统。
    """

    file_name: str
    file_version: int
    file_size: int
    file_md5: str
    file_url: str
    subpackage_flag: int


def _parse_int(value: str, *, field_name: str, line_number: int, positive: bool = False) -> int:
    """解析无前导零的非负规范十进制 Int32。

    参数：
        value: 原始字段文本。
        field_name: 字段名。
        line_number: 协议行号。
        positive: 是否要求大于零。

    返回：
        合法 Int32 整数。

    异常：
        文本格式、范围或正性不满足时抛出带行号的 ``CompatibilityError``。

    约束与副作用：
        拒绝加号、空白、前导零和布尔语义。
    """
    if _INTEGER_PATTERN.fullmatch(value) is None:
        raise CompatibilityError(f"第 {line_number} 行 {field_name} 不是规范十进制")
    number = int(value)
    lower = 1 if positive else 0
    if number < lower or number > _INT32_MAX:
        raise CompatibilityError(f"第 {line_number} 行 {field_name} 超出 Int32 范围")
    return number


class LegacyFileListParser:
    """严格读取六字段文件列表 bytes 的 Parser。

    参数：
        line_ending: 调用方声明的唯一合法换行策略。

    返回：
        ``parse`` 返回按文件出现顺序排列的 ``ParsedFileListRow`` 元组。

    异常：
        BOM、非 UTF-8、混合/错误换行、未终止行、字段数量、路径、整数、MD5 或
        重复文件名错误均抛出 ``CompatibilityError``。

    约束与副作用：
        只解析内存 bytes，不读写文件，不调用 Writer 或 release 领域对象。
    """

    def __init__(self, line_ending: LineEnding) -> None:
        """绑定 Parser 的唯一合法换行。

        参数：
            line_ending: ``LineEnding.LF`` 或 ``LineEnding.CRLF``。

        返回：
            无。

        异常：
            非枚举值时抛出 ``CompatibilityError``。

        约束与副作用：
            只保存策略，不执行 I/O。
        """
        if not isinstance(line_ending, LineEnding):
            raise CompatibilityError("line_ending 必须是 LineEnding")
        self._line_ending = line_ending

    def parse(
        self,
        data: bytes,
        *,
        expected_list_version: int | None = None,
    ) -> tuple[ParsedFileListRow, ...]:
        """严格解析六字段文件列表。

        参数：
            data: 原始协议 bytes。
            expected_list_version: 可选的全文件版本约束。

        返回：
            按原始顺序排列的解析行元组；空 bytes 返回空元组。

        异常：
            任一协议不变量违反时抛出带行号的 ``CompatibilityError``。

        约束与副作用：
            要求非空输入以指定换行结束，并拒绝混合换行和 BOM。
        """
        if not isinstance(data, bytes):
            raise CompatibilityError("file list data 必须是 bytes")
        if expected_list_version is not None:
            if not isinstance(expected_list_version, int) or isinstance(
                expected_list_version, bool
            ):
                raise CompatibilityError("expected_list_version 必须是 int")
            if expected_list_version <= 0 or expected_list_version > _INT32_MAX:
                raise CompatibilityError("expected_list_version 超出 Int32 范围")
        if data == b"":
            return ()
        if data.startswith(b"\xef\xbb\xbf"):
            raise CompatibilityError("文件列表不得包含 UTF-8 BOM")
        ending = self._line_ending.value
        if not data.endswith(ending):
            raise CompatibilityError("非空文件列表必须以指定换行结束")
        if self._line_ending is LineEnding.LF:
            if b"\r" in data:
                raise CompatibilityError("LF 文件列表不得包含 CR 或混合换行")
        else:
            if b"\n" in data.replace(b"\r\n", b""):
                raise CompatibilityError("CRLF 文件列表不得包含孤立 LF")
        raw_lines = data[: -len(ending)].split(ending)
        rows: list[ParsedFileListRow] = []
        seen_names: set[str] = set()
        for line_number, raw_line in enumerate(raw_lines, start=1):
            try:
                line = raw_line.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise CompatibilityError(f"第 {line_number} 行不是合法 UTF-8") from exc
            if any(char in line for char in "\t\r\n"):
                fields = line.split("\t")
            else:
                fields = line.split("\t")
            if len(fields) != 6:
                raise CompatibilityError(f"第 {line_number} 行必须恰有六个字段")
            file_name = _validate_path(fields[0], field_name=f"第 {line_number} 行 file_name")
            if file_name in seen_names:
                raise CompatibilityError(f"第 {line_number} 行 file_name 重复: {file_name!r}")
            seen_names.add(file_name)
            file_version = _parse_int(
                fields[1], field_name="file_version", line_number=line_number, positive=True
            )
            if expected_list_version is not None and file_version != expected_list_version:
                raise CompatibilityError(f"第 {line_number} 行 file_version 与期望版本不一致")
            file_size = _parse_int(fields[2], field_name="file_size", line_number=line_number)
            file_md5 = fields[3]
            if _MD5_PATTERN.fullmatch(file_md5) is None:
                raise CompatibilityError(f"第 {line_number} 行 file_md5 非法")
            file_url = _validate_text(fields[4], field_name=f"第 {line_number} 行 file_url")
            subpackage_flag = _parse_int(
                fields[5], field_name="subpackage_flag", line_number=line_number
            )
            rows.append(
                ParsedFileListRow(
                    file_name=file_name,
                    file_version=file_version,
                    file_size=file_size,
                    file_md5=file_md5,
                    file_url=file_url,
                    subpackage_flag=subpackage_flag,
                )
            )
        return tuple(rows)
