"""从逻辑产物显式组装 ReleaseManifest 与 ReleaseBundle。

组装服务要求调用方提供源 MD5、文件列表号和来源 manifest 身份；它不从文件名猜测
分包、不生成协议文本、不上传对象。普通文件和 AssetBundle 的 membership 由现有
``ArtifactKind`` 显式映射到 ``ReleaseSnapshotEntry``，最终只调用 release 工厂。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.artifacts import ArtifactKind, LogicalArtifact
from release.bundle_codec import ReleaseBundleFactory
from release.bundles import (
    RELEASE_BUNDLE_SCHEMA_VERSION,
    ReleaseBundle,
    ReleaseBundlePayload,
)
from release.entries import ReleaseEntry, ReleaseObjectOrigin, ResourceVariant
from release.manifest_codec import ReleaseManifestFactory
from release.manifests import (
    RELEASE_MANIFEST_SCHEMA_VERSION,
    ReleaseManifest,
    ReleaseManifestPayload,
)
from release.snapshots import (
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)

_MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class ReleaseAssemblyItem:
    """将逻辑产物补充为发布所需源 MD5 的组装输入。"""

    artifact: LogicalArtifact
    source_md5: str
    object_origin: ReleaseObjectOrigin = ReleaseObjectOrigin.CURRENT_UPLOAD
    historical_object_version: str | None = None
    file_url: str | None = None
    subpackage_flag: int = 0


@dataclass(frozen=True, slots=True)
class ReleaseAssemblyResult:
    """组装产生的 ReleaseManifest 与 ReleaseBundle。"""

    manifest: ReleaseManifest
    bundle: ReleaseBundle


class ReleaseAssembler:
    """把显式产物输入组装为单变体发布模型。"""

    @staticmethod
    def assemble(
        variant: ResourceVariant,
        file_list_no: int,
        source_manifest_ids: tuple[str, ...],
        items: tuple[ReleaseAssemblyItem, ...],
    ) -> ReleaseAssemblyResult:
        """创建当前 FileListNo 下的 ReleaseManifest 和单主清 Bundle。

        参数：
            variant: 主清或低清变体。
            file_list_no: 正 Int32 文件列表号。
            source_manifest_ids: 来源 BuildManifest ID 元组。
            items: 每个逻辑产物及其源 MD5/历史对象信息。

        返回：
            已经由现有 factory 创建的 ``ReleaseAssemblyResult``。

        异常：
            输入为空、MD5/版本/变体或逻辑产物不合法时抛出 ``ValueError`` /
            ``PublishError``。

        约束与副作用：
            纯内存组装；不访问 Blob、不写 compatibility、不上传和激活。
        """
        if not isinstance(variant, ResourceVariant):
            raise TypeError("variant 必须是 ResourceVariant")
        if not isinstance(file_list_no, int) or isinstance(file_list_no, bool) or file_list_no <= 0:
            raise ValueError("file_list_no 必须是正整数")
        if not isinstance(source_manifest_ids, tuple) or not source_manifest_ids:
            raise ValueError("source_manifest_ids 必须是非空 tuple")
        if not items:
            raise ValueError("items 不得为空")
        snapshot_items: list[ReleaseSnapshotEntry] = []
        for item in items:
            if not isinstance(item, ReleaseAssemblyItem):
                raise TypeError("items 必须全部是 ReleaseAssemblyItem")
            if not isinstance(item.artifact, LogicalArtifact):
                raise TypeError("artifact 必须是 LogicalArtifact")
            if _MD5_PATTERN.fullmatch(item.source_md5) is None:
                raise ValueError("source_md5 必须是 32 位小写 MD5")
            if item.object_origin is ReleaseObjectOrigin.CURRENT_UPLOAD:
                object_version = (
                    str(file_list_no) if variant is ResourceVariant.MAIN else f"{file_list_no}_low"
                )
            elif item.object_origin is ReleaseObjectOrigin.HISTORICAL:
                if not item.historical_object_version:
                    raise ValueError("HISTORICAL 必须提供 historical_object_version")
                object_version = item.historical_object_version
            else:
                raise ValueError("object_origin 非法")
            is_bundle = item.artifact.kind is ArtifactKind.ASSET_BUNDLE
            artifact_class = (
                ReleaseArtifactClass.ASSET_BUNDLE
                if is_bundle
                else ReleaseArtifactClass.REGULAR_FILE
            )
            memberships = (
                frozenset({ReleaseMembership.FILE_LIST, ReleaseMembership.ASSET_BUNDLE_DATABASE})
                if is_bundle
                else frozenset({ReleaseMembership.FILE_LIST})
            )
            file_url = item.file_url or f"cdn/{item.artifact.logical_path}"
            entry = ReleaseEntry(
                logical_path=item.artifact.logical_path,
                variant=variant,
                source_blob=item.artifact.blob,
                source_md5=item.source_md5,
                original_size=item.artifact.blob.size,
                transfer_blob=item.artifact.blob,
                transfer_size=item.artifact.blob.size,
                list_version=file_list_no,
                object_version=object_version,
                file_url=file_url,
                subpackage_flag=item.subpackage_flag,
                object_origin=item.object_origin,
            )
            snapshot_items.append(
                ReleaseSnapshotEntry(
                    release_entry=entry,
                    artifact_class=artifact_class,
                    memberships=memberships,
                    assetbundle_dependencies=item.artifact.dependencies if is_bundle else (),
                    redirect_slice=None,
                )
            )
        snapshot = ReleaseSnapshot.create(variant, tuple(snapshot_items))
        manifest = ReleaseManifestFactory.create(
            ReleaseManifestPayload(
                RELEASE_MANIFEST_SCHEMA_VERSION,
                variant,
                file_list_no,
                snapshot,
                source_manifest_ids,
            )
        )
        bundle = ReleaseBundleFactory.create(
            ReleaseBundlePayload(RELEASE_BUNDLE_SCHEMA_VERSION, (manifest,), None)
        )
        return ReleaseAssemblyResult(manifest, bundle)
