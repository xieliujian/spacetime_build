"""发布条目、资源变体与对象来源语义。

本模块提供不可变的 ``ResourceVariant``、``ReleaseObjectOrigin`` 与
``ReleaseEntry``，用于表达主/低清发布条目的传输身份、列表版本与对象来源规则。
``CURRENT_UPLOAD`` 条目的 ``object_version`` 必须使用 ``{current}`` /
``{current}_low`` 哨兵，或已展开的正整数 FileListNo / ``{n}_low``；
``HISTORICAL`` 可保留合法历史版本与 URL。本模块不出现六字段文本列名，不导入
``compatibility``。导入本模块不执行构建或发布。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import cast

from core.artifacts import BlobRef
from core.errors import PublishError

# 原始内容 MD5：恰好 32 位小写十六进制，与传输侧 SHA256 身份分离。
_MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$")

# 与旧客户端 FileListNo 占位一致：本次新上传 main/low 可用哨兵或已展开的正整数。
_CURRENT_MAIN_OBJECT_VERSION = "{current}"
_CURRENT_LOW_OBJECT_VERSION = "{current}_low"
_CONCRETE_MAIN_OBJECT_VERSION = re.compile(r"^[1-9][0-9]*$")
_CONCRETE_LOW_OBJECT_VERSION = re.compile(r"^[1-9][0-9]*_low$")

_INT32_MAX = 2**31 - 1


class ResourceVariant(Enum):
    """资源发布变体（主清 / 低清）。

    职责：
        区分主资源与低清资源通道，供 ``ReleaseEntry``、snapshot、manifest 与
        bundle 锁定单一变体；本枚举为 ``release`` 包内唯一声明点。

    参数：
        枚举成员无额外构造参数；值为稳定字符串标签。

    返回：
        无；通过 ``ResourceVariant.<NAME>`` 取值。

    异常：
        无；非法名称访问由 ``Enum`` 标准机制报错。

    约束与副作用：
        不得在 ``core`` 或其他包重复声明同义枚举。无外部副作用。
    """

    MAIN = "main"
    LOW = "low"


class ReleaseObjectOrigin(Enum):
    """发布对象在本次发布中的来源类别。

    职责：
        区分本次新上传对象与历史沿用对象，驱动 ``object_version`` 哨兵校验。

    参数：
        枚举成员无额外构造参数；值为稳定字符串标签。

    返回：
        无；通过 ``ReleaseObjectOrigin.<NAME>`` 取值。

    异常：
        无；非法名称访问由 ``Enum`` 标准机制报错。

    约束与副作用：
        ``CURRENT_UPLOAD`` 强制哨兵版本；``HISTORICAL`` 允许历史版本串。
        无外部副作用。
    """

    CURRENT_UPLOAD = "current_upload"
    HISTORICAL = "historical"


def _validate_logical_path(path: str) -> None:
    """校验客户端逻辑路径不变量（与产物层规则对齐）。

    参数：
        path: 待校验路径字符串。

    返回：
        ``None``；校验通过时无返回值。

    异常：
        路径为空、绝对形式、含反斜杠、``.`` / ``..``、空段或尾斜杠时，抛出
        ``PublishError``。

    约束与副作用：
        纯函数；客户端逻辑路径统一使用 ``/``。
    """
    path_obj = cast(object, path)
    if not isinstance(path_obj, str) or path_obj == "":
        raise PublishError("logical_path 不得为空")
    path = path_obj
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":"):
        raise PublishError(f"logical_path 不得为绝对路径: {path!r}")
    if "\\" in path:
        raise PublishError(f"logical_path 不得包含反斜杠: {path!r}")
    if path.endswith("/"):
        raise PublishError(f"logical_path 不得以斜杠结尾: {path!r}")
    for segment in path.split("/"):
        if segment == "" or segment == "." or segment == "..":
            raise PublishError(f"logical_path 含非法路径段: {path!r}")


def _validate_non_negative_int32(value: int, *, field_name: str) -> None:
    """校验字段为非负 Int32（含 0）。

    参数：
        value: 待校验整数。
        field_name: 用于错误消息的字段名。

    返回：
        ``None``。

    异常：
        非 ``int``、为 ``bool``、为负或超过 Int32 最大值时抛出 ``PublishError``。

    约束与副作用：
        纯函数；与旧客户端有符号 32 位字段边界对齐。
    """
    value_obj = cast(object, value)
    if not isinstance(value_obj, int) or isinstance(value_obj, bool):
        raise PublishError(f"{field_name} 必须是 int")
    if value_obj < 0 or value_obj > _INT32_MAX:
        raise PublishError(
            f"{field_name} 必须是非负 Int32（0..{_INT32_MAX}），实际为 {value_obj!r}"
        )


def _validate_positive_int32(value: int, *, field_name: str) -> None:
    """校验字段为正 Int32（> 0）。

    参数：
        value: 待校验整数。
        field_name: 用于错误消息的字段名。

    返回：
        ``None``。

    异常：
        非 ``int``、为 ``bool``、非正或超过 Int32 最大值时抛出 ``PublishError``。

    约束与副作用：
        纯函数；``list_version`` 等列表版本号不得为 0。
    """
    value_obj = cast(object, value)
    if not isinstance(value_obj, int) or isinstance(value_obj, bool):
        raise PublishError(f"{field_name} 必须是 int")
    if value_obj <= 0 or value_obj > _INT32_MAX:
        raise PublishError(f"{field_name} 必须是正 Int32（1..{_INT32_MAX}），实际为 {value_obj!r}")


def _is_allowed_current_object_version(variant: ResourceVariant, object_version: str) -> bool:
    """判断 ``CURRENT_UPLOAD`` 的 object_version 是否为合法哨兵或已展开 FileListNo。

    参数：
        variant: 主清或低清变体。
        object_version: 待检查的对象版本字符串。

    返回：
        合法则 ``True``，否则 ``False``。

    异常：
        未知变体时抛出 ``PublishError``。

    约束与副作用：
        纯函数；``ReleaseManifestPayload`` 另须校验已展开值与 ``file_list_no`` 一致。
    """
    if variant is ResourceVariant.MAIN:
        return (
            object_version == _CURRENT_MAIN_OBJECT_VERSION
            or _CONCRETE_MAIN_OBJECT_VERSION.fullmatch(object_version) is not None
        )
    if variant is ResourceVariant.LOW:
        return (
            object_version == _CURRENT_LOW_OBJECT_VERSION
            or _CONCRETE_LOW_OBJECT_VERSION.fullmatch(object_version) is not None
        )
    raise PublishError(f"未知 ResourceVariant: {variant!r}")


@dataclass(frozen=True, slots=True)
class ReleaseEntry:
    """单条协议无关发布条目。

    职责：
        绑定逻辑路径、主/低清变体、源内容与传输内容身份、列表版本、对象版本、
        URL、分包标志与对象来源；供 snapshot / manifest 组装，不生成六字段文本。

    参数：
        logical_path: 客户端 ``/`` 分隔相对逻辑路径。
        variant: ``ResourceVariant.MAIN`` 或 ``LOW``。
        source_blob: 源内容持久 ``BlobRef``（传输前身份）。
        source_md5: 原始内容 32 位小写十六进制 MD5。
        original_size: 原始字节大小，非负 Int32。
        transfer_blob: 传输内容持久 ``BlobRef``（可与源哈希不同）。
        transfer_size: 传输字节大小，非负 Int32；可与 ``original_size`` 不同。
        list_version: 正 Int32 文件列表版本。
        object_version: 对象版本；``CURRENT_UPLOAD`` 时必须为哨兵
            ``{current}`` / ``{current}_low``，或已展开的正整数 /
            ``{n}_low``（由 manifest 层与 ``file_list_no`` 对齐）。
        file_url: 非空发布 URL 字符串。
        subpackage_flag: 非负 Int32 分包 bit flag。
        object_origin: ``CURRENT_UPLOAD`` 或 ``HISTORICAL``。

    返回：
        无；本类为不可变数据载体。

    异常：
        路径、MD5、Int32 边界或对象来源版本规则违反时抛出 ``PublishError``。
        ``BlobRef`` 自身非法时抛出 ``ArtifactValidationError``。

    约束与副作用：
        ``frozen=True, slots=True``；不导入 ``compatibility``，不读写磁盘。
        原始 MD5/大小与传输 SHA256/大小必须作为独立身份字段建模。
    """

    logical_path: str
    variant: ResourceVariant
    source_blob: BlobRef
    source_md5: str
    original_size: int
    transfer_blob: BlobRef
    transfer_size: int
    list_version: int
    object_version: str
    file_url: str
    subpackage_flag: int
    object_origin: ReleaseObjectOrigin

    def __post_init__(self) -> None:
        """构造后校验路径、传输身份、Int32 边界与对象来源哨兵。

        参数：
            无；读取各实例字段。

        返回：
            ``None``。

        异常：
            任一发布条目不变量被违反时抛出 ``PublishError``。

        约束与副作用：
            仅内存校验；``CURRENT_UPLOAD`` 不得使用历史版本字面量。
        """
        variant = cast(object, self.variant)
        if not isinstance(variant, ResourceVariant):
            raise PublishError("variant 必须是 ResourceVariant")
        object_origin = cast(object, self.object_origin)
        if not isinstance(object_origin, ReleaseObjectOrigin):
            raise PublishError("object_origin 必须是 ReleaseObjectOrigin")
        source_blob = cast(object, self.source_blob)
        if not isinstance(source_blob, BlobRef):
            raise PublishError("source_blob 必须是 BlobRef")
        transfer_blob = cast(object, self.transfer_blob)
        if not isinstance(transfer_blob, BlobRef):
            raise PublishError("transfer_blob 必须是 BlobRef")

        _validate_logical_path(self.logical_path)

        source_md5 = cast(object, self.source_md5)
        if not isinstance(source_md5, str) or not _MD5_PATTERN.fullmatch(source_md5):
            raise PublishError("source_md5 必须是恰好 32 位小写十六进制字符串")

        _validate_non_negative_int32(self.original_size, field_name="original_size")
        _validate_non_negative_int32(self.transfer_size, field_name="transfer_size")
        _validate_positive_int32(self.list_version, field_name="list_version")
        _validate_non_negative_int32(self.subpackage_flag, field_name="subpackage_flag")

        file_url = cast(object, self.file_url)
        if not isinstance(file_url, str) or not file_url.strip():
            raise PublishError("file_url 不得为空或仅空白")

        object_version = cast(object, self.object_version)
        if not isinstance(object_version, str) or object_version == "":
            raise PublishError("object_version 不得为空")

        # 本次新上传：哨兵或已展开正整数 FileListNo；禁止任意历史风格字符串。
        if self.object_origin is ReleaseObjectOrigin.CURRENT_UPLOAD:
            if not _is_allowed_current_object_version(self.variant, self.object_version):
                raise PublishError(
                    "CURRENT_UPLOAD 的 object_version 必须为 "
                    f"哨兵或正整数 FileListNo 形式（variant={self.variant.value}），"
                    f"实际为 {self.object_version!r}"
                )
        elif self.object_origin is ReleaseObjectOrigin.HISTORICAL:
            # 历史条目可保留任意非空 object_version / URL，仍受路径与 Int32 约束。
            pass
        else:
            raise PublishError(f"未知 ReleaseObjectOrigin: {self.object_origin!r}")
