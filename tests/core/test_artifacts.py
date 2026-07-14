"""验证产物元数据、Blob 引用与逻辑产物的不可变契约与路径校验。

本模块按第二阶段 Task 3–4 分步覆盖类型化 metadata、持久 Blob 引用与
``LogicalArtifact`` 路径/集合语义。测试不访问 SVN、Unity、Jenkins 或 CDN，
也不写入构建产物。
"""

from __future__ import annotations

from typing import cast

from collections.abc import Mapping

import pytest

from st.build.core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    BlobRef,
    LogicalArtifact,
)
from st.build.core.errors import ArtifactValidationError

_VALID_SHA256 = "a" * 64


def _valid_metadata() -> ArtifactMetadata:
    """构造测试用合法 ``ArtifactMetadata``。

    返回：
        含最小合法字段的不可变 metadata 实例。
    """
    return ArtifactMetadata(
        source_task="scene.build",
        source_revision="r12345",
        toolchain_digest="toolchain-sha256-digest",
        attributes=(("platform", "android"),),
    )


def _valid_blob() -> BlobRef:
    """构造测试用合法 ``BlobRef``。

    返回：
        内容寻址 locator、合法 sha256 与非负 size 的 Blob 引用。
    """
    return BlobRef(
        locator=f"sha256:{_VALID_SHA256}",
        sha256=_VALID_SHA256,
        size=1024,
    )


def test_artifact_metadata_is_typed_immutable_and_canonicalizable() -> None:
    """验证 ``ArtifactMetadata`` 不可变，且 ``attributes`` 仅接受可稳定编码元组。

    测试无参数和返回值。断言：

    - 构造 ``ArtifactMetadata(source_task, source_revision, toolchain_digest,
      attributes)`` 成功时字段可读且对象 ``frozen``（赋值触发
      ``dataclasses.FrozenInstanceError`` / ``AttributeError``）；
    - ``attributes`` 接受按 key 排序后可稳定编码的 ``tuple[tuple[str, str], ...]``；
    - 拒绝任意 ``Mapping[str, object]``、重复 key，以及非字符串值（含非 str key）。

    当 ``st.build.core.artifacts`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入外不产生外部副作用。
    """
    attributes: tuple[tuple[str, str], ...] = (
        ("platform", "android"),
        ("variant", "main"),
    )
    metadata = ArtifactMetadata(
        source_task="scene.build",
        source_revision="r12345",
        toolchain_digest="toolchain-sha256-digest",
        attributes=attributes,
    )
    assert metadata.source_task == "scene.build"
    assert metadata.source_revision == "r12345"
    assert metadata.toolchain_digest == "toolchain-sha256-digest"
    assert metadata.attributes == attributes

    with pytest.raises((AttributeError, TypeError)):
        metadata.source_task = "other.task"  # type: ignore[misc]

    with pytest.raises(ArtifactValidationError):
        ArtifactMetadata(
            source_task="scene.build",
            source_revision="r12345",
            toolchain_digest="toolchain-sha256-digest",
            attributes={"platform": "android"},  # type: ignore[arg-type]
        )

    assert isinstance({"platform": "android"}, Mapping)

    with pytest.raises(ArtifactValidationError):
        ArtifactMetadata(
            source_task="scene.build",
            source_revision="r12345",
            toolchain_digest="toolchain-sha256-digest",
            attributes=(
                ("platform", "android"),
                ("platform", "ios"),
            ),
        )

    with pytest.raises(ArtifactValidationError):
        ArtifactMetadata(
            source_task="scene.build",
            source_revision="r12345",
            toolchain_digest="toolchain-sha256-digest",
            attributes=(("platform", 1),),  # type: ignore[arg-type]
        )

    with pytest.raises(ArtifactValidationError):
        ArtifactMetadata(
            source_task="scene.build",
            source_revision="r12345",
            toolchain_digest="toolchain-sha256-digest",
            attributes=((1, "android"),),  # type: ignore[arg-type]
        )


