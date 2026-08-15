"""验证 BuildManifest 规范编解码、工厂 ID 与严格读写契约。

本模块按第二阶段 Task 6 分步覆盖 payload 规范化、工厂计算不可变 ID，以及原子
读写与完整性校验。测试不访问 SVN、Unity、Jenkins 或 CDN。
"""

from __future__ import annotations

from typing import Any, cast

import hashlib
import json
from pathlib import Path

import pytest

from core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    BlobRef,
    LogicalArtifact,
)
from core.build_records import BuildManifest, BuildManifestPayload
from core.errors import ArtifactValidationError
from core.manifest_codec import (
    BuildManifestFactory,
    build_manifest_payload_dict,
    canonical_json_bytes,
    read_build_manifest,
    write_build_manifest,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _artifact(
    *,
    logical_path: str,
    sha256: str,
    dependencies: tuple[str, ...] = (),
    subpackage_ids: frozenset[int] = frozenset(),
    attributes: tuple[tuple[str, str], ...] = (),
) -> LogicalArtifact:
    """构造测试用 ``LogicalArtifact``。

    参数：
        logical_path: 客户端逻辑路径。
        sha256: 64 位小写十六进制内容哈希。
        dependencies: 有序依赖路径元组。
        subpackage_ids: 分包 ID 集合。
        attributes: metadata 属性对元组。

    返回：
        合法的不可变逻辑产物。
    """
    return LogicalArtifact(
        logical_path=logical_path,
        kind=ArtifactKind.ASSET_BUNDLE,
        blob=BlobRef(
            locator=f"sha256:{sha256}",
            sha256=sha256,
            size=100,
        ),
        dependencies=dependencies,
        subpackage_ids=subpackage_ids,
        metadata=ArtifactMetadata(
            source_task="task.build",
            source_revision="r1",
            toolchain_digest="toolchain-v1",
            attributes=attributes,
        ),
    )


def test_payload_codec_normalizes_only_unordered_collections() -> None:
    """验证仅无序集合边界被规范化，有序依赖保留顺序与重复。

    测试无参数和返回值。断言：

    - ``build_manifest_payload_dict`` / ``canonical_json_bytes`` 对交换
      artifacts、metadata attributes、subpackage_ids 输入顺序后产生相同字节；
    - 依赖 ``("b", "a", "b")`` 在 JSON 中保持同序且保留重复；
    - 规范 JSON 为 UTF-8、无 BOM、``sort_keys=True``、紧凑分隔符。

    当 ``core.manifest_codec`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除临时断言外不产生外部副作用。
    """
    art_z = _artifact(
        logical_path="z/last.assetbundle",
        sha256=_SHA_A,
        dependencies=("b", "a", "b"),
        subpackage_ids=frozenset({3, 1, 2}),
        attributes=(("z_key", "z"), ("a_key", "a")),
    )
    art_a = _artifact(
        logical_path="a/first.assetbundle",
        sha256=_SHA_B,
        dependencies=(),
        subpackage_ids=cast(frozenset[int], {2, 1}),
        attributes=(("b_key", "b"), ("a_key", "a")),
    )

    payload_order1 = BuildManifestPayload(
        schema_version=1,
        request_digest="req-1",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id="baseline-1",
        artifacts=(art_z, art_a),
        task_identities=("task.a", "task.b"),
    )
    payload_order2 = BuildManifestPayload(
        schema_version=1,
        request_digest="req-1",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id="baseline-1",
        artifacts=(art_a, art_z),
        task_identities=("task.a", "task.b"),
    )

    dict1 = build_manifest_payload_dict(payload_order1)
    dict2 = build_manifest_payload_dict(payload_order2)
    bytes1 = canonical_json_bytes(dict1)
    bytes2 = canonical_json_bytes(dict2)

    assert bytes1 == bytes2
    assert isinstance(bytes1, bytes)
    assert not bytes1.startswith(b"\xef\xbb\xbf")
    assert bytes1 == bytes1.decode("utf-8").encode("utf-8")

    parsed = json.loads(bytes1.decode("utf-8"))
    artifacts = parsed["artifacts"]
    assert [item["logical_path"] for item in artifacts] == [
        "a/first.assetbundle",
        "z/last.assetbundle",
    ]
    assert artifacts[1]["dependencies"] == ["b", "a", "b"]
    assert artifacts[1]["subpackage_ids"] == [1, 2, 3]
    assert artifacts[0]["metadata"]["attributes"] == [
        ["a_key", "a"],
        ["b_key", "b"],
    ]
    assert artifacts[1]["metadata"]["attributes"] == [
        ["a_key", "a"],
        ["z_key", "z"],
    ]

    # 紧凑分隔符且顶层键已排序（sort_keys=True）。
    assert b", " not in bytes1
    assert b": " not in bytes1
    expected_compact = json.dumps(
        dict1, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert bytes1 == expected_compact


def test_manifest_factory_computes_immutable_id_from_payload_only() -> None:
    """验证工厂仅由 payload 规范字节计算不可变 64 位 ID。

    测试无参数和返回值。断言：

    - ``BuildManifestFactory.create(payload)`` 返回同时持有不可变 payload 与
      64 位小写十六进制 ``manifest_id`` 的 ``BuildManifest``；
    - ID 精确等于 payload 规范 JSON 字节的 SHA256，结构上排除 ID 自身；
    - 修改 request、revision、toolchain、baseline、schema、task identity、
      artifact hash 或有序依赖任一项都会改变 ID；
    - 直接调用 ``BuildManifest(...)``（含空 ID 或陈旧 ID）以 ``TypeError`` 失败。

    当前一步最小 GREEN 尚未定义 ``BuildManifest`` / ``BuildManifestFactory`` 时，
    导入应以 ``ImportError`` 失败。除导入外不产生外部副作用。
    """
    artifact = _artifact(
        logical_path="scene/a.assetbundle",
        sha256=_SHA_A,
        dependencies=("b", "a", "b"),
        subpackage_ids=frozenset({1, 2}),
        attributes=(("platform", "android"),),
    )
    payload = BuildManifestPayload(
        schema_version=1,
        request_digest="req-1",
        revision="r100",
        toolchain_digest="toolchain-v1",
        baseline_id="baseline-1",
        artifacts=(artifact,),
        task_identities=("task.a:r100",),
    )

    manifest = BuildManifestFactory.create(payload)
    expected_id = hashlib.sha256(
        canonical_json_bytes(build_manifest_payload_dict(payload))
    ).hexdigest()
    assert manifest.manifest_id == expected_id
    assert len(manifest.manifest_id) == 64
    assert manifest.manifest_id == manifest.manifest_id.lower()
    assert all(c in "0123456789abcdef" for c in manifest.manifest_id)
    assert manifest.payload is payload or manifest.payload == payload

    with pytest.raises((AttributeError, TypeError)):
        manifest.manifest_id = "x" * 64  # type: ignore[misc]

    with pytest.raises(TypeError):
        BuildManifest(manifest_id="", payload=payload)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        BuildManifest(manifest_id="0" * 64, payload=payload)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        BuildManifest(
            manifest_id=expected_id,
            payload=payload,
        )  # type: ignore[call-arg]

    mutations: list[BuildManifestPayload] = [
        BuildManifestPayload(
            schema_version=2,
            request_digest="req-1",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id="baseline-1",
            artifacts=(artifact,),
            task_identities=("task.a:r100",),
        ),
        BuildManifestPayload(
            schema_version=1,
            request_digest="req-CHANGED",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id="baseline-1",
            artifacts=(artifact,),
            task_identities=("task.a:r100",),
        ),
        BuildManifestPayload(
            schema_version=1,
            request_digest="req-1",
            revision="r999",
            toolchain_digest="toolchain-v1",
            baseline_id="baseline-1",
            artifacts=(artifact,),
            task_identities=("task.a:r100",),
        ),
        BuildManifestPayload(
            schema_version=1,
            request_digest="req-1",
            revision="r100",
            toolchain_digest="toolchain-CHANGED",
            baseline_id="baseline-1",
            artifacts=(artifact,),
            task_identities=("task.a:r100",),
        ),
        BuildManifestPayload(
            schema_version=1,
            request_digest="req-1",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id=None,
            artifacts=(artifact,),
            task_identities=("task.a:r100",),
        ),
        BuildManifestPayload(
            schema_version=1,
            request_digest="req-1",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id="baseline-1",
            artifacts=(artifact,),
            task_identities=("task.CHANGED:r100",),
        ),
        BuildManifestPayload(
            schema_version=1,
            request_digest="req-1",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id="baseline-1",
            artifacts=(
                _artifact(
                    logical_path="scene/a.assetbundle",
                    sha256=_SHA_B,
                    dependencies=("b", "a", "b"),
                    subpackage_ids=frozenset({1, 2}),
                    attributes=(("platform", "android"),),
                ),
            ),
            task_identities=("task.a:r100",),
        ),
        BuildManifestPayload(
            schema_version=1,
            request_digest="req-1",
            revision="r100",
            toolchain_digest="toolchain-v1",
            baseline_id="baseline-1",
            artifacts=(
                _artifact(
                    logical_path="scene/a.assetbundle",
                    sha256=_SHA_A,
                    dependencies=("a", "b", "b"),
                    subpackage_ids=frozenset({1, 2}),
                    attributes=(("platform", "android"),),
                ),
            ),
            task_identities=("task.a:r100",),
        ),
    ]
    for mutated in mutations:
        other = BuildManifestFactory.create(mutated)
        assert other.manifest_id != manifest.manifest_id


def test_write_and_read_manifest_round_trip_and_verify_id(tmp_path: Path) -> None:
    """验证原子写读 round-trip，并在读取时严格重算校验 ID。

    参数：
        tmp_path: pytest 临时目录，用于写入 manifest JSON。

    返回：
        无返回值。断言：

    - ``write_build_manifest`` 经临时文件 ``Path.replace()`` 原子落盘；
    - round-trip 后 ``manifest_id`` 与 payload 等价；
    - 读取时先解析 payload，再用工厂重算 ID，要求文件中 ID 非空且与重算值
      严格相等；
    - 空 ID、陈旧 ID、错误 schema 或错误结构均抛 ``ArtifactValidationError``，
      不返回半合法对象。

    当前一步最小 GREEN 尚未定义读写 API 时，导入应以 ``ImportError`` 失败。
    仅向临时目录写文件，不访问外部系统。
    """
    artifact = _artifact(
        logical_path="scene/a.assetbundle",
        sha256=_SHA_A,
        dependencies=("shared/base",),
        subpackage_ids=frozenset({1}),
        attributes=(("platform", "android"),),
    )
    payload = BuildManifestPayload(
        schema_version=1,
        request_digest="req-round",
        revision="r200",
        toolchain_digest="toolchain-v2",
        baseline_id=None,
        artifacts=(artifact,),
        task_identities=("task.a:r200",),
    )
    manifest = BuildManifestFactory.create(payload)
    path = tmp_path / "build_manifest.json"

    write_build_manifest(manifest, path)
    assert path.is_file()
    # 原子写：最终路径存在，同目录临时文件不应残留为半写状态。
    leftovers = list(tmp_path.glob("*.tmp*")) + list(tmp_path.glob(".*tmp*"))
    assert leftovers == []

    loaded = read_build_manifest(path)
    assert loaded.manifest_id == manifest.manifest_id
    assert loaded.payload == manifest.payload

    # 空 ID
    empty_id_doc = {
        "manifest_id": "",
        "payload": build_manifest_payload_dict(payload),
    }
    path.write_text(
        json.dumps(empty_id_doc, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactValidationError):
        read_build_manifest(path)

    # 陈旧 ID
    stale_doc = {
        "manifest_id": "0" * 64,
        "payload": build_manifest_payload_dict(payload),
    }
    path.write_text(
        json.dumps(stale_doc, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactValidationError):
        read_build_manifest(path)

    # 错误结构：缺少 payload
    path.write_text(
        json.dumps({"manifest_id": manifest.manifest_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactValidationError):
        read_build_manifest(path)

    # 错误 schema / 结构：payload 字段类型非法
    bad_schema_doc: dict[str, Any] = {
        "manifest_id": manifest.manifest_id,
        "payload": {
            "schema_version": "not-an-int",
            "request_digest": "req-round",
            "revision": "r200",
            "toolchain_digest": "toolchain-v2",
            "baseline_id": None,
            "artifacts": [],
            "task_identities": [],
        },
    }
    path.write_text(
        json.dumps(bad_schema_doc, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactValidationError):
        read_build_manifest(path)
