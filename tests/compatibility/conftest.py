"""提供兼容协议测试共用的最小发布领域对象构造器。

测试夹具只在内存中创建已经校验的 ``release`` 对象，不访问 Unity、SVN、Jenkins、
CDN 或旧参考目录。所有协议测试都通过这些构造器验证 compatibility 的单向转换。
"""

from __future__ import annotations

from core.artifacts import BlobRef
from release.entries import ReleaseEntry, ReleaseObjectOrigin, ResourceVariant
from release.manifests import (
    RELEASE_MANIFEST_SCHEMA_VERSION,
    ReleaseManifestPayload,
)
from release.snapshots import (
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
MD5_A = "1" * 32
MD5_B = "2" * 32
BOTH = frozenset({ReleaseMembership.FILE_LIST, ReleaseMembership.ASSET_BUNDLE_DATABASE})
AB_ONLY = frozenset({ReleaseMembership.ASSET_BUNDLE_DATABASE})
FILE_ONLY = frozenset({ReleaseMembership.FILE_LIST})


def blob(sha256: str, *, size: int) -> BlobRef:
    """创建固定大小的内容寻址 Blob 引用。

    参数：
        sha256: 64 位小写 SHA256。
        size: 非负字节大小。

    返回：
        通过领域校验的 ``BlobRef``。

    异常：
        输入不符合 BlobRef 不变量时由领域模型抛出 ``ArtifactValidationError``。

    约束与副作用：
        纯内存构造，不读写文件。
    """
    return BlobRef(locator=f"sha256:{sha256}", sha256=sha256, size=size)


def release_entry(
    logical_path: str,
    *,
    variant: ResourceVariant = ResourceVariant.MAIN,
    artifact_class: ReleaseArtifactClass = ReleaseArtifactClass.ASSET_BUNDLE,
    list_version: int = 123,
    object_version: str | None = None,
    object_origin: ReleaseObjectOrigin = ReleaseObjectOrigin.CURRENT_UPLOAD,
    source_sha: str = SHA_A,
    transfer_sha: str = SHA_A,
    source_size: int = 100,
    transfer_size: int | None = None,
    source_md5: str = MD5_A,
    file_url: str | None = None,
    subpackage_flag: int = 0,
    dependencies: tuple[str, ...] = (),
) -> ReleaseSnapshotEntry:
    """创建一个用于兼容协议测试的快照条目。

    参数：
        logical_path: 客户端逻辑路径。
        variant: 主清或低清变体。
        artifact_class: 普通文件、AssetBundle 或 Redirect 分类。
        list_version: 文件列表版本。
        object_version: 对象版本；缺省按变体生成当前版本值。
        object_origin: 当前上传或历史沿用。
        source_sha/transfer_sha: 源与传输 Blob 摘要。
        source_size/transfer_size: 源与传输大小。
        source_md5: 原始内容 MD5。
        file_url: 历史条目的旧 URL。
        subpackage_flag: 旧客户端分包 bit flag。
        dependencies: 有序且可重复的 AssetBundle 依赖。

    返回：
        已通过 ``ReleaseSnapshotEntry`` 单条校验的条目。

    异常：
        输入违反 release 领域不变量时抛出 ``PublishError``。

    约束与副作用：
        Redirect 切片由专门测试显式构造；本辅助函数不读写外部系统。
    """
    if object_version is None:
        object_version = "123" if variant is ResourceVariant.MAIN else "123_low"
    if transfer_size is None:
        transfer_size = source_size
    if file_url is None:
        file_url = f"{object_version}/{logical_path}"
    entry = ReleaseEntry(
        logical_path=logical_path,
        variant=variant,
        source_blob=blob(source_sha, size=source_size),
        source_md5=source_md5,
        original_size=source_size,
        transfer_blob=blob(transfer_sha, size=transfer_size),
        transfer_size=transfer_size,
        list_version=list_version,
        object_version=object_version,
        file_url=file_url,
        subpackage_flag=subpackage_flag,
        object_origin=object_origin,
    )
    if artifact_class is ReleaseArtifactClass.REGULAR_FILE:
        memberships = FILE_ONLY
    elif artifact_class is ReleaseArtifactClass.REDIRECT_SLICE:
        memberships = AB_ONLY
    else:
        memberships = BOTH
    return ReleaseSnapshotEntry(
        release_entry=entry,
        artifact_class=artifact_class,
        memberships=memberships,
        assetbundle_dependencies=dependencies,
        redirect_slice=None,
    )


def manifest(
    entries: tuple[ReleaseSnapshotEntry, ...],
    *,
    variant: ResourceVariant = ResourceVariant.MAIN,
    file_list_no: int = 123,
) -> ReleaseManifestPayload:
    """把测试快照包装成通过版本一致性校验的 manifest payload。

    参数：
        entries: 同一变体的快照条目元组。
        variant: payload 变体。
        file_list_no: 当前文件列表号。

    返回：
        已通过 ``ReleaseManifestPayload`` 校验的 payload。

    异常：
        条目变体、版本或 snapshot 关系非法时抛出 ``PublishError``。

    约束与副作用：
        纯内存构造，不计算 manifest ID，不执行 I/O。
    """
    snapshot = ReleaseSnapshot.create(variant, entries)
    return ReleaseManifestPayload(
        schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
        variant=variant,
        file_list_no=file_list_no,
        snapshot=snapshot,
        source_manifest_ids=("build-test",),
    )