def test_blob_ref_validates_locator_sha256_and_size() -> None:
    """验证 ``BlobRef`` 接受内容寻址引用，并拒绝临时路径与非法哈希/大小。

    测试无参数和返回值。断言：

    - 接受内容寻址 ``locator``、恰好 64 位小写十六进制 ``sha256`` 与非负 ``size``；
    - 对象不可变；
    - 拒绝空 locator、临时工作目录标记、错误哈希与负数 size；失败抛出
      ``ArtifactValidationError``。

    当前一步仅定义 ``ArtifactMetadata`` 时，导入 ``BlobRef`` 应以
    ``ImportError`` 失败。除导入外不产生外部副作用。
    """
    blob = BlobRef(
        locator=f"sha256:{_VALID_SHA256}",
        sha256=_VALID_SHA256,
        size=1024,
    )
    assert blob.locator == f"sha256:{_VALID_SHA256}"
    assert blob.sha256 == _VALID_SHA256
    assert blob.size == 1024

    blobs_path = BlobRef(
        locator=f"blobs/{_VALID_SHA256}",
        sha256=_VALID_SHA256,
        size=0,
    )
    assert blobs_path.size == 0

    with pytest.raises((AttributeError, TypeError)):
        blob.size = 1  # type: ignore[misc]

    with pytest.raises(ArtifactValidationError):
        BlobRef(locator="", sha256=_VALID_SHA256, size=1)

    with pytest.raises(ArtifactValidationError):
        BlobRef(locator="   ", sha256=_VALID_SHA256, size=1)

    with pytest.raises(ArtifactValidationError):
        BlobRef(locator="/tmp/workdir/blob", sha256=_VALID_SHA256, size=1)

    with pytest.raises(ArtifactValidationError):
        BlobRef(locator="C:\\Temp\\workdir\\blob", sha256=_VALID_SHA256, size=1)

    with pytest.raises(ArtifactValidationError):
        BlobRef(locator="tmp/scratch/blob", sha256=_VALID_SHA256, size=1)

    with pytest.raises(ArtifactValidationError):
        BlobRef(locator="temp/scratch/blob", sha256=_VALID_SHA256, size=1)

    with pytest.raises(ArtifactValidationError):
        BlobRef(
            locator=f"sha256:{_VALID_SHA256}",
            sha256="A" * 64,
            size=1,
        )

    with pytest.raises(ArtifactValidationError):
        BlobRef(
            locator=f"sha256:{_VALID_SHA256}",
            sha256="z" * 64,
            size=1,
        )

    with pytest.raises(ArtifactValidationError):
        BlobRef(
            locator=f"sha256:{_VALID_SHA256}",
            sha256="a" * 63,
            size=1,
        )

    with pytest.raises(ArtifactValidationError):
        BlobRef(
            locator=f"sha256:{_VALID_SHA256}",
            sha256=_VALID_SHA256,
            size=-1,
        )


def test_logical_artifact_validates_paths_and_preserves_collection_semantics() -> None:
    """验证 ``LogicalArtifact`` 路径不变量与依赖/分包集合语义。

    测试无参数和返回值。断言：

    - 接受客户端逻辑路径 ``scene/a.assetbundle``；
    - 拒绝绝对路径（``/`` 开头或盘符）、反斜杠、``.`` / ``..`` 段、空段与尾斜杠；
    - ``dependencies`` 对输入 ``("b", "a", "b")`` 原样保留顺序与重复；
    - ``subpackage_ids`` 规范为无序 ``frozenset({1, 2})``；
    - 对象不可变；失败抛出 ``ArtifactValidationError``。

    当前一步仅定义 ``ArtifactMetadata`` / ``BlobRef`` 时，导入
    ``ArtifactKind`` / ``LogicalArtifact`` 应以 ``ImportError`` 失败。除导入外不
    产生外部副作用。
    """
    metadata = _valid_metadata()
    blob = _valid_blob()
    artifact = LogicalArtifact(
        logical_path="scene/a.assetbundle",
        kind=ArtifactKind.ASSET_BUNDLE,
        blob=blob,
        dependencies=("b", "a", "b"),
        subpackage_ids=cast(frozenset[int], [2, 1, 2]),
        metadata=metadata,
    )
    assert artifact.logical_path == "scene/a.assetbundle"
    assert artifact.kind is ArtifactKind.ASSET_BUNDLE
    assert artifact.blob is blob
    assert artifact.dependencies == ("b", "a", "b")
    assert artifact.subpackage_ids == frozenset({1, 2})
    assert artifact.metadata is metadata

    with pytest.raises((AttributeError, TypeError)):
        artifact.logical_path = "other"  # type: ignore[misc]

    invalid_paths = (
        "/scene/a.assetbundle",
        "C:/scene/a.assetbundle",
        "scene\\a.assetbundle",
        "scene/./a.assetbundle",
        "scene/../a.assetbundle",
        "scene//a.assetbundle",
        "scene/a.assetbundle/",
        "",
    )
    for path in invalid_paths:
        with pytest.raises(ArtifactValidationError):
            LogicalArtifact(
                logical_path=path,
                kind=ArtifactKind.ASSET_BUNDLE,
                blob=blob,
                dependencies=(),
                subpackage_ids=cast(frozenset[int], ()),
                metadata=metadata,
            )

    with pytest.raises(ArtifactValidationError):
        LogicalArtifact(
            logical_path="scene/a.assetbundle",
            kind=ArtifactKind.ASSET_BUNDLE,
            blob=blob,
            dependencies=("/abs",),
            subpackage_ids=cast(frozenset[int], ()),
            metadata=metadata,
        )
