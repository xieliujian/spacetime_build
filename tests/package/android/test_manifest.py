"""AndroidManifest 结构化变换测试。"""

import pytest

from package.platforms.android.manifest import AndroidManifestTransformer


def test_android_manifest_transformer_updates_namespace_version_permissions_and_metadata() -> None:
    """验证 XML API 变换结果确定且权限不重复。"""
    source = b'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="old"><uses-permission android:name="android.permission.INTERNET"/><application android:debuggable="true" /></manifest>'
    result = AndroidManifestTransformer.transform(
        source,
        application_id="com.example.game",
        version_name="1.2.3",
        version_code=8,
        debuggable=False,
        permissions=("android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE"),
        metadata=(("com.example.KEY", "value"),),
    )
    assert b'package="com.example.game"' in result
    assert result.count(b"android.permission.INTERNET") == 1
    assert b"android.permission.ACCESS_NETWORK_STATE" in result
    assert b'android:debuggable="false"' in result


def test_android_manifest_transformer_rejects_metadata_conflict() -> None:
    """验证已有 meta-data 与请求值冲突时失败。"""
    source = b'<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application><meta-data android:name="KEY" android:value="old" /></application></manifest>'
    with pytest.raises(ValueError):
        AndroidManifestTransformer.transform(
            source,
            application_id="com.example.game",
            version_name="1",
            version_code=1,
            debuggable=False,
            metadata=(("KEY", "new"),),
        )
