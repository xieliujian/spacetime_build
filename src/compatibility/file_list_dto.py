"""把 ReleaseManifest 转换为旧客户端六字段文件列表 DTO。

本模块只允许从已验证的 ``ReleaseManifest`` 读取条目，不重新压缩文件、不修正
历史 URL，也不生成文本。``FileListRow`` 使用隐藏工厂绑定，避免调用方直接构造或
用 ``dataclasses.replace`` 绕过协议字段校验。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from core.errors import CompatibilityError, PublishError
from release.entries import ReleaseObjectOrigin
from release.manifests import ReleaseManifestPayload
from release.snapshots import ReleaseMembership

_INT32_MAX = 2**31 - 1
_MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ROW_FACTORY_TOKEN = object()


def _validate_text(value: object, *, field_name: str, allow_empty: bool = False) -> str:
    """校验协议文本可严格编码且不包含控制分隔符。

    参数：
        value: 待校验对象。
        field_name: 错误消息中的字段名。
        allow_empty: 是否允许空字符串。

    返回：
        原样返回的合法字符串。

    异常：
        类型、空值、Tab、CR、LF 或 UTF-8 编码失败时抛出 ``CompatibilityError``。

    约束与副作用：
        纯内存校验，不规范化、不替换文本。
    """
    if not isinstance(value, str):
        raise CompatibilityError(f"{field_name} 必须是 str")
    if not allow_empty and value == "":
        raise CompatibilityError(f"{field_name} 不得为空")
    if any(char in value for char in "\t\r\n"):
        raise CompatibilityError(f"{field_name} 不得包含 Tab、CR 或 LF")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise CompatibilityError(f"{field_name} 不是合法 UTF-8 文本") from exc
    return value


def _validate_path(value: object, *, field_name: str) -> str:
    """校验兼容协议中的客户端相对逻辑路径。

    参数：
        value: 待校验路径。
        field_name: 错误消息中的字段名。

    返回：
        保持原样的合法路径。

    异常：
        绝对路径、反斜杠、空段、点段或控制字符时抛出 ``CompatibilityError``。

    约束与副作用：
        路径统一使用 ``/``，不访问文件系统。
    """
    path = _validate_text(value, field_name=field_name)
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":"):
        raise CompatibilityError(f"{field_name} 不得为绝对路径: {path!r}")
    if "\\" in path:
        raise CompatibilityError(f"{field_name} 不得包含反斜杠: {path!r}")
    if any(segment in {"", ".", ".."} for segment in path.split("/")):
        raise CompatibilityError(f"{field_name} 含非法路径段: {path!r}")
    return path


def _validate_int(value: object, *, field_name: str, positive: bool = False) -> int:
    """校验兼容协议中的 Int32 整数。

    参数：
        value: 待校验整数。
        field_name: 错误消息中的字段名。
        positive: 是否要求大于零。

    返回：
        合法整数。

    异常：
        非整数、布尔值、负数、零或超过 Int32 上限时抛出 ``CompatibilityError``。

    约束与副作用：
        纯内存校验，边界为 32 位有符号整数。
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise CompatibilityError(f"{field_name} 必须是 int")
    lower = 1 if positive else 0
    if value < lower or value > _INT32_MAX:
        raise CompatibilityError(f"{field_name} 超出 Int32 范围: {value!r}")
    return value


