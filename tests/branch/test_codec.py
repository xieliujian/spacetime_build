"""验证 BranchPlan 规范 JSON、内容寻址 ID 和严格本地读写。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from branch.codec import BranchPlanCodec, BranchPlanCodecError
from branch.config import BranchConfig, MappingRule
from branch.model import BranchSource, BranchTarget, PropertyChange
from branch.planner import BranchPlanner, compute_plan_id
from branch.validator import (
    BranchPreconditionValidator,
    RepositoryNodeSnapshot,
    RepositoryNodeType,
    RepositorySnapshot,
)


SOURCE_URL = "https://svn.example.test/repo/trunk"
TARGET_URL = "https://svn.example.test/repo/branches/feature"
REPOSITORY_UUID = "repo-uuid"


def _plan():
    """构造一个供 codec 重复使用的确定性计划。"""
    source = BranchSource(SOURCE_URL, REPOSITORY_UUID, 17)
    target = BranchTarget(TARGET_URL, REPOSITORY_UUID)

    @dataclass
    class _Provider:
        """返回固定测试快照的只读假提供器。"""

        def inspect(self, url: str, revision: int | None) -> RepositorySnapshot:
            """按查询 URL 返回节点快照。"""
            del revision
            if url == SOURCE_URL:
                return RepositorySnapshot(
                    REPOSITORY_UUID,
                    17,
                    RepositoryNodeSnapshot(
                        SOURCE_URL,
                        True,
                        RepositoryNodeType.DIRECTORY,
                    ),
                )
            return RepositorySnapshot(
                REPOSITORY_UUID,
                23,
                RepositoryNodeSnapshot(TARGET_URL, False, RepositoryNodeType.MISSING),
            )

    snapshot = BranchPreconditionValidator(_Provider()).validate(source, target)
    mapping = BranchConfig(
        schema_version=1,
        mappings=(MappingRule("project", f"{SOURCE_URL}/project", f"{TARGET_URL}/project"),),
    )
    return BranchPlanner().create_plan(snapshot, source, target, mapping)


def test_codec_emits_canonical_json_and_round_trips_without_object_store() -> None:
    """验证 JSON 字段顺序、紧凑 UTF-8 编码和本地 bytes/file 往返。"""
    plan = _plan()
    encoded = BranchPlanCodec.to_bytes(plan)
    decoded = BranchPlanCodec.from_bytes(encoded)

    assert encoded == BranchPlanCodec.to_bytes(plan)
    assert encoded == json.dumps(
        json.loads(encoded.decode("utf-8")),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert decoded == plan


def test_codec_recalculates_plan_id_and_rejects_stale_or_tampered_payload() -> None:
    """验证读取时不信任文件中的 ID，任何字段篡改都被发现。"""
    plan = _plan()
    payload = BranchPlanCodec.to_payload(plan)

    stale = dict(payload)
    stale["plan_id"] = "0" * 64
    with pytest.raises(BranchPlanCodecError, match="plan_id"):
        BranchPlanCodec.from_payload(stale)

    tampered = dict(payload)
    tampered["target_root"] = TARGET_URL + "/tampered"
    with pytest.raises(BranchPlanCodecError, match="篡改|plan_id"):
        BranchPlanCodec.from_payload(tampered)


def test_codec_rejects_raw_svn_externals_old_value() -> None:
    """验证计划持久化不接受可能含凭据的 externals 原始旧值。"""
    payload = BranchPlanCodec.to_payload(_plan())
    payload["property_changes"] = [
        {
            "path": "project",
            "property_name": "svn:externals",
            "old_value": "https://user:password@example.test/repo/lib lib\n",
            "new_value": "https://example.test/repo/lib lib\n",
        }
    ]

    with pytest.raises(BranchPlanCodecError, match="old_value"):
        BranchPlanCodec.from_payload(payload)


def test_codec_to_payload_rejects_raw_svn_externals_old_value() -> None:
    """验证 codec 写出侧同样不会持久化手工构造的 externals 原文旧值。"""
    raw_change = PropertyChange(
        path="project",
        property_name="svn:externals",
        old_value="https://user:password@example.test/repo/lib lib\n",
        new_value="https://example.test/repo/lib lib\n",
    )
    pending = replace(_plan(), property_changes=(raw_change,))
    plan = replace(pending, plan_id=compute_plan_id(pending))

    with pytest.raises(BranchPlanCodecError, match="old_value"):
        BranchPlanCodec.to_payload(plan)


def test_codec_rejects_unknown_schema_and_unknown_fields(tmp_path: Path) -> None:
    """验证未来 schema 和隐藏字段不会被宽松解析。"""
    plan = _plan()
    unknown_schema = dict(BranchPlanCodec.to_payload(plan))
    unknown_schema["schema_version"] = 99
    with pytest.raises(BranchPlanCodecError, match="schema"):
        BranchPlanCodec.from_payload(unknown_schema)

    unknown_field = dict(BranchPlanCodec.to_payload(plan))
    unknown_field["unexpected"] = True
    with pytest.raises(BranchPlanCodecError, match="未知"):
        BranchPlanCodec.from_payload(unknown_field)

    path = tmp_path / "branch-plan.json"
    BranchPlanCodec.write(plan, path)
    assert BranchPlanCodec.read(path) == plan
