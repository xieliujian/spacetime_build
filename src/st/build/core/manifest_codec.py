"""BuildManifest payload 规范编解码、工厂与严格本地读写。

本模块负责将 ``BuildManifestPayload`` 编码为确定性 JSON 字节，通过
``BuildManifestFactory`` 计算 ``manifest_id``，并以临时文件加 ``Path.replace()``
原子写入、读取时重算 ID 做完整性校验。领域语义上声明为无序的集合（artifacts、
metadata attributes、subpackage_ids）在编码边界按 UTF-8 字节键排序；有序依赖
元组保留顺序与重复。禁止用文件中的 ID 直接构造 ``BuildManifest``。导入本模块
不执行构建，也不访问 SVN、Unity、Jenkins 或 CDN。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

from st.build.core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    BlobRef,
    LogicalArtifact,
)
from st.build.core.build_records import (
    BuildManifest,
    BuildManifestPayload,
    bind_build_manifest,
)
from st.build.core.errors import ArtifactValidationError


def _sorted_attributes(
    attributes: tuple[tuple[str, str], ...],
) -> list[list[str]]:
    """将 metadata attributes 按 key 的 UTF-8 字节序排序。

    参数：
        attributes: ``(key, value)`` 字符串对元组。

    返回：
        排序后的 ``[[key, value], ...]`` 列表，供 JSON 编码使用。

    异常：
        无；调用方保证输入已通过 ``ArtifactMetadata`` 校验。

    约束与副作用：
        纯函数；仅无序 attributes 边界排序，不修改其他字段。
    """
    return [
        [key, value] for key, value in sorted(attributes, key=lambda pair: pair[0].encode("utf-8"))
    ]


def _artifact_dict(artifact: LogicalArtifact) -> dict[str, object]:
    """将单个逻辑产物转为可规范编码的字典。

    参数：
        artifact: 已校验的 ``LogicalArtifact``。

    返回：
        含逻辑路径、kind、blob、有序依赖、已排序分包 ID 与 metadata 的字典。

    异常：
        无；调用方保证产物合法。

    约束与副作用：
        依赖保持原序与重复；``subpackage_ids`` 与 attributes 在此边界规范化。
        纯函数，无 I/O。
    """
    return {
        "logical_path": artifact.logical_path,
        "kind": artifact.kind.value,
        "blob": {
            "locator": artifact.blob.locator,
            "sha256": artifact.blob.sha256,
            "size": artifact.blob.size,
        },
        # 有序集合：保留依赖顺序与重复，禁止排序或去重。
        "dependencies": list(artifact.dependencies),
        # 无序集合：按整数自然序输出，保证确定性。
        "subpackage_ids": sorted(artifact.subpackage_ids),
        "metadata": {
            "source_task": artifact.metadata.source_task,
            "source_revision": artifact.metadata.source_revision,
            "toolchain_digest": artifact.metadata.toolchain_digest,
            "attributes": _sorted_attributes(artifact.metadata.attributes),
        },
    }


def build_manifest_payload_dict(
    payload: BuildManifestPayload,
) -> dict[str, object]:
    """将 ``BuildManifestPayload`` 转为规范可编码字典。

    参数：
        payload: 可复现构建清单 payload；不得含 ``manifest_id``。

    返回：
        仅含可复现字段的 ``dict[str, object]``；artifacts 按 ``logical_path``
        UTF-8 字节序排序。

    异常：
        无；调用方保证 payload 已通过领域校验。

    约束与副作用：
        只在无序集合边界规范化；不递归排序全部 list/tuple；不计算 ID；无 I/O。
    """
    sorted_artifacts = sorted(
        payload.artifacts,
        key=lambda item: item.logical_path.encode("utf-8"),
    )
    return {
        "schema_version": payload.schema_version,
        "request_digest": payload.request_digest,
        "revision": payload.revision,
        "toolchain_digest": payload.toolchain_digest,
        "baseline_id": payload.baseline_id,
        "artifacts": [_artifact_dict(item) for item in sorted_artifacts],
        "task_identities": list(payload.task_identities),
    }


def canonical_json_bytes(value: Any) -> bytes:
    """将任意 JSON 可序列化对象编码为规范 UTF-8 字节。

    参数：
        value: 可被 ``json.dumps`` 序列化的对象（通常为 payload 字典）。

    返回：
        UTF-8 编码字节；无 BOM；``sort_keys=True``；分隔符为 ``(',', ':')``。

    异常：
        对象不可 JSON 序列化时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        纯函数；确定性输出，供 SHA256 身份计算复用。
    """
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text.encode("utf-8")


class BuildManifestFactory:
    """由可复现 payload 创建不可变 ``BuildManifest`` 的工厂。

    职责：
        完整规范编码 payload 后计算 SHA256 作为 ``manifest_id``，再以私有 token
        构造 ``BuildManifest``；调用方不能传入或覆盖 ID。

    参数：
        类方法接收 ``BuildManifestPayload``；无实例状态。

    返回：
        ``create`` 返回绑定 payload 与 64 位 ID 的 ``BuildManifest``。

    异常：
        payload 非法时由领域模型抛出 ``ArtifactValidationError``；工厂本身不
        接受外部 ID 参数。

    约束与副作用：
        纯内存计算；无 I/O；ID 输入刻意排除 ``manifest_id`` 字段自身。
    """

    @staticmethod
    def create(payload: BuildManifestPayload) -> BuildManifest:
        """根据 payload 规范字节创建带内容寻址 ID 的 ``BuildManifest``。

        参数：
            payload: 仅含可复现内容的 ``BuildManifestPayload``。

        返回：
            ``manifest_id`` 等于规范 JSON UTF-8 字节 SHA256 的不可变清单。

        异常：
            无额外异常；依赖 payload / 编解码路径上的既有校验。

        约束与副作用：
            调用方不能传入 ID；不读写磁盘。
        """
        digest = hashlib.sha256(
            canonical_json_bytes(build_manifest_payload_dict(payload))
        ).hexdigest()
        return bind_build_manifest(manifest_id=digest, payload=payload)


def _require_mapping(value: object, *, field_name: str) -> dict[str, object]:
    """要求值为 JSON 对象映射。

    参数：
        value: 待检查对象。
        field_name: 用于错误消息的字段名。

    返回：
        确认为 ``dict[str, object]`` 后的映射。

    异常：
        非 ``dict`` 时抛出 ``ArtifactValidationError``。

    约束与副作用：
        纯校验；不修改入参。
    """
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{field_name} 必须是 JSON 对象")
    return cast(dict[str, object], value)


def _require_list(value: object, *, field_name: str) -> list[object]:
    """要求值为 JSON 数组。"""
    if not isinstance(value, list):
        raise ArtifactValidationError(f"{field_name} 必须是列表")
    return cast(list[object], value)


def _require_str(value: object, *, field_name: str) -> str:
    """要求值为字符串。

    参数：
        value: 待检查对象。
        field_name: 用于错误消息的字段名。

    返回：
        确认为 ``str`` 的值。

    异常：
        非 ``str`` 时抛出 ``ArtifactValidationError``。

    约束与副作用：
        纯校验。
    """
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field_name} 必须是 str")
    return value


def _require_int(value: object, *, field_name: str) -> int:
    """要求值为非布尔整数。

    参数：
        value: 待检查对象。
        field_name: 用于错误消息的字段名。

    返回：
        确认为 ``int`` 的值。

    异常：
        非 ``int``（含 bool）时抛出 ``ArtifactValidationError``。

    约束与副作用：
        纯校验；JSON ``true``/``false`` 不得当作 schema 整数。
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactValidationError(f"{field_name} 必须是 int")
    return value


