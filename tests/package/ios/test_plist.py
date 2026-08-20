"""验证 iOS Info.plist 的结构化变换、去重、删除和确定性编码。"""

from __future__ import annotations

import plistlib
from typing import Any, cast

import pytest

from package.platforms.ios.plist import PlistTransformer


def _plist_bytes(value: dict[str, object]) -> bytes:
    """把测试用字典编码为 XML plist 输入。"""
    return plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=False)


def _plist_value(source: bytes) -> dict[str, object]:
    """解析变换结果，供测试断言结构化字段。"""
    value: object = plistlib.loads(source)
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_transform_updates_bundle_versions_merges_url_types_and_removes_deprecated_keys() -> None:
    """验证 bundle/version、URL schemes、重复 URL 类型和废弃键都按结构处理。"""
    source = _plist_bytes(
        {
            "CFBundleVersion": "old-build",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleIdentifier": "com.example.old",
            "CFBundleURLTypes": [
                {
                    "CFBundleURLSchemes": ["zeta", "game", "game"],
                    "CFBundleTypeRole": "Editor",
                    "CFBundleURLName": "com.example.game",
                },
                {
                    "CFBundleURLName": "com.example.game",
                    "CFBundleTypeRole": "Editor",
                    "CFBundleURLSchemes": ["alpha", "zeta"],
                },
                {"CFBundleURLSchemes": ["other"], "CFBundleURLName": "other"},
            ],
            "LegacyTopLevelKey": "remove",
            "Nested": {"LegacyTopLevelKey": "keep"},
        }
    )

    result = PlistTransformer.transform(
        source,
        bundle_id="com.example.game",
        version="1.2.3",
        build_number="42",
        url_schemes=("beta", "game"),
        deprecated_keys=("LegacyTopLevelKey",),
    )

    document = _plist_value(result)
    assert document["CFBundleIdentifier"] == "com.example.game"
    assert document["CFBundleShortVersionString"] == "1.2.3"
    assert document["CFBundleVersion"] == "42"
    assert "LegacyTopLevelKey" not in document
    assert document["Nested"] == {"LegacyTopLevelKey": "keep"}
    assert document["CFBundleURLTypes"] == [
        {
            "CFBundleURLName": "com.example.game",
            "CFBundleTypeRole": "Editor",
            "CFBundleURLSchemes": ["alpha", "beta", "game", "zeta"],
        },
        {"CFBundleURLName": "other", "CFBundleURLSchemes": ["other"]},
    ]


def test_transform_adds_url_schemes_when_url_types_are_missing() -> None:
    """验证没有现有 URL 类型时创建唯一的 scheme 容器并去重排序。"""
    result = PlistTransformer.transform(
        _plist_bytes({"CFBundleIdentifier": "com.example.game"}),
        url_schemes=("zeta", "alpha", "zeta"),
    )

    document = _plist_value(result)
    assert document["CFBundleURLTypes"] == [{"CFBundleURLSchemes": ["alpha", "zeta"]}]


@pytest.mark.parametrize(
    ("source", "kwargs"),
    [
        (b"not a plist", {}),
        (_plist_bytes([]), {}),  # pyright: ignore[reportArgumentType]
        (_plist_bytes({"CFBundleIdentifier": "ok"}), {"bundle_id": 7}),
        (_plist_bytes({"CFBundleIdentifier": "ok"}), {"version": 7}),
        (_plist_bytes({"CFBundleIdentifier": "ok"}), {"build_number": True}),
        (_plist_bytes({"CFBundleIdentifier": "ok"}), {"url_schemes": ("ok", 2)}),
        (_plist_bytes({"CFBundleIdentifier": "ok"}), {"deprecated_keys": ("",)}),
    ],
)
def test_transform_rejects_invalid_plist_or_option_types(
    source: bytes, kwargs: dict[str, Any]
) -> None:
    """验证 plist 根节点和全部变换参数都不能静默接受非法类型。"""
    with pytest.raises((TypeError, ValueError)):
        PlistTransformer.transform(source, **kwargs)


def test_transform_output_is_deterministic_and_idempotent() -> None:
    """验证同一结构反复变换得到完全相同的 XML 字节。"""
    source = _plist_bytes(
        {
            "ZKey": "z",
            "CFBundleURLTypes": [
                {"CFBundleURLSchemes": ["b", "a", "b"], "CFBundleTypeRole": "Viewer"}
            ],
            "AKey": "a",
        }
    )
    kwargs: dict[str, Any] = {
        "bundle_id": "com.example.game",
        "version": "2.0.0",
        "build_number": "8",
        "url_schemes": ("c", "a"),
    }

    first = PlistTransformer.transform(source, **kwargs)
    second = PlistTransformer.transform(source, **kwargs)
    repeated = PlistTransformer.transform(first)

    assert first == second
    assert repeated == first


def test_transform_rejects_duplicate_xml_dictionary_keys() -> None:
    """验证 XML plist 中重复字典键不会被解析器静默覆盖。"""
    source = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>first</string>
<key>CFBundleIdentifier</key><string>second</string>
</dict></plist>"""

    with pytest.raises(ValueError, match="重复 key"):
        PlistTransformer.transform(source)
