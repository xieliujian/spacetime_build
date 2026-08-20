"""SDK catalog 严格 TOML schema 测试。"""

import pytest

from sdk.catalog import SdkCatalog


def test_catalog_loads_descriptor_and_sorts_operations() -> None:
    """验证 TOML descriptor 只映射为结构化数据并稳定排序。"""
    payload = b"""\
[[descriptors]]
sdk_id = "com.example.sdk"
version = "1.0.0"
platform = "android"
stage = "pre_build"
inputs = [{locator = "blobs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", size = 4}]
outputs = ["res/sdk.xml"]
secret_refs = ["secret://sdk/key"]
validation_rules = ["manifest-present"]

[[descriptors.operations]]
kind = "set_property"
target = "manifest/appId"
value = "com.example"
conflict_key = "app-id"
"""

    catalog = SdkCatalog.from_toml(payload)

    assert catalog.descriptors[0].platform.value == "android"
    assert catalog.descriptors[0].operations[0].target == "manifest/appId"


def test_catalog_rejects_unknown_and_executable_fields() -> None:
    """验证未知字段以及 module/script/command 字段都在加载边界失败。"""
    for field in ("unknown", "module", "script", "command"):
        payload = f"""\
[[descriptors]]
sdk_id = "sdk"
version = "1.0.0"
platform = "android"
stage = "pre_build"
inputs = []
outputs = []
secret_refs = []
validation_rules = []
{field} = "blocked"
""".encode()
        with pytest.raises(ValueError):
            SdkCatalog.from_toml(payload)
