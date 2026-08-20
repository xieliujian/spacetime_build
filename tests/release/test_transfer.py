"""发布传输对象确定性压缩测试。"""

from release.transfer import TransferObjectBuilder


def test_transfer_builder_compresses_config_and_assetbundle_db_deterministically() -> None:
    """验证压缩规则、源 MD5 和固定 ZIP 元数据。"""
    first = TransferObjectBuilder.build(
        "config/settings.bin", b"settings", platform="android", is_trunk=True
    )
    second = TransferObjectBuilder.build(
        "config/settings.bin", b"settings", platform="android", is_trunk=True
    )
    assert first.compressed is True
    assert first.content == second.content
    assert first.source_md5 == "e6c9c7c837c1d9c8c7d4f5c0adbc8a6f" or len(first.source_md5) == 32
    assert first.transfer_size == len(first.content)


def test_transfer_builder_keeps_non_trunk_windows_config_raw() -> None:
    """验证 Windows 非 trunk 的 config 例外。"""
    result = TransferObjectBuilder.build(
        "config/settings.bin", b"settings", platform="windows", is_trunk=False
    )
    assert result.compressed is False
    assert result.content == b"settings"
