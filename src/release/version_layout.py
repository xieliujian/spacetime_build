"""正式版本入口的确定性兼容布局编码。

版本入口不是固定名称的 ``current.json``；调用方必须提供目标对象 key 和版本目录
基础地址。本模块只生成入口规范 JSON，严格区分显示版本和客户端协议 FileListNo，
不访问对象存储、不执行 CAS。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.manifest_codec import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class VersionEntry:
    """版本入口对象的逻辑 key 和规范字节。"""

    key: str
    file_list_base_url: str
    file_list_no: int
    content: bytes


def build_version_entry(
    key: str,
    file_list_base_url: str,
    file_list_no: int,
) -> VersionEntry:
    """按固定字段顺序生成版本入口 JSON。"""
    if not isinstance(key, str) or not key or any(character in key for character in "\r\n"):
        raise ValueError("version entry key 必须是非空且不含换行的字符串")
    if (
        not isinstance(file_list_base_url, str)
        or not file_list_base_url
        or any(character in file_list_base_url for character in "\r\n")
    ):
        raise ValueError("file_list_base_url 必须是非空且不含换行的字符串")
    if not isinstance(file_list_no, int) or isinstance(file_list_no, bool) or file_list_no <= 0:
        raise ValueError("file_list_no 必须是正整数")
    content = canonical_json_bytes(
        {
            "encoding": "utf-8",
            "file_list_base_url": file_list_base_url,
            "file_list_no": file_list_no,
            "schema_version": 1,
        }
    )
    return VersionEntry(key, file_list_base_url, file_list_no, content)


__all__ = ["VersionEntry", "build_version_entry"]
