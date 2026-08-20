"""严格解析旧客户端 AssetBundle 数据库文本。

Parser 返回独立的只读解析视图，逐行验证记录索引、依赖/Redirect 子行顺序、引用
范围、路径、换行和依赖环。它不调用 ``assetbundle_dto`` 的私有工厂，避免把外部
协议文本反向伪装成已验证 ReleaseSnapshot。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from compatibility.file_list_dto import _validate_path
from compatibility.line_endings import LineEnding
from core.errors import CompatibilityError

_INTEGER_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)$")
_INT32_MAX = 2**31 - 1


@dataclass(frozen=True, slots=True)
class ParsedAssetBundleRedirect:
    """Parser 返回的 Redirect 子行解析视图。

    参数：
        container_index: 容器记录索引。
        offset: 非负字节偏移。
        length: 正字节长度。

    返回：
        无；本类仅承载 Parser 结果。

    异常：
        字段错误由 Parser 在构造前报告为 ``CompatibilityError``。

    约束与副作用：
        不依赖 Writer DTO，不访问文件系统。
    """

    container_index: int
    offset: int
    length: int


@dataclass(frozen=True, slots=True)
class ParsedAssetBundleRecord:
    """Parser 返回的 AssetBundle 记录视图。

    参数：
        name: 逻辑 AssetBundle 名称。
        index: 文件中的连续记录索引。
        dependencies: 解析后的依赖名称，顺序和重复保持不变。
        redirect: 可选 Redirect 解析视图。

    返回：
        无；本类为不可变解析数据。

    异常：
        结构错误由 ``LegacyAssetBundleDbParser`` 统一抛出。

    约束与副作用：
        只表达协议文本，不声称来自 ReleaseSnapshot。
    """

    name: str
    index: int
    dependencies: tuple[str, ...]
    redirect: ParsedAssetBundleRedirect | None


@dataclass(frozen=True, slots=True)
class ParsedAssetBundleDatabase:
    """AssetBundle 数据库 Parser 的只读结果。

    参数：
        records: 按协议主行顺序排列的记录元组。

    返回：
        无；通过 ``parse`` 获得实例。

    异常：
        Parser 在构造前拒绝非法字节和引用。

    约束与副作用：
        不包含数据库文件名，不写文件，不可直接反向生成领域快照。
    """

    records: tuple[ParsedAssetBundleRecord, ...]


def _parse_int(value: str, *, field_name: str, line_number: int, positive: bool = False) -> int:
    """解析无前导零的非负规范十进制整数。

    参数：
        value: 原始字段文本。
        field_name: 字段名。
        line_number: 行号。
        positive: 是否要求大于零。

    返回：
        合法 Int32 整数。

    异常：
        格式、范围或正性错误时抛出 ``CompatibilityError``。

    约束与副作用：
        拒绝空白、加号、前导零和超出 Int32 的数值。
    """
    if _INTEGER_PATTERN.fullmatch(value) is None:
        raise CompatibilityError(f"第 {line_number} 行 {field_name} 不是规范十进制")
    number = int(value)
    if number > _INT32_MAX or (positive and number == 0):
        raise CompatibilityError(f"第 {line_number} 行 {field_name} 超出范围")
    return number


class LegacyAssetBundleDbParser:
    """严格解析 AssetBundle 数据库 bytes。

    参数：
        line_ending: 输入必须使用的唯一换行策略。

    返回：
        ``parse`` 返回独立的 ``ParsedAssetBundleDatabase``。

    异常：
        BOM、非 UTF-8、混合换行、坏字段、索引、引用、子行顺序或循环均抛出
        带行号上下文的 ``CompatibilityError``。

    约束与副作用：
        空 bytes 合法；非空输入必须每行以指定换行结束，不调用 Writer。
    """

    def __init__(self, line_ending: LineEnding) -> None:
        """绑定数据库 Parser 的换行策略。

        参数：
            line_ending: ``LineEnding`` 枚举成员。

        返回：
            无。

        异常：
            非枚举输入时抛出 ``CompatibilityError``。

        约束与副作用：
            只保存内存配置。
        """
        if not isinstance(line_ending, LineEnding):
            raise CompatibilityError("line_ending 必须是 LineEnding")
        self._line_ending = line_ending

    def parse(self, data: bytes) -> ParsedAssetBundleDatabase:
        """严格解析记录主行和缩进子行。

        参数：
            data: 原始 AssetBundle 数据库 bytes。

        返回：
            按主行顺序排列的独立解析数据库。

        异常：
            任一格式、引用、范围、换行或依赖环错误时抛出 ``CompatibilityError``。

        约束与副作用：
            先完成整份输入结构解析，再验证索引引用和有向环，不产生部分业务结果。
        """
        if not isinstance(data, bytes):
            raise CompatibilityError("assetbundle database data 必须是 bytes")
        if data == b"":
            return ParsedAssetBundleDatabase(records=())
        if data.startswith(b"\xef\xbb\xbf"):
            raise CompatibilityError("AssetBundle 数据库不得包含 UTF-8 BOM")
        ending = self._line_ending.value
        if not data.endswith(ending):
            raise CompatibilityError("非空 AssetBundle 数据库必须以指定换行结束")
        if self._line_ending is LineEnding.LF:
            if b"\r" in data:
                raise CompatibilityError("LF 数据库不得包含 CR 或混合换行")
        elif b"\n" in data.replace(b"\r\n", b""):
            raise CompatibilityError("CRLF 数据库不得包含孤立 LF")

        raw_lines = data[: -len(ending)].split(ending)
        names: list[str] = []
        indexes: list[int] = []
        dependencies: list[tuple[int, ...]] = []
        redirects: list[ParsedAssetBundleRedirect | None] = []
        depend_seen: list[bool] = []
        redirect_seen: list[bool] = []
        current_index: int | None = None
        for line_number, raw_line in enumerate(raw_lines, start=1):
            try:
                line = raw_line.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise CompatibilityError(f"第 {line_number} 行不是合法 UTF-8") from exc
            if line.startswith("\t"):
                if current_index is None:
                    raise CompatibilityError(f"第 {line_number} 行子行没有前置记录")
                if line.startswith("\tDepend:"):
                    if depend_seen[current_index] or redirect_seen[current_index]:
                        raise CompatibilityError(f"第 {line_number} 行 Depend 子行顺序或重复非法")
                    fields = line.split("\t")
                    if (
                        len(fields) < 2
                        or fields[1] == "Depend:"
                        or not fields[1].startswith("Depend:")
                    ):
                        raise CompatibilityError(f"第 {line_number} 行 Depend 字段为空或非法")
                    raw_indexes = fields[1][len("Depend:") :]
                    dependency_indexes = [raw_indexes]
                    dependency_indexes.extend(fields[2:])
                    parsed_indexes = tuple(
                        _parse_int(
                            value,
                            field_name="dependency_index",
                            line_number=line_number,
                        )
                        for value in dependency_indexes
                    )
                    dependencies[current_index] = parsed_indexes
                    depend_seen[current_index] = True
                    continue
                if line.startswith("\tRedirect:"):
                    if (
                        redirect_seen[current_index]
                        or depend_seen[current_index] is False
                        and False
                    ):
                        raise CompatibilityError(f"第 {line_number} 行 Redirect 子行重复或顺序非法")
                    if not depend_seen[current_index] and dependencies[current_index]:
                        raise CompatibilityError(
                            f"第 {line_number} 行 Redirect 必须位于 Depend 之后"
                        )
                    fields = line.split("\t")
                    if len(fields) != 4 or not fields[1].startswith("Redirect:"):
                        raise CompatibilityError(f"第 {line_number} 行 Redirect 字段数非法")
                    container_index = _parse_int(
                        fields[1][len("Redirect:") :],
                        field_name="container_index",
                        line_number=line_number,
                    )
                    offset = _parse_int(fields[2], field_name="offset", line_number=line_number)
                    length = _parse_int(
                        fields[3], field_name="length", line_number=line_number, positive=True
                    )
                    redirects[current_index] = ParsedAssetBundleRedirect(
                        container_index=container_index,
                        offset=offset,
                        length=length,
                    )
                    redirect_seen[current_index] = True
                    continue
                raise CompatibilityError(f"第 {line_number} 行未知子行类型")

            fields = line.split("\t")
            if len(fields) != 2:
                raise CompatibilityError(f"第 {line_number} 行主记录必须有两个字段")
            name = _validate_path(fields[0], field_name=f"第 {line_number} 行 assetbundle_name")
            if name in names:
                raise CompatibilityError(f"第 {line_number} 行 AssetBundle 名称重复")
            index = _parse_int(fields[1], field_name="record_index", line_number=line_number)
            expected_index = len(names)
            if index != expected_index:
                raise CompatibilityError(
                    f"第 {line_number} 行 record_index 必须为 {expected_index}"
                )
            names.append(name)
            indexes.append(index)
            dependencies.append(())
            redirects.append(None)
            depend_seen.append(False)
            redirect_seen.append(False)
            current_index = index

        self._validate_references(names, dependencies, redirects)
        records = tuple(
            ParsedAssetBundleRecord(
                name=name,
                index=index,
                dependencies=tuple(names[target] for target in deps),
                redirect=redirect,
            )
            for name, index, deps, redirect in zip(names, indexes, dependencies, redirects)
        )
        return ParsedAssetBundleDatabase(records=records)

    @staticmethod
    def _validate_references(
        names: list[str],
        dependencies: list[tuple[int, ...]],
        redirects: list[ParsedAssetBundleRedirect | None],
    ) -> None:
        """校验依赖/Redirect 索引范围并检测依赖环。

        参数：
            names: 记录名称表。
            dependencies: 每条记录的依赖索引。
            redirects: 每条记录的可选 Redirect 索引。

        返回：
            ``None``；通过时所有索引都能解析且依赖无环。

        异常：
            未知索引或有向环时抛出 ``CompatibilityError``。

        约束与副作用：
            只做内存校验，不修改解析结果。
        """
        count = len(names)
        graph: dict[int, tuple[int, ...]] = {}
        for index, deps in enumerate(dependencies):
            for target in deps:
                if target >= count:
                    raise CompatibilityError(f"记录 {index} 的依赖索引越界: {target}")
            graph[index] = deps
        for index, redirect in enumerate(redirects):
            if redirect is not None and redirect.container_index >= count:
                raise CompatibilityError(
                    f"记录 {index} 的 Redirect 容器索引越界: {redirect.container_index}"
                )

        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(index: int) -> None:
            """深度优先检查解析结果中的一个依赖节点。"""
            if index in visiting:
                raise CompatibilityError(f"AssetBundle 依赖存在循环: {names[index]!r}")
            if index in visited:
                return
            visiting.add(index)
            for target in graph[index]:
                visit(target)
            visiting.remove(index)
            visited.add(index)

        for index in range(count):
            visit(index)
