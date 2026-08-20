"""提供 iOS Info.plist 的结构化、可重复变换能力。

本模块只在内存中解析和编码 plist，不执行 macOS 工具，也不通过字符串替换修改
文件。变换范围集中在包标识、版本字段、URL schemes 以及调用方明确列出的顶层
废弃键，便于后续 Xcode 导出流程复用并审计每一项输入。
"""

from __future__ import annotations

import plistlib
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from typing import Any, cast


class PlistTransformer:
    """以结构化方式变换 iOS Info.plist，并生成确定性的 XML bytes。"""

    @staticmethod
    def transform(
        source: bytes,
        *,
        bundle_id: str | None = None,
        version: str | None = None,
        build_number: str | None = None,
        url_schemes: Iterable[str] = (),
        deprecated_keys: Iterable[str] = (),
        remove_keys: Iterable[str] | None = None,
    ) -> bytes:
        """返回应用变换后的 Info.plist，不修改输入并保证重复调用结果一致。

        参数：
            source: XML 或 binary plist 的原始 bytes；根节点必须是字典。
            bundle_id: 可选 ``CFBundleIdentifier``，为空字符串和非字符串都会被拒绝。
            version: 可选 ``CFBundleShortVersionString``，通常是面向用户的版本号。
            build_number: 可选 ``CFBundleVersion``，必须是非空字符串。
            url_schemes: 需要加入 URL 类型的 scheme 可迭代对象；重复项会去重并排序。
            deprecated_keys: 要从根字典删除的顶层键；嵌套字典中的同名键不会删除。
            remove_keys: ``deprecated_keys`` 的兼容别名；提供时与其合并。

        返回：
            使用 plist XML 格式和稳定键排序编码的 bytes。

        异常：
            source 不是 bytes、plist 无法解析、根节点不是字典、字典含重复键或任一
            变换参数类型非法时抛出 ``TypeError`` 或 ``ValueError``。

        约束与副作用：
            仅操作解析后的 Python plist 结构，不访问文件系统、不调用外部工具；重复
            ``CFBundleURLTypes`` 会按非 schemes 字段合并，URL scheme 按 UTF-8 字节排序。
        """
        if not isinstance(source, bytes):
            raise TypeError("source 必须是 bytes")
        _reject_duplicate_xml_keys(source)
        try:
            document = plistlib.loads(source)
        except (OSError, plistlib.InvalidFileException, ValueError) as exc:
            raise ValueError("Info.plist 无法解析") from exc
        if not isinstance(document, dict):
            raise ValueError("Info.plist 根节点必须是字典")

        _validate_optional_string(bundle_id, "bundle_id")
        _validate_optional_string(version, "version")
        _validate_optional_string(build_number, "build_number")
        normalized_schemes = _normalize_strings(url_schemes, "url_schemes")
        normalized_deprecated = _normalize_strings(deprecated_keys, "deprecated_keys")
        if remove_keys is not None:
            normalized_deprecated += _normalize_strings(remove_keys, "remove_keys")

        transformed: dict[str, Any] = dict(cast(dict[str, Any], document))
        if bundle_id is not None:
            transformed["CFBundleIdentifier"] = bundle_id
        if version is not None:
            transformed["CFBundleShortVersionString"] = version
        if build_number is not None:
            transformed["CFBundleVersion"] = build_number

        transformed["CFBundleURLTypes"] = _normalize_url_types(
            transformed.get("CFBundleURLTypes"), normalized_schemes
        )
        if not normalized_schemes and "CFBundleURLTypes" not in document:
            transformed.pop("CFBundleURLTypes", None)
        for key in set(normalized_deprecated):
            transformed.pop(key, None)

        try:
            return plistlib.dumps(
                transformed,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Info.plist 含有无法编码的值") from exc


def _validate_optional_string(value: str | None, field_name: str) -> None:
    """校验可选的 plist 字符串字段，``None`` 表示保持源值。"""
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{field_name} 必须是非空字符串或 None")


def _normalize_strings(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    """校验字符串集合并按 UTF-8 字节序去重排序。"""
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} 必须是字符串可迭代对象，而不是单个字符串")
    try:
        items = tuple(values)
    except TypeError as exc:
        raise TypeError(f"{field_name} 必须是字符串可迭代对象") from exc
    if not all(isinstance(item, str) and item for item in items):
        raise ValueError(f"{field_name} 的每一项必须是非空字符串")
    return tuple(sorted(set(items), key=lambda item: item.encode("utf-8")))


def _normalize_url_types(
    raw_url_types: object,
    requested_schemes: tuple[str, ...],
) -> list[dict[str, Any]]:
    """校验、合并和规范化 ``CFBundleURLTypes`` 数组。"""
    if raw_url_types is None:
        url_types: list[dict[str, Any]] = []
    elif isinstance(raw_url_types, list):
        raw_items = cast(list[object], raw_url_types)
        url_types = [_normalize_url_type(item) for item in raw_items]
    else:
        raise ValueError("CFBundleURLTypes 必须是数组")

    merged: list[dict[str, Any]] = []
    indexes: dict[bytes, int] = {}
    for url_type in url_types:
        identity = _url_type_identity(url_type)
        existing_index = indexes.get(identity)
        if existing_index is None:
            indexes[identity] = len(merged)
            merged.append(url_type)
            continue
        existing = merged[existing_index]
        existing_schemes = existing.get("CFBundleURLSchemes", [])
        duplicate_schemes = url_type.get("CFBundleURLSchemes", [])
        existing["CFBundleURLSchemes"] = _normalize_scheme_values(
            [*existing_schemes, *duplicate_schemes]
        )

    if requested_schemes:
        if merged:
            current = merged[0].get("CFBundleURLSchemes", [])
            merged[0]["CFBundleURLSchemes"] = _normalize_scheme_values(
                [*current, *requested_schemes]
            )
        else:
            merged.append({"CFBundleURLSchemes": list(requested_schemes)})
    return merged


def _normalize_url_type(raw_url_type: object) -> dict[str, Any]:
    """校验单个 URL 类型字典并规范化其 schemes。"""
    if not isinstance(raw_url_type, dict):
        raise ValueError("CFBundleURLTypes 的每一项必须是字典")
    url_type: dict[str, Any] = dict(cast(dict[str, Any], raw_url_type))
    schemes = url_type.get("CFBundleURLSchemes", [])
    if not isinstance(schemes, list):
        raise ValueError("CFBundleURLSchemes 必须是数组")
    url_type["CFBundleURLSchemes"] = _normalize_scheme_values(cast(list[object], schemes))
    return url_type


def _normalize_scheme_values(values: Iterable[object]) -> list[str]:
    """校验 URL scheme 列表并按 UTF-8 字节序去重排序。"""
    schemes = tuple(values)
    normalized: set[str] = set()
    for scheme in schemes:
        if not isinstance(scheme, str) or not scheme:
            raise ValueError("CFBundleURLSchemes 的每一项必须是非空字符串")
        normalized.add(scheme)
    return sorted(normalized, key=lambda scheme: scheme.encode("utf-8"))


def _url_type_identity(url_type: dict[str, Any]) -> bytes:
    """把 URL 类型的非 scheme 字段编码为稳定身份键。"""
    identity = {key: value for key, value in url_type.items() if key != "CFBundleURLSchemes"}
    try:
        return plistlib.dumps(identity, fmt=plistlib.FMT_BINARY, sort_keys=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("CFBundleURLTypes 含有无法编码的字段") from exc


def _reject_duplicate_xml_keys(source: bytes) -> None:
    """用 XML 结构解析器拒绝 plist 字典中的重复 key。"""
    if b"<plist" not in source:
        return
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return
    _walk_xml_node(root)


def _walk_xml_node(node: ET.Element) -> None:
    """递归检查 XML plist 的每个字典节点并继续检查嵌套容器。"""
    if node.tag == "dict":
        keys: list[str] = []
        children = list(node)
        for index, child in enumerate(children):
            if child.tag == "key":
                if child.text is None:
                    raise ValueError("plist 字典 key 不能为空")
                if child.text in keys:
                    raise ValueError(f"plist 字典存在重复 key: {child.text}")
                keys.append(child.text)
            elif index == 0 or children[index - 1].tag != "key":
                raise ValueError("plist 字典必须按 key/value 成对出现")
    for child in node:
        _walk_xml_node(child)
