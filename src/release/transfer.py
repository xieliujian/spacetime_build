"""发布传输对象的确定性原始/ZIP 构建。"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TransferObject:
    """记录源内容和最终传输字节身份。"""

    logical_path: str
    content: bytes
    source_md5: str
    original_size: int
    transfer_size: int
    compressed: bool


class TransferObjectBuilder:
    """按客户端兼容规则构造确定性传输对象。"""

    @staticmethod
    def build(
        logical_path: str,
        content: bytes,
        *,
        platform: str,
        is_trunk: bool = True,
    ) -> TransferObject:
        """生成原始或 ZIP 传输内容。

        参数：
            logical_path: 客户端相对逻辑路径。
            content: 原始文件字节。
            platform: 平台标签；``windows`` 参与 config 例外规则。
            is_trunk: 是否 trunk；Windows 非 trunk 的 config 不压缩。

        返回：
            包含原始 MD5、原始大小、传输大小和确定性内容的 ``TransferObject``。

        异常：
            路径、内容、平台或 trunk 类型非法时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            ZIP 使用固定时间戳、条目名和压缩配置；纯内存执行，不写文件。
        """
        _validate_inputs(logical_path, content, platform, is_trunk)
        compressed = _should_compress(logical_path, platform, is_trunk)
        transfer = _deterministic_zip(logical_path, content) if compressed else content
        return TransferObject(
            logical_path,
            transfer,
            hashlib.md5(content).hexdigest(),
            len(content),
            len(transfer),
            compressed,
        )


def _validate_inputs(logical_path: str, content: bytes, platform: str, is_trunk: bool) -> None:
    """校验传输对象构建输入。"""
    if not isinstance(logical_path, str) or not logical_path or "\\" in logical_path:
        raise ValueError("logical_path 必须是非空正斜杠路径")
    if not isinstance(content, bytes):
        raise TypeError("content 必须是 bytes")
    if not isinstance(platform, str) or not platform:
        raise ValueError("platform 必须是非空字符串")
    if not isinstance(is_trunk, bool):
        raise TypeError("is_trunk 必须是 bool")


def _should_compress(logical_path: str, platform: str, is_trunk: bool) -> bool:
    """应用旧客户端规定的压缩路径规则。"""
    if platform.casefold() == "windows" and not is_trunk and logical_path.startswith("config/"):
        return False
    return logical_path.startswith("config/") or "assetbundledb_" in logical_path.rsplit("/", 1)[-1]


def _deterministic_zip(logical_path: str, content: bytes) -> bytes:
    """创建固定元数据的单文件 ZIP。"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        info = zipfile.ZipInfo(logical_path, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 0
        info.external_attr = 0
        archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()