@dataclass(frozen=True, slots=True, init=False)
class FileListRow:
    """旧客户端六字段文件列表的一行只读 DTO。

    字段顺序为文件名、列表版本、传输大小、源内容 MD5、对象 URL 和分包 bit flag。
    实例只能由 ``file_list_rows_from_manifest`` 创建，避免散装协议数据绕过领域边界。

    参数：
        通过隐藏工厂绑定六个公开只读字段。

    返回：
        无；本类是不可变协议数据载体。

    异常：
        直接构造或替换实例时抛出 ``TypeError``；字段不合法时由内部工厂抛出
        ``CompatibilityError``。

    约束与副作用：
        不含换行字节、不访问文件系统、不执行外部副作用。
    """

    file_name: str
    file_version: int
    file_size: int
    file_md5: str
    file_url: str
    subpackage_flag: int

    @staticmethod
    def _create(
        *,
        file_name: str,
        file_version: int,
        file_size: int,
        file_md5: str,
        file_url: str,
        subpackage_flag: int,
    ) -> FileListRow:
        """由兼容转换工厂创建并校验一行 DTO。

        参数：
            file_name: 客户端逻辑文件名。
            file_version: 正 Int32 文件列表版本。
            file_size: 非负 Int32 传输大小。
            file_md5: 原始内容小写 MD5。
            file_url: 非空对象 URL。
            subpackage_flag: 非负 Int32 分包 bit flag。

        返回：
            通过字段校验的 ``FileListRow``。

        异常：
            字段非法时抛出 ``CompatibilityError``。

        约束与副作用：
            仅通过 ``object.__new__`` 绑定冻结字段，不产生 I/O。
        """
        name = _validate_path(file_name, field_name="file_name")
        version = _validate_int(file_version, field_name="file_version", positive=True)
        size = _validate_int(file_size, field_name="file_size")
        url = _validate_text(file_url, field_name="file_url")
        flag = _validate_int(subpackage_flag, field_name="subpackage_flag")
        if not isinstance(file_md5, str) or _MD5_PATTERN.fullmatch(file_md5) is None:
            raise CompatibilityError("file_md5 必须是 32 位小写十六进制字符串")

        row = object.__new__(FileListRow)
        object.__setattr__(row, "file_name", name)
        object.__setattr__(row, "file_version", version)
        object.__setattr__(row, "file_size", size)
        object.__setattr__(row, "file_md5", file_md5)
        object.__setattr__(row, "file_url", url)
        object.__setattr__(row, "subpackage_flag", flag)
        return row


def file_list_rows_from_manifest(manifest: ReleaseManifestPayload) -> tuple[FileListRow, ...]:
    """从已校验 manifest 单向生成六字段文件列表行。

    参数：
        manifest: 已通过 ``ReleaseManifestFactory`` 约束的 manifest payload。

    返回：
        按快照顺序生成的不可变 ``FileListRow`` 元组；Writer 负责最终排序。

    异常：
        输入类型、领域版本一致性或协议字段不合法时抛出 ``CompatibilityError``。

    约束与副作用：
        只转换带 ``FILE_LIST`` membership 的条目；Redirect slice 不进入文件列表，
        Redirect container 由其自身唯一逻辑路径输出一次。不读取 Blob 字节。
    """
    if not isinstance(manifest, ReleaseManifestPayload):
        raise CompatibilityError("file_list_rows_from_manifest 只接受 ReleaseManifestPayload")

    rows: list[FileListRow] = []
    try:
        for snapshot_entry in manifest.snapshot.entries:
            if ReleaseMembership.FILE_LIST not in snapshot_entry.memberships:
                continue
            entry = snapshot_entry.release_entry
            if entry.list_version != manifest.file_list_no:
                raise CompatibilityError(
                    f"条目 list_version 必须等于 file_list_no: {entry.logical_path!r}"
                )
            logical_path = _validate_path(entry.logical_path, field_name="logical_path")
            if entry.object_origin is ReleaseObjectOrigin.CURRENT_UPLOAD:
                file_url = quote(
                    f"{entry.object_version}/{logical_path}",
                    safe="/",
                )
                if (
                    entry.transfer_blob != entry.source_blob
                    or entry.transfer_size != entry.original_size
                ):
                    file_url += ".zip"
            else:
                # 历史 URL 是兼容协议的一部分，必须原样保留，不能按当前规则重写。
                file_url = entry.file_url
            rows.append(
                FileListRow._create(
                    file_name=logical_path,
                    file_version=entry.list_version,
                    file_size=entry.transfer_size,
                    file_md5=entry.source_md5,
                    file_url=file_url,
                    subpackage_flag=entry.subpackage_flag,
                )
            )
    except CompatibilityError:
        raise
    except (PublishError, UnicodeError, TypeError, ValueError) as exc:
        raise CompatibilityError(f"ReleaseManifest 无法转换为文件列表 DTO: {exc}") from exc
    return tuple(rows)
