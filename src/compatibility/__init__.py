"""旧客户端兼容协议生成与解析入口。

本包只从已经校验的 ``release`` 领域模型生成六字段文件列表和
``assetbundledb_*.txt`` 数据库。协议字节、换行策略和严格解析均在本包内完成；
本包不访问 Unity、SVN、Jenkins、CDN，也不反向依赖 compatibility 的领域模型。
"""

from compatibility.line_endings import LineEnding
from compatibility.assetbundle_dto import (
    AssetBundleDatabase,
    AssetBundleRecord,
    AssetBundleRedirectRecord,
    assetbundle_records_from_release_snapshot,
)
from compatibility.assetbundle_parser import (
    LegacyAssetBundleDbParser,
    ParsedAssetBundleDatabase,
    ParsedAssetBundleRecord,
    ParsedAssetBundleRedirect,
)
from compatibility.assetbundle_routing import (
    DATABASE_ORDER,
    client_databases_from_release_snapshot,
    database_names_for_path,
)
from compatibility.assetbundle_writer import LegacyAssetBundleDbWriter
from compatibility.file_list_dto import FileListRow, file_list_rows_from_manifest
from compatibility.file_list_parser import LegacyFileListParser, ParsedFileListRow
from compatibility.file_list_writer import LegacyFileListWriter

__all__ = [
    "AssetBundleDatabase",
    "AssetBundleRecord",
    "AssetBundleRedirectRecord",
    "DATABASE_ORDER",
    "FileListRow",
    "LegacyAssetBundleDbParser",
    "LegacyAssetBundleDbWriter",
    "LegacyFileListParser",
    "LegacyFileListWriter",
    "LineEnding",
    "ParsedAssetBundleDatabase",
    "ParsedAssetBundleRecord",
    "ParsedAssetBundleRedirect",
    "ParsedFileListRow",
    "assetbundle_records_from_release_snapshot",
    "client_databases_from_release_snapshot",
    "database_names_for_path",
    "file_list_rows_from_manifest",
]
