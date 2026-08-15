"""验证 ``ReleaseEntry`` 传输身份分离与对象来源语义。

本模块覆盖第二阶段 Task 12：发布条目将原始 MD5/大小与传输 SHA256/大小独立建模，
并对 ``CURRENT_UPLOAD`` / ``HISTORICAL`` 的 object_version 哨兵规则做构造期校验。
测试不访问 SVN、Unity、Jenkins、CDN，也不导入 ``compatibility``。
"""

from __future__ import annotations

import pytest

from core.artifacts import BlobRef
from core.errors import PublishError
from release.entries import (
    ReleaseEntry,
    ReleaseObjectOrigin,
    ResourceVariant,
)

_SOURCE_SHA256 = "a" * 64
_TRANSFER_SHA256 = "b" * 64
_SOURCE_MD5 = "c" * 32
_INT32_MAX = 2**31 - 1


def _source_blob(*, size: int = 2048) -> BlobRef:
    """构造测试用源内容 ``BlobRef``。

    参数：
        size: Blob 字节大小，默认 ``2048``。

    返回：
        合法内容寻址源 Blob 引用。
    """
    return BlobRef(
        locator=f"sha256:{_SOURCE_SHA256}",
        sha256=_SOURCE_SHA256,
        size=size,
    )


def _transfer_blob(*, size: int = 1024) -> BlobRef:
    """构造测试用传输内容 ``BlobRef``。

    参数：
        size: Blob 字节大小，默认 ``1024``。

    返回：
        与源 Blob 哈希不同的合法传输 Blob 引用。
    """
    return BlobRef(
        locator=f"sha256:{_TRANSFER_SHA256}",
        sha256=_TRANSFER_SHA256,
        size=size,
    )


def _make_entry(
    *,
    logical_path: str = "scene/a.assetbundle",
    variant: ResourceVariant = ResourceVariant.MAIN,
    source_md5: str = _SOURCE_MD5,
    original_size: int = 2048,
    transfer_size: int = 1024,
    list_version: int = 1,
    object_version: str = "{current}",
    file_url: str = "https://cdn.example/v/{current}/scene/a.assetbundle",
    subpackage_flag: int = 0,
    object_origin: ReleaseObjectOrigin = ReleaseObjectOrigin.CURRENT_UPLOAD,
) -> ReleaseEntry:
    """用可覆盖字段构造 ``ReleaseEntry`` 便于断言边界。

    参数：
        logical_path: 客户端逻辑路径。
        variant: 主/低清变体。
        source_md5: 原始内容 MD5。
        original_size: 原始字节大小。
        transfer_size: 传输字节大小。
        list_version: 正 Int32 列表版本。
        object_version: 对象版本哨兵或历史版本串。
        file_url: 发布 URL。
        subpackage_flag: 非负 Int32 分包标志。
        object_origin: 对象来源枚举。

    返回：
        构造完成的 ``ReleaseEntry`` 实例。

    异常：
        字段非法时由 ``ReleaseEntry`` 抛出领域异常。
    """
    # Blob 大小保持合法，便于单独触发 ReleaseEntry 对 original/transfer size 的校验。
    return ReleaseEntry(
        logical_path=logical_path,
        variant=variant,
        source_blob=_source_blob(),
        source_md5=source_md5,
        original_size=original_size,
        transfer_blob=_transfer_blob(),
        transfer_size=transfer_size,
        list_version=list_version,
        object_version=object_version,
        file_url=file_url,
        subpackage_flag=subpackage_flag,
        object_origin=object_origin,
    )


