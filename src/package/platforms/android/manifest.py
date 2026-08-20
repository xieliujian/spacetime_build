"""使用 XML API 确定性变换 AndroidManifest。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ET.register_namespace("android", ANDROID_NS)


class AndroidManifestTransformer:
    """在 XML 结构层更新包名、版本、权限和 meta-data。"""

    @staticmethod
    def transform(
        source: bytes,
        *,
        application_id: str,
        version_name: str,
        version_code: int,
        debuggable: bool,
        permissions: tuple[str, ...] = (),
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> bytes:
        """返回确定性 AndroidManifest XML。

        参数：
            source: UTF-8 AndroidManifest XML bytes。
            application_id: 根 manifest 的 package 值。
            version_name: android:versionName。
            version_code: 正 Int32 android:versionCode。
            debuggable: application 的 android:debuggable。
            permissions: 需要存在的权限名集合。
            metadata: android:name 到 android:value 的显式键值。

        返回：
            UTF-8 XML bytes；不写文件。

        异常：
            XML、字段、权限重复或 meta-data 冲突时抛出 ``ValueError``。

        约束与副作用：
            只通过 XML 元素操作，不做文本替换；权限和 meta-data 输出按名称排序。
        """
        if not isinstance(source, bytes):
            raise TypeError("source 必须是 bytes")
        if not isinstance(application_id, str) or not application_id:
            raise ValueError("application_id 必须是非空字符串")
        if not isinstance(version_name, str) or not version_name:
            raise ValueError("version_name 必须是非空字符串")
        if not isinstance(version_code, int) or isinstance(version_code, bool) or version_code <= 0:
            raise ValueError("version_code 必须是正整数")
        if not isinstance(debuggable, bool):
            raise TypeError("debuggable 必须是 bool")
        try:
            root = ET.fromstring(source)
        except ET.ParseError as exc:
            raise ValueError("AndroidManifest XML 无法解析") from exc
        root.set("package", application_id)
        root.set(_android("versionName"), version_name)
        root.set(_android("versionCode"), str(version_code))
        application = root.find("application")
        if application is None:
            application = ET.SubElement(root, "application")
        application.set(_android("debuggable"), "true" if debuggable else "false")
        existing_permissions = {
            item.get(_android("name")) for item in root.findall("uses-permission")
        }
        for permission in sorted(set(permissions)):
            if not isinstance(permission, str) or not permission:
                raise ValueError("permission 必须是非空字符串")
            if permission not in existing_permissions:
                ET.SubElement(root, "uses-permission", {_android("name"): permission})
        existing_metadata = {
            item.get(_android("name")): item for item in application.findall("meta-data")
        }
        for name, value in sorted(metadata, key=lambda pair: pair[0].encode("utf-8")):
            if not isinstance(name, str) or not name or not isinstance(value, str):
                raise ValueError("metadata 必须是字符串键值")
            item = existing_metadata.get(name)
            if item is not None and item.get(_android("value")) != value:
                raise ValueError(f"AndroidManifest meta-data 冲突: {name}")
            if item is None:
                ET.SubElement(
                    application, "meta-data", {_android("name"): name, _android("value"): value}
                )
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _android(name: str) -> str:
    """返回 Android XML 命名空间限定名。"""
    return "{" + ANDROID_NS + "}" + name
