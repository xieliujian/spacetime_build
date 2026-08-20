"""确定性 PackageManifest payload、工厂与本地 JSON codec。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from core.artifacts import BlobRef
from core.manifest_codec import canonical_json_bytes
from package.model import PackageArtifact

PACKAGE_MANIFEST_SCHEMA_VERSION = 1
_PACKAGE_MANIFEST_TOKEN = object()
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PackageManifestPayload:
    """不含 manifest ID、运行状态和上传 URL 的可复现包体清单 payload。"""

    schema_version: int
    package_id: str
    release_bundle_id: str
    source_revision: str
    unity_version: str
    tool_versions: tuple[tuple[str, str], ...]
    config_digest: str
    artifacts: tuple[tuple[str, BlobRef, str], ...]
    certificate_fingerprint: str | None

    def __post_init__(self) -> None:
        """校验 schema、工具链、产物身份和秘密排除约束。"""
        if self.schema_version != PACKAGE_MANIFEST_SCHEMA_VERSION:
            raise ValueError("不支持的 PackageManifest schema_version")
        for name in ("package_id", "source_revision", "unity_version", "config_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or any(char in value for char in "\r\n"):
                raise ValueError(f"{name} 必须是非空无换行字符串")
        if (
            not isinstance(self.release_bundle_id, str)
            or _SHA256_PATTERN.fullmatch(self.release_bundle_id) is None
        ):
            raise ValueError("release_bundle_id 必须是 SHA256 身份")
        if not isinstance(self.tool_versions, tuple):
            raise TypeError("tool_versions 必须是 tuple")
        for name, version in self.tool_versions:
            if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
                raise ValueError("tool_versions 必须是非空字符串对")
        if not isinstance(self.artifacts, tuple) or not self.artifacts:
            raise ValueError("artifacts 必须是非空 tuple")
        paths: set[str] = set()
        for logical_path, blob, kind in self.artifacts:
            artifact = PackageArtifact(logical_path, blob, kind)
            if artifact.logical_path in paths:
                raise ValueError("PackageManifest 产物路径不得重复")
            paths.add(artifact.logical_path)
        if self.certificate_fingerprint is not None and not self.certificate_fingerprint:
            raise ValueError("certificate_fingerprint 必须是非空字符串或 None")


@dataclass(frozen=True, slots=True)
class PackageManifest:
    """绑定确定性 manifest ID 的不可变包体清单。"""

    manifest_id: str
    payload: PackageManifestPayload
    _factory_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """拒绝公开构造并校验工厂令牌。"""
        if self._factory_token is not _PACKAGE_MANIFEST_TOKEN:
            raise TypeError("PackageManifest 只能通过 PackageManifestFactory.create 创建")


class PackageManifestFactory:
    """从 payload 计算内容寻址 PackageManifest。"""

    @staticmethod
    def create(payload: PackageManifestPayload) -> PackageManifest:
        """规范编码 payload 并计算 SHA256 manifest ID。"""
        if not isinstance(payload, PackageManifestPayload):
            raise TypeError("payload 必须是 PackageManifestPayload")
        digest = hashlib.sha256(canonical_json_bytes(_payload_dict(payload))).hexdigest()
        return PackageManifest(digest, payload, _PACKAGE_MANIFEST_TOKEN)

    @staticmethod
    def bind(manifest_id: str, payload: PackageManifestPayload) -> PackageManifest:
        """验证外部 ID 与 payload 一致后绑定清单。"""
        if not isinstance(payload, PackageManifestPayload):
            raise TypeError("payload 必须是 PackageManifestPayload")
        manifest = PackageManifestFactory.create(payload)
        if manifest.manifest_id != manifest_id:
            raise ValueError("manifest_id 与 payload 不一致")
        return manifest


def _payload_dict(payload: PackageManifestPayload) -> dict[str, object]:
    """将 payload 转为规范 JSON 字典。"""
    artifacts = sorted(payload.artifacts, key=lambda item: item[0].encode("utf-8"))
    return {
        "artifacts": [
            {
                "blob": {"locator": blob.locator, "sha256": blob.sha256, "size": blob.size},
                "kind": kind,
                "logical_path": path,
            }
            for path, blob, kind in artifacts
        ],
        "certificate_fingerprint": payload.certificate_fingerprint,
        "config_digest": payload.config_digest,
        "package_id": payload.package_id,
        "release_bundle_id": payload.release_bundle_id,
        "schema_version": payload.schema_version,
        "source_revision": payload.source_revision,
        "tool_versions": [
            list(item)
            for item in sorted(payload.tool_versions, key=lambda pair: pair[0].encode("utf-8"))
        ],
        "unity_version": payload.unity_version,
    }


def write_package_manifest(manifest: PackageManifest, path: Path) -> None:
    """以规范 JSON 写入 PackageManifest；调用方负责目录权限。"""
    if not isinstance(manifest, PackageManifest):
        raise TypeError("manifest 必须是 PackageManifest")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        canonical_json_bytes(
            {"manifest_id": manifest.manifest_id, "payload": _payload_dict(manifest.payload)}
        )
    )


def read_package_manifest(path: Path) -> PackageManifest:
    """读取 JSON、重算 ID 并拒绝陈旧清单。

    参数：
        path: 本地 PackageManifest JSON 路径。

    返回：
        经 payload factory 重算并核对 ID 的不可变清单。

    异常：
        JSON、字段、Blob 或 ID 不合法时抛出 ``ValueError`` / ``TypeError``。

    约束与副作用：
        只读本地文件；不信任文件中的 manifest_id，也不执行包体工具。
    """
    document_object = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document_object, dict):
        raise ValueError("PackageManifest 根对象缺少 manifest_id")
    document = cast(dict[str, object], document_object)
    if not isinstance(document.get("manifest_id"), str):
        raise ValueError("PackageManifest 根对象缺少 manifest_id")
    raw_payload = document.get("payload")
    if not isinstance(raw_payload, dict):
        raise ValueError("PackageManifest 缺少 payload 对象")
    raw_payload = cast(dict[str, object], raw_payload)
    raw_artifacts = raw_payload.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ValueError("payload.artifacts 必须是列表")
    raw_artifacts = cast(list[object], raw_artifacts)
    artifacts: list[tuple[str, BlobRef, str]] = []
    for raw_object in raw_artifacts:
        if not isinstance(raw_object, dict):
            raise ValueError("payload.artifacts 每项必须是对象")
        raw = cast(dict[str, object], raw_object)
        raw_blob = raw.get("blob")
        if not isinstance(raw_blob, dict):
            raise ValueError("artifact.blob 必须是对象")
        raw_blob = cast(dict[str, object], raw_blob)
        artifacts.append(
            (
                _require_string(raw.get("logical_path"), "artifact.logical_path"),
                BlobRef(
                    _require_string(raw_blob.get("locator"), "artifact.blob.locator"),
                    _require_string(raw_blob.get("sha256"), "artifact.blob.sha256"),
                    _require_int(raw_blob.get("size"), "artifact.blob.size"),
                ),
                _require_string(raw.get("kind"), "artifact.kind"),
            )
        )
    raw_tools = raw_payload.get("tool_versions")
    if not isinstance(raw_tools, list):
        raise ValueError("payload.tool_versions 必须是列表")
    raw_tools = cast(list[object], raw_tools)
    tools: list[tuple[str, str]] = []
    for item_object in raw_tools:
        if not isinstance(item_object, list):
            raise ValueError("tool_versions 每项必须是长度为 2 的列表")
        item = cast(list[object], item_object)
        if len(item) != 2:
            raise ValueError("tool_versions 每项必须是长度为 2 的列表")
        tools.append(
            (
                _require_string(item[0], "tool_versions.name"),
                _require_string(item[1], "tool_versions.version"),
            )
        )
    payload = PackageManifestPayload(
        _require_int(raw_payload.get("schema_version"), "payload.schema_version"),
        _require_string(raw_payload.get("package_id"), "payload.package_id"),
        _require_string(raw_payload.get("release_bundle_id"), "payload.release_bundle_id"),
        _require_string(raw_payload.get("source_revision"), "payload.source_revision"),
        _require_string(raw_payload.get("unity_version"), "payload.unity_version"),
        tuple(tools),
        _require_string(raw_payload.get("config_digest"), "payload.config_digest"),
        tuple(artifacts),
        _optional_string(raw_payload.get("certificate_fingerprint"), "certificate_fingerprint"),
    )
    return PackageManifestFactory.bind(
        _require_string(document.get("manifest_id"), "manifest_id"), payload
    )


def _require_string(value: object, field_name: str) -> str:
    """读取非空字符串字段。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def _require_int(value: object, field_name: str) -> int:
    """读取非布尔整数字段。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是 int")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    """读取可选字符串字段。"""
    if value is None:
        return None
    return _require_string(value, field_name)
