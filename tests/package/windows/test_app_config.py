"""Windows appconfig 结构化变换测试。"""

from pathlib import Path

import pytest

from package.platforms.windows.app_config import WindowsAppConfig, WindowsAppConfigTransformer


def _config() -> WindowsAppConfig:
    """构造测试用的固定 appconfig 请求。"""
    return WindowsAppConfig(
        release_bundle_id="a" * 64,
        release_entry="versions/1.2.3/entry.json",
        branch="release/2026.08",
        version_name="1.2.3",
        version_code=42,
    )


def test_windows_appconfig_transformer_returns_deterministic_whitelisted_json() -> None:
    """验证入口、版本和 branch 写入白名单字段且 key 顺序确定。"""
    source = b'{"branch":"old","version_code":1}'
    result = WindowsAppConfigTransformer.transform(source, _config())

    assert result == (
        b'{"branch":"release/2026.08","release_bundle_id":"'
        + b"a" * 64
        + b'","release_entry":"versions/1.2.3/entry.json","version_code":42,"version_name":"1.2.3"}'
    )


def test_windows_appconfig_transformer_rejects_unknown_and_duplicate_keys() -> None:
    """验证未知字段与重复 JSON 键不能静默进入包体配置。"""
    with pytest.raises(ValueError, match="未知"):
        WindowsAppConfigTransformer.transform(b'{"unexpected":true}', _config())
    with pytest.raises(ValueError, match="重复"):
        WindowsAppConfigTransformer.transform(b'{"branch":"a","branch":"b"}', _config())


def test_windows_appconfig_write_is_atomic_and_rejects_bad_entry(tmp_path: Path) -> None:
    """验证 appconfig 原子写入，并拒绝非法 release entry。"""
    path = tmp_path / "appconfig.json"
    WindowsAppConfigTransformer.write(path, _config())
    assert path.read_bytes() == WindowsAppConfigTransformer.transform(b"{}", _config())

    with pytest.raises(ValueError):
        WindowsAppConfig(
            "a" * 64,
            "../entry.json",
            "release",
            "1.0",
            1,
        )