def _parse_attributes(raw: object) -> tuple[tuple[str, str], ...]:
    """解析 metadata attributes 列表。

    参数：
        raw: JSON 中的 attributes 字段。

    返回：
        ``tuple[tuple[str, str], ...]``。

    异常：
        结构非法时抛出 ``ArtifactValidationError``。

    约束与副作用：
        纯解析；唯一性由 ``ArtifactMetadata`` 再校验。
    """
    attrs_list = _require_list(raw, field_name="metadata.attributes")
    pairs: list[tuple[str, str]] = []
    for item in attrs_list:
        if isinstance(item, list):
            pair_seq: list[object] | tuple[object, ...] = cast(list[object], item)
        elif isinstance(item, tuple):
            pair_seq = cast(tuple[object, ...], item)
        else:
            raise ArtifactValidationError("metadata.attributes 每一项必须是长度为 2 的列表")
        if len(pair_seq) != 2:
            raise ArtifactValidationError("metadata.attributes 每一项必须是长度为 2 的列表")
        key: object = pair_seq[0]
        value: object = pair_seq[1]
        if not isinstance(key, str) or not isinstance(value, str):
            raise ArtifactValidationError("metadata.attributes 的 key 与 value 必须均为 str")
        pairs.append((key, value))
    return tuple(pairs)


