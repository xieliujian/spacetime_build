"""构建产物元数据、Blob 引用与逻辑产物的领域模型。

本模块提供不可变的 ``ArtifactMetadata``、``BlobRef``、``ArtifactKind`` 与
``LogicalArtifact`` 类型，用于表达产物来源、工具链摘要、内容寻址存储定位，以及
客户端 ``/`` 逻辑路径与依赖/分包集合语义。字段在构造时校验，违反不变量时抛出
``ArtifactValidationError``。导入本模块不执行构建，也不访问 SVN、Unity、
Jenkins 或 CDN。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import cast

from core.errors import ArtifactValidationError

# SHA256 必须恰好 64 位小写十六进制，保证缓存键与协议输出确定性。
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# 内容寻址 locator 的可接受形式：sha256:<hex> 或 blobs/<hex>（hex 可为摘要前缀）。
_CONTENT_ADDRESSABLE_LOCATOR_PATTERN = re.compile(r"^(?:sha256:[0-9a-f]+|blobs/[0-9a-f]+)$")


def _is_temporary_workdir_locator(locator: str) -> bool:
    """判断 locator 是否表现为临时工作目录标记。

    参数：
        locator: 待检查的定位字符串；调用方保证已为 ``str``。

    返回：
        若匹配常见临时目录段或前缀则返回 ``True``，否则 ``False``。

    异常：
        无。

    约束与副作用：
        纯函数；不访问文件系统。规则覆盖 ``/tmp/``、``\\Temp\\`` 以及
        ``tmp/`` / ``temp/`` 前缀，防止把本地临时路径误当作持久 CAS 引用。
    """
    normalized = locator.replace("\\", "/")
    lowered = normalized.lower()
    if "/tmp/" in lowered or lowered.startswith("tmp/"):
        return True
    if "/temp/" in lowered or lowered.startswith("temp/"):
        return True
    return False


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """类型化、可稳定编码的产物元数据。

    职责：
        记录产物来源任务、源码 revision、工具链摘要，以及按 key 排序后可确定性
        编码的字符串属性对；供 BuildManifest 与下游校验复用，不包含运行状态。

    参数：
        source_task: 产生该产物的任务名（逻辑标识，非文件系统路径）。
        source_revision: 源码或输入快照 revision 标识。
        toolchain_digest: 工具链配置摘要，用于复现性比对。
        attributes: 仅接受 ``tuple[tuple[str, str], ...]``；key 必须唯一，
            值必须为 ``str``。禁止传入任意 ``Mapping``，以保证编码确定性。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        构造时若 ``attributes`` 不是合法的字符串对元组、含重复 key、含非字符串
        元素，或传入 ``Mapping``，抛出 ``ArtifactValidationError``。

    约束与副作用：
        ``frozen=True, slots=True``；构造后不可变。不实现 manifest JSON 编解码。
        不读写磁盘，无外部副作用。
    """

    source_task: str
    source_revision: str
    toolchain_digest: str
    attributes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """构造后校验 ``attributes`` 的类型与唯一性不变量。

        参数：
            无；读取 ``self.attributes``。

        返回：
            ``None``。

        异常：
            ``attributes`` 为 ``Mapping``、非元组、元素非 ``(str, str)`` 对，或
            存在重复 key 时，抛出 ``ArtifactValidationError``。

        约束与副作用：
            仅做内存校验，无 I/O。故意拒绝 ``Mapping``，避免无序字典破坏
            确定性编码。
        """
        attrs = cast(object, self.attributes)
        # Mapping 无稳定顺序，禁止作为 canonical attributes 输入。
        if isinstance(attrs, Mapping):
            raise ArtifactValidationError(
                "attributes 不得为 Mapping，须使用 tuple[tuple[str, str], ...]"
            )
        if not isinstance(attrs, tuple):
            raise ArtifactValidationError("attributes 必须是 tuple[tuple[str, str], ...]")
        seen_keys: set[str] = set()
        for item in cast(tuple[object, ...], attrs):
            if not isinstance(item, tuple) or len(cast(tuple[object, ...], item)) != 2:
                raise ArtifactValidationError("attributes 每一项必须是长度为 2 的 (str, str) 元组")
            pair = cast(tuple[object, ...], item)
            key_obj: object = pair[0]
            value_obj: object = pair[1]
            if not isinstance(key_obj, str) or not isinstance(value_obj, str):
                raise ArtifactValidationError("attributes 的 key 与 value 必须均为 str")
            if key_obj in seen_keys:
                raise ArtifactValidationError(f"attributes 存在重复 key: {key_obj!r}")
            seen_keys.add(key_obj)


@dataclass(frozen=True, slots=True)
class BlobRef:
    """指向持久内容寻址存储的不可变 Blob 引用。

    职责：
        用 locator、SHA256 与字节大小唯一标识一份已物化产物内容；供逻辑产物与
        发布条目引用，不得指向临时工作目录。

    参数：
        locator: 非空内容寻址定位串，形如 ``sha256:<hex>`` 或 ``blobs/<hex>``。
        sha256: 恰好 64 位小写十六进制内容哈希。
        size: 非负整数字节大小。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        locator 为空/空白、含临时工作目录标记、不符合内容寻址形式，或 sha256 /
        size 非法时，抛出 ``ArtifactValidationError``。

    约束与副作用：
        ``frozen=True, slots=True``；构造后不可变。不打开文件、不访问 CAS。
    """

    locator: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        """构造后校验 locator、sha256 与 size 不变量。

        参数：
            无；读取 ``self.locator``、``self.sha256``、``self.size``。

        返回：
            ``None``。

        异常：
            任一字段违反持久 CAS 引用约束时抛出 ``ArtifactValidationError``。

        约束与副作用：
            仅内存校验；拒绝临时目录标记，避免把工作区路径登记为 Blob。
        """
        locator = cast(object, self.locator)
        if not isinstance(locator, str) or not locator.strip():
            raise ArtifactValidationError("locator 不得为空或仅空白")
        if _is_temporary_workdir_locator(locator):
            raise ArtifactValidationError(f"locator 不得引用临时工作目录: {locator!r}")
        if not _CONTENT_ADDRESSABLE_LOCATOR_PATTERN.fullmatch(locator):
            raise ArtifactValidationError(
                "locator 必须是内容寻址形式（sha256:<hex> 或 blobs/<hex>）"
            )

        sha256 = cast(object, self.sha256)
        if not isinstance(sha256, str) or not _SHA256_PATTERN.fullmatch(sha256):
            raise ArtifactValidationError("sha256 必须是恰好 64 位小写十六进制字符串")

        size = cast(object, self.size)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ArtifactValidationError("size 必须是非负整数")


class ArtifactKind(Enum):
    """逻辑产物的类型分类。

    职责：
        区分 AssetBundle、普通文件等产物种类，供规划、校验与兼容层按 kind
        分支处理；本枚举本身不编码发布 membership。

    参数：
        枚举成员无额外构造参数；值为稳定字符串标签。

    返回：
        无；通过 ``ArtifactKind.<NAME>`` 取值。

    异常：
        无；非法名称访问由 ``Enum`` 标准机制报错。

    约束与副作用：
        仅描述内部产物种类；不读写磁盘，无外部副作用。
    """

    ASSET_BUNDLE = "asset_bundle"
    FILE = "file"


def _validate_logical_path(path: str, *, field_name: str) -> None:
    """校验客户端逻辑路径不变量。

    参数：
        path: 待校验路径字符串。
        field_name: 用于错误消息的字段名（如 ``logical_path`` 或 ``dependencies``）。

    返回：
        ``None``；校验通过时无返回值。

    异常：
        路径为空、含绝对形式、反斜杠、``.`` / ``..`` 段、空段或尾斜杠时，抛出
        ``ArtifactValidationError``。

    约束与副作用：
        纯函数；客户端逻辑路径统一使用 ``/``，禁止文件系统绝对路径语义。
    """
    path_obj = cast(object, path)
    if not isinstance(path_obj, str) or path_obj == "":
        raise ArtifactValidationError(f"{field_name} 不得为空")
    path = path_obj
    # 绝对 POSIX 路径或以盘符开头的 Windows 路径均不得作为客户端逻辑路径。
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":"):
        raise ArtifactValidationError(f"{field_name} 不得为绝对路径: {path!r}")
    if "\\" in path:
        raise ArtifactValidationError(f"{field_name} 不得包含反斜杠: {path!r}")
    if path.endswith("/"):
        raise ArtifactValidationError(f"{field_name} 不得以斜杠结尾: {path!r}")
    segments = path.split("/")
    for segment in segments:
        if segment == "" or segment == "." or segment == "..":
            raise ArtifactValidationError(f"{field_name} 含非法路径段: {path!r}")


@dataclass(frozen=True, slots=True)
class LogicalArtifact:
    """带客户端逻辑路径的不可变构建产物。

    职责：
        将逻辑路径、产物种类、持久 Blob、有序依赖、分包集合与 metadata 绑定为
        单一不可变记录；供 BuildManifest、任务恢复与发布快照引用。

    参数：
        logical_path: 客户端 ``/`` 分隔相对逻辑路径，不得绝对、含 ``\\``、
            ``.`` / ``..``、空段或尾斜杠。
        kind: ``ArtifactKind`` 分类。
        blob: 指向持久 CAS 的 ``BlobRef``。
        dependencies: 有序依赖逻辑路径元组；保留重复与顺序，仅逐项校验路径。
        subpackage_ids: 可迭代整数分包 ID；构造时规范为 ``frozenset[int]``。
        metadata: 类型化 ``ArtifactMetadata``。

    返回：
        无；本类为不可变数据载体，通过字段访问读取。

    异常：
        逻辑路径或任一依赖路径非法，或 ``subpackage_ids`` 含非整数时，抛出
        ``ArtifactValidationError``。

    约束与副作用：
        ``frozen=True, slots=True``；依赖保持 tuple 语义，仅分包 ID 使用无序
        集合。不读写磁盘，无外部副作用。
    """

    logical_path: str
    kind: ArtifactKind
    blob: BlobRef
    dependencies: tuple[str, ...]
    subpackage_ids: frozenset[int]
    metadata: ArtifactMetadata

    def __post_init__(self) -> None:
        """构造后校验路径不变量，并将分包 ID 规范为 frozenset。

        参数：
            无；读取各实例字段。

        返回：
            ``None``。

        异常：
            逻辑路径或任一依赖路径非法，或 ``subpackage_ids`` 无法规范为整数
            ``frozenset`` 时，抛出 ``ArtifactValidationError``。

        约束与副作用：
            仅内存校验与字段规范化；通过 ``object.__setattr__`` 写回冻结字段。
        """
        _validate_logical_path(self.logical_path, field_name="logical_path")
        dependencies = cast(object, self.dependencies)
        if not isinstance(dependencies, tuple):
            raise ArtifactValidationError("dependencies 必须是 tuple[str, ...]")
        for dep in cast(tuple[object, ...], dependencies):
            if not isinstance(dep, str):
                raise ArtifactValidationError("dependencies 的每一项必须是 str")
            _validate_logical_path(dep, field_name="dependencies")

        raw_ids = cast(object, self.subpackage_ids)
        # 无序集合语义：接受任意可迭代输入，规范为 frozenset 以保证确定性比较。
        if isinstance(raw_ids, frozenset):
            normalized_ids = cast(frozenset[object], raw_ids)
        else:
            try:
                normalized_ids = frozenset(cast(Iterable[object], raw_ids))
            except TypeError as exc:
                raise ArtifactValidationError("subpackage_ids 必须可迭代为整数集合") from exc
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in normalized_ids):
            raise ArtifactValidationError("subpackage_ids 的每一项必须是 int")
        object.__setattr__(self, "subpackage_ids", frozenset(cast(frozenset[int], normalized_ids)))
