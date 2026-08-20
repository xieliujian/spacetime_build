"""将已验证 ReleaseManifest 交给 compatibility writer 并登记 Blob。

release 层只负责选择协议对象、计算内容寻址身份和稳定对象键；五库路由、六字段
行生成和文本换行全部复用 ``compatibility`` 的现有 DTO/Writer，避免两层各自实现
旧客户端协议。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from compatibility.assetbundle_routing import DATABASE_ORDER, client_databases_from_release_snapshot
from compatibility.assetbundle_writer import LegacyAssetBundleDbWriter
from compatibility.file_list_dto import file_list_rows_from_manifest
from compatibility.file_list_writer import LegacyFileListWriter
from compatibility.line_endings import LineEnding
from core.artifacts import BlobRef
from core.errors import PublishError
from release.entries import ResourceVariant
from release.manifests import ReleaseManifest


@dataclass(frozen=True, slots=True)
class ProtocolOutput:
    """单个兼容协议文件的逻辑键、字节和 Blob 身份。"""

    key: str
    content: bytes
    blob: BlobRef


class ProtocolOutputBuilder:
    """从 ReleaseManifest 生成五库和主/低清文件列表 Blob。"""

    @staticmethod
    def build(
        manifests: tuple[ReleaseManifest, ...], line_ending: LineEnding
    ) -> tuple[ProtocolOutput, ...]:
        """调用 compatibility writer 生成确定性协议对象。

        参数：
            manifests: 一个主清及可选低清的已工厂创建 manifest 元组。
            line_ending: 明确的 LF 或 CRLF 输出策略。

        返回：
            按变体和数据库固定顺序排列的 12 或 6 个协议 Blob。

        异常：
            manifest、变体重复或换行策略非法时抛出 ``TypeError`` / ``PublishError``。

        约束与副作用：
            只读内存并调用 compatibility writer；不拼接协议字段、不上传对象。
        """
        if not isinstance(manifests, tuple) or not manifests:
            raise TypeError("manifests 必须是非空 tuple")
        if not isinstance(line_ending, LineEnding):
            raise TypeError("line_ending 必须是 LineEnding")
        variants: set[ResourceVariant] = set()
        outputs: list[ProtocolOutput] = []
        for manifest in sorted(manifests, key=lambda item: item.payload.variant.value):
            if not isinstance(manifest, ReleaseManifest):
                raise TypeError("manifests 的每一项必须是 ReleaseManifest")
            variant = manifest.payload.variant
            if variant in variants:
                raise PublishError("协议输出不得重复同一 ResourceVariant")
            variants.add(variant)
            databases = client_databases_from_release_snapshot(manifest.payload.snapshot)
            db_writer = LegacyAssetBundleDbWriter(line_ending)
            for database_name, database in zip(DATABASE_ORDER, databases):
                content = db_writer.write(database)
                outputs.append(_output(f"{variant.value}/{database_name}", content))
            rows = file_list_rows_from_manifest(manifest.payload)
            content = LegacyFileListWriter(line_ending).write(rows)
            outputs.append(
                _output(f"{variant.value}/file_list_{manifest.payload.file_list_no}.txt", content)
            )
        return tuple(outputs)


def _output(key: str, content: bytes) -> ProtocolOutput:
    """计算协议内容寻址 Blob。"""
    digest = hashlib.sha256(content).hexdigest()
    return ProtocolOutput(key, content, BlobRef(f"blobs/{digest}", digest, len(content)))