def _parse_artifact(raw: object) -> LogicalArtifact:
    """从字典解析 ``LogicalArtifact``。

    参数：
        raw: 单个 artifact JSON 对象。

    返回：
        已通过领域校验的 ``LogicalArtifact``。

    异常：
        结构或字段非法时抛出 ``ArtifactValidationError``。

    约束与副作用：
        纯解析；依赖保留列表顺序，分包 ID 交给领域模型规范为 frozenset。
    """
    data = _require_mapping(raw, field_name="artifact")
    try:
        kind = ArtifactKind(data["kind"])
    except (KeyError, ValueError) as exc:
        raise ArtifactValidationError(f"artifact.kind 非法: {data.get('kind')!r}") from exc

    blob_raw = _require_mapping(data.get("blob"), field_name="artifact.blob")
    metadata_raw = _require_mapping(data.get("metadata"), field_name="artifact.metadata")

    dependencies_raw = _require_list(data.get("dependencies"), field_name="artifact.dependencies")
    dependencies = tuple(
        _require_str(item, field_name="artifact.dependencies[]") for item in dependencies_raw
    )

    subpackage_raw = _require_list(data.get("subpackage_ids"), field_name="artifact.subpackage_ids")
    subpackage_ids = frozenset(
        _require_int(item, field_name="artifact.subpackage_ids[]") for item in subpackage_raw
    )

    try:
        return LogicalArtifact(
            logical_path=_require_str(data.get("logical_path"), field_name="artifact.logical_path"),
            kind=kind,
            blob=BlobRef(
                locator=_require_str(blob_raw.get("locator"), field_name="blob.locator"),
                sha256=_require_str(blob_raw.get("sha256"), field_name="blob.sha256"),
                size=_require_int(blob_raw.get("size"), field_name="blob.size"),
            ),
            dependencies=dependencies,
            subpackage_ids=subpackage_ids,
            metadata=ArtifactMetadata(
                source_task=_require_str(
                    metadata_raw.get("source_task"),
                    field_name="metadata.source_task",
                ),
                source_revision=_require_str(
                    metadata_raw.get("source_revision"),
                    field_name="metadata.source_revision",
                ),
                toolchain_digest=_require_str(
                    metadata_raw.get("toolchain_digest"),
                    field_name="metadata.toolchain_digest",
                ),
                attributes=_parse_attributes(metadata_raw.get("attributes")),
            ),
        )
    except ArtifactValidationError:
        raise
    except (TypeError, KeyError, ValueError) as exc:
        raise ArtifactValidationError(f"解析 LogicalArtifact 失败: {exc}") from exc


