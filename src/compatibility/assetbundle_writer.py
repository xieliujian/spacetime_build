"""将 AssetBundle 数据库 DTO 编码为旧客户端索引文本 bytes。

Writer 为记录按名称分配连续索引，依赖索引保持原顺序和重复；每条记录最多输出一行
``Depend:`` 和一行 ``Redirect:``，并在输出前拒绝依赖环。所有换行由调用方显式
选择，模块不写文件。
"""

from __future__ import annotations

from core.errors import CompatibilityError

from compatibility.assetbundle_dto import AssetBundleDatabase, AssetBundleRecord
from compatibility.line_endings import LineEnding


class LegacyAssetBundleDbWriter:
    """生成确定性 AssetBundle 数据库文本的 Writer。

    参数：
        line_ending: 显式 LF 或 CRLF 换行策略。

    返回：
        ``write`` 返回 UTF-8 数据库 bytes；空数据库返回空 bytes。

    异常：
        输入类型、引用或依赖循环非法时抛出 ``CompatibilityError``。

    约束与副作用：
        只在内存中生成 bytes，不执行外部副作用。
    """

    def __init__(self, line_ending: LineEnding) -> None:
        """绑定数据库 Writer 的显式换行策略。

        参数：
            line_ending: ``LineEnding`` 枚举成员。

        返回：
            无。

        异常：
            非枚举换行策略时抛出 ``CompatibilityError``。

        约束与副作用：
            只保存策略，不访问文件系统。
        """
        if not isinstance(line_ending, LineEnding):
            raise CompatibilityError("line_ending 必须是 LineEnding")
        self._line_ending = line_ending

    def write(self, database: AssetBundleDatabase) -> bytes:
        """编码一个数据库并校验依赖无环。

        参数：
            database: 由五库聚合工厂创建的不可变数据库。

        返回：
            记录主行及缩进 Depend/Redirect 子行组成的 UTF-8 bytes。

        异常：
            数据库类型、引用或依赖循环非法时抛出 ``CompatibilityError``。

        约束与副作用：
            记录按名称 UTF-8 bytes 排序，索引从零连续分配，不写文件。
        """
        if not isinstance(database, AssetBundleDatabase):
            raise CompatibilityError("database 必须是 AssetBundleDatabase")
        records = tuple(sorted(database.records, key=lambda item: item.name.encode("utf-8")))
        by_name = {record.name: index for index, record in enumerate(records)}
        if len(by_name) != len(records):
            raise CompatibilityError("AssetBundle 数据库名称重复")
        self._validate_dependencies(records, by_name)
        lines: list[bytes] = []
        for index, record in enumerate(records):
            lines.append(f"{record.name}\t{index}".encode("utf-8"))
            if record.dependencies:
                dependency_indexes = [str(by_name[name]) for name in record.dependencies]
                lines.append(("\tDepend:" + "\t".join(dependency_indexes)).encode("utf-8"))
            if record.redirect is not None:
                redirect = record.redirect
                if redirect.container_name not in by_name:
                    raise CompatibilityError(
                        f"Redirect 容器不在数据库内: {redirect.container_name!r}"
                    )
                lines.append(
                    (
                        f"\tRedirect:{by_name[redirect.container_name]}"
                        f"\t{redirect.offset}\t{redirect.length}"
                    ).encode("utf-8")
                )
        if not lines:
            return b""
        return self._line_ending.value.join(lines) + self._line_ending.value

    @staticmethod
    def _validate_dependencies(
        records: tuple[AssetBundleRecord, ...],
        by_name: dict[str, int],
    ) -> None:
        """检测数据库依赖引用和有向环。

        参数：
            records: 已按输出顺序排列的记录。
            by_name: 记录名到输出索引的映射。

        返回：
            ``None``；通过时表示依赖图无环。

        异常：
            缺失依赖或检测到自环/多节点环时抛出 ``CompatibilityError``。

        约束与副作用：
            只读取 DTO，不修改图和记录。
        """
        graph: dict[str, tuple[str, ...]] = {}
        for record in records:
            graph[record.name] = record.dependencies
            for dependency in record.dependencies:
                if dependency not in by_name:
                    raise CompatibilityError(
                        f"AssetBundle 依赖不在数据库内: {record.name!r} -> {dependency!r}"
                    )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            """深度优先检查一个依赖节点。"""
            if name in visiting:
                raise CompatibilityError(f"AssetBundle 依赖存在循环: {name!r}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in graph[name]:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for record in records:
            visit(record.name)