def test_release_entry_separates_transfer_identity_and_enforces_object_origin_rules() -> None:
    """验证传输身份独立，并强制 main/low 本次上传与历史对象来源规则。

    测试无参数和返回值。断言：

    - ``source_md5`` / ``original_size`` 与 ``transfer_blob.sha256`` /
      ``transfer_size`` 可独立取值且互不影响；
    - ``list_version`` 须为正 Int32；``original_size`` / ``transfer_size`` /
      ``subpackage_flag`` 须为非负 Int32；逻辑路径与 object_version 合法；
    - ``CURRENT_UPLOAD`` + ``MAIN`` 的 ``object_version`` 必须为 ``{current}``
      或正整数 FileListNo；
    - ``CURRENT_UPLOAD`` + ``LOW`` 的 ``object_version`` 必须为 ``{current}_low``
      或 ``{n}_low``；
    - ``HISTORICAL`` 允许保留合法历史 ``object_version`` / URL；
    - 对象不可变；对象来源规则违反时抛出 ``PublishError``。

    当 ``release.entries`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入外不产生外部副作用。
    """
    main_current = _make_entry(
        variant=ResourceVariant.MAIN,
        object_version="{current}",
        object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        original_size=4096,
        transfer_size=1500,
        source_md5="d" * 32,
    )
    assert main_current.source_md5 == "d" * 32
    assert main_current.original_size == 4096
    assert main_current.transfer_blob.sha256 == _TRANSFER_SHA256
    assert main_current.transfer_size == 1500
    assert main_current.source_md5 != main_current.transfer_blob.sha256
    assert main_current.original_size != main_current.transfer_size
    assert main_current.list_version == 1
    assert main_current.object_version == "{current}"
    assert main_current.variant is ResourceVariant.MAIN
    assert main_current.object_origin is ReleaseObjectOrigin.CURRENT_UPLOAD

    low_current = _make_entry(
        variant=ResourceVariant.LOW,
        object_version="{current}_low",
        object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        file_url="https://cdn.example/v/{current}_low/scene/a.assetbundle",
        subpackage_flag=_INT32_MAX,
        list_version=_INT32_MAX,
    )
    assert low_current.object_version == "{current}_low"
    assert low_current.variant is ResourceVariant.LOW
    assert low_current.subpackage_flag == _INT32_MAX
    assert low_current.list_version == _INT32_MAX

    historical = _make_entry(
        object_version="20240101_120000",
        file_url="https://cdn.example/v/20240101_120000/scene/a.assetbundle",
        object_origin=ReleaseObjectOrigin.HISTORICAL,
        list_version=42,
        subpackage_flag=3,
    )
    assert historical.object_version == "20240101_120000"
    assert historical.file_url.endswith("20240101_120000/scene/a.assetbundle")
    assert historical.object_origin is ReleaseObjectOrigin.HISTORICAL

    with pytest.raises((AttributeError, TypeError)):
        main_current.object_version = "mutated"  # type: ignore[misc]

    with pytest.raises(PublishError):
        _make_entry(
            variant=ResourceVariant.MAIN,
            object_version="{current}_low",
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )

    with pytest.raises(PublishError):
        _make_entry(
            variant=ResourceVariant.LOW,
            object_version="{current}",
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
            file_url="https://cdn.example/v/{current}_low/scene/a.assetbundle",
        )

    # 已展开 FileListNo 合法；非哨兵/非正整数形式非法。
    concrete_main = _make_entry(
        variant=ResourceVariant.MAIN,
        object_version="123",
        object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
    )
    assert concrete_main.object_version == "123"
    concrete_low = _make_entry(
        variant=ResourceVariant.LOW,
        object_version="123_low",
        object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        file_url="https://cdn.example/v/123_low/scene/a.assetbundle",
    )
    assert concrete_low.object_version == "123_low"

    with pytest.raises(PublishError):
        _make_entry(
            variant=ResourceVariant.MAIN,
            object_version="not-a-filelistno",
            object_origin=ReleaseObjectOrigin.CURRENT_UPLOAD,
        )

    with pytest.raises(PublishError):
        _make_entry(list_version=0)

    with pytest.raises(PublishError):
        _make_entry(list_version=-1)

    with pytest.raises(PublishError):
        _make_entry(list_version=_INT32_MAX + 1)

    with pytest.raises(PublishError):
        _make_entry(original_size=-1)

    with pytest.raises(PublishError):
        _make_entry(transfer_size=-1)

    with pytest.raises(PublishError):
        _make_entry(subpackage_flag=-1)

    with pytest.raises(PublishError):
        _make_entry(original_size=_INT32_MAX + 1)

    with pytest.raises(PublishError):
        _make_entry(logical_path="/abs/path")

    with pytest.raises(PublishError):
        _make_entry(source_md5="not-md5")

    with pytest.raises(PublishError):
        _make_entry(
            object_version="",
            object_origin=ReleaseObjectOrigin.HISTORICAL,
            file_url="https://cdn.example/v/hist/scene/a.assetbundle",
        )