def _parse_payload(raw: object) -> BuildManifestPayload:
    """从字典解析 ``BuildManifestPayload``。

    参数：
        raw: JSON 中的 payload 对象。

    返回：
        已校验的 ``BuildManifestPayload``。

    异常：
        schema/结构非法时抛出 ``ArtifactValidationError``。

    约束与副作用：
        纯解析；不信任文件中的 ``manifest_id``。
    """
    data = _require_mapping(raw, field_name="payload")
    artifacts_raw = _require_list(data.get("artifacts"), field_name="payload.artifacts")
    identities_raw = _require_list(
        data.get("task_identities"), field_name="payload.task_identities"
    )

    baseline_raw = data.get("baseline_id")
    if baseline_raw is not None and not isinstance(baseline_raw, str):
        raise ArtifactValidationError("payload.baseline_id 必须是 str 或 null")

    try:
        return BuildManifestPayload(
            schema_version=_require_int(
                data.get("schema_version"), field_name="payload.schema_version"
            ),
            request_digest=_require_str(
                data.get("request_digest"), field_name="payload.request_digest"
            ),
            revision=_require_str(data.get("revision"), field_name="payload.revision"),
            toolchain_digest=_require_str(
                data.get("toolchain_digest"),
                field_name="payload.toolchain_digest",
            ),
            baseline_id=baseline_raw,
            artifacts=tuple(_parse_artifact(item) for item in artifacts_raw),
            task_identities=tuple(
                _require_str(item, field_name="payload.task_identities[]")
                for item in identities_raw
            ),
        )
    except ArtifactValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise ArtifactValidationError(f"解析 BuildManifestPayload 失败: {exc}") from exc


def write_build_manifest(manifest: BuildManifest, path: Path) -> None:
    """将 ``BuildManifest`` 以规范 JSON 原子写入本地路径。

    参数：
        manifest: 工厂创建的不可变清单。
        path: 目标文件路径；父目录须已存在或可创建临时文件。

    返回：
        ``None``；成功时 ``path`` 指向完整文件。

    异常：
        磁盘写入失败时抛出底层 ``OSError``。

    约束与副作用：
        先写入同目录临时文件，再 ``Path.replace()`` 替换目标，避免半写文件。
        仅本地文件系统，不访问网络或外部构建系统。
    """
    document = {
        "manifest_id": manifest.manifest_id,
        "payload": build_manifest_payload_dict(manifest.payload),
    }
    body = canonical_json_bytes(document)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 同目录临时文件 + replace，保证读者不会看到半写 JSON。
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_bytes(body)
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def read_build_manifest(path: Path) -> BuildManifest:
    """从本地 JSON 读取并严格校验 ``BuildManifest``。

    参数：
        path: 清单文件路径。

    返回：
        经工厂重算 ID 且与文件 ID 严格相等后的 ``BuildManifest``。

    异常：
        JSON/结构/schema 非法，或文件中 ID 为空、与重算值不等时，抛出
        ``ArtifactValidationError``；不返回半合法对象。文件无法读取时抛出
        底层 ``OSError``。

    约束与副作用：
        先解析 payload，再用 ``BuildManifestFactory.create`` 重算 ID；禁止用
        文件中的 ID 直接构造 ``BuildManifest``。只读本地文件。
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(f"BuildManifest JSON 无法解析: {path}") from exc

    root = _require_mapping(document, field_name="BuildManifest 根对象")
    if "payload" not in root:
        raise ArtifactValidationError("BuildManifest 缺少 payload 字段")

    file_id = root.get("manifest_id")
    if not isinstance(file_id, str) or file_id == "":
        raise ArtifactValidationError("BuildManifest.manifest_id 不得为空且必须是 str")

    payload = _parse_payload(root["payload"])
    # 完整性：始终由工厂重算，拒绝信任磁盘上的陈旧/伪造 ID。
    recomputed = BuildManifestFactory.create(payload)
    if file_id != recomputed.manifest_id:
        raise ArtifactValidationError("BuildManifest.manifest_id 与 payload 规范字节 SHA256 不一致")
    return recomputed
