"""验证 BranchPlan 的确定性生成、externals 变更和冲突拒绝。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from branch.config import BranchConfig, MappingRule
from branch.model import BranchSource, BranchTarget
from branch.planner import BranchPlan, BranchPlanError, BranchPlanner
from branch.validator import (
    BranchPreconditionValidator,
    ExternalPropertySummary,
    RepositoryNodeSnapshot,
    RepositoryNodeType,
    RepositorySnapshot,
)


SOURCE_URL = "https://svn.example.test/repo/trunk"
TARGET_URL = "https://svn.example.test/repo/branches/feature"
REPOSITORY_UUID = "repo-uuid"


def _source() -> BranchSource:
    """构造测试源引用。"""
    return BranchSource(SOURCE_URL, REPOSITORY_UUID, 17)


def _target() -> BranchTarget:
    """构造测试目标引用。"""
    return BranchTarget(TARGET_URL, REPOSITORY_UUID)


@dataclass
class _Provider:
    """为 planner 测试返回固定的只读源和目标快照。"""

    source_snapshot: RepositorySnapshot
    target_snapshot: RepositorySnapshot

    def inspect(self, url: str, revision: int | None) -> RepositorySnapshot:
        """返回预置快照，不执行任何文件或 SVN 写操作。"""
        del revision
        return self.source_snapshot if url == SOURCE_URL else self.target_snapshot


def _mapping() -> BranchConfig:
    """构造 project/resource 两条完整 URL 映射。"""
    return BranchConfig(
        schema_version=1,
        mappings=(
            MappingRule(
                "project",
                f"{SOURCE_URL}/project",
                f"{TARGET_URL}/project",
            ),
            MappingRule(
                "resource",
                f"{SOURCE_URL}/resource",
                f"{TARGET_URL}/resource",
            ),
        ),
        allowlist=(TARGET_URL,),
        unmatched_policy="preserve",
    )


def _validated_snapshot(revision: int = 17):
    """通过 validator 生成 planner 只接受的已验证快照。"""
    source_snapshot = RepositorySnapshot(
        repository_uuid=REPOSITORY_UUID,
        revision=revision,
        node=RepositoryNodeSnapshot(
            url=SOURCE_URL,
            exists=True,
            node_type=RepositoryNodeType.DIRECTORY,
            externals=(),
        ),
    )
    target_snapshot = RepositorySnapshot(
        repository_uuid=REPOSITORY_UUID,
        revision=23,
        node=RepositoryNodeSnapshot(
            url=TARGET_URL,
            exists=False,
            node_type=RepositoryNodeType.MISSING,
        ),
    )
    return BranchPreconditionValidator(_Provider(source_snapshot, target_snapshot)).validate(
        _source(), _target()
    )


def test_planner_generates_immutable_plan_with_sorted_copies_and_id() -> None:
    """验证 project/resource copy 集和 expected revision 进入单个不可变计划。"""
    plan = BranchPlanner().create_plan(_validated_snapshot(), _source(), _target(), _mapping())

    assert isinstance(plan, BranchPlan)
    assert len(plan.plan_id) == 64
    assert plan.source_revision == 17
    assert plan.expected_repository_revision == 23
    assert tuple(copy.source_path for copy in plan.copies) == ("project", "resource")
    assert plan.repository_uuid == REPOSITORY_UUID
    with pytest.raises(AttributeError):
        plan.target_root = "https://evil.example.test"  # type: ignore[misc]


def test_planner_rewrites_externals_and_preserves_no_change_as_empty() -> None:
    """验证外部属性变化落到 property_changes，无变化时不制造空操作。"""
    external_snapshot = _validated_snapshot_with_external(
        "https://svn.example.test/repo/trunk/resource resource\n"
    )
    plan = BranchPlanner().create_plan(external_snapshot, _source(), _target(), _mapping())

    assert len(plan.property_changes) == 1
    change = plan.property_changes[0]
    assert change.path == "project"
    assert change.property_name == "svn:externals"
    assert change.old_value == (
        "sha256:"
        + hashlib.sha256(b"https://svn.example.test/repo/trunk/resource resource\n").hexdigest()
    )
    assert change.new_value == "https://svn.example.test/repo/branches/feature/resource resource\n"

    no_change = BranchPlanner().create_plan(
        _validated_snapshot_with_external(
            "https://svn.example.test/repo/branches/feature/resource resource\n"
        ),
        _source(),
        _target(),
        _mapping(),
    )
    assert no_change.property_changes == ()


def _validated_snapshot_with_external(value: str):
    """构造含 project 节点 externals 属性的已验证快照。"""
    source_snapshot = RepositorySnapshot(
        repository_uuid=REPOSITORY_UUID,
        revision=17,
        node=RepositoryNodeSnapshot(
            url=SOURCE_URL,
            exists=True,
            node_type=RepositoryNodeType.DIRECTORY,
            externals=(),
        ),
        externals=(ExternalPropertySummary("project", value),),
    )
    target_snapshot = RepositorySnapshot(
        repository_uuid=REPOSITORY_UUID,
        revision=23,
        node=RepositoryNodeSnapshot(
            url=TARGET_URL,
            exists=False,
            node_type=RepositoryNodeType.MISSING,
        ),
    )
    return BranchPreconditionValidator(_Provider(source_snapshot, target_snapshot)).validate(
        _source(), _target()
    )


def test_planner_is_independent_of_mapping_input_order() -> None:
    """验证映射排列变化不会改变 copies、property changes 或 plan ID。"""
    mapping = _mapping()
    reversed_mapping = BranchConfig(
        schema_version=1,
        mappings=tuple(reversed(mapping.mappings)),
        allowlist=tuple(reversed(mapping.allowlist)),
        unmatched_policy=mapping.unmatched_policy,
    )

    first = BranchPlanner().create_plan(_validated_snapshot(), _source(), _target(), mapping)
    second = BranchPlanner().create_plan(
        _validated_snapshot(), _source(), _target(), reversed_mapping
    )

    assert first == second


def test_planner_rejects_revision_drift_and_duplicate_target() -> None:
    """验证 snapshot revision 漂移和两个 mapping 指向同一目标时必须重规划。"""
    with pytest.raises(BranchPlanError, match="revision"):
        drifted_source = BranchSource(SOURCE_URL, REPOSITORY_UUID, 18)
        BranchPlanner().create_plan(_validated_snapshot(), drifted_source, _target(), _mapping())

    conflict = BranchConfig(
        schema_version=1,
        mappings=(
            MappingRule("a", f"{SOURCE_URL}/a", f"{TARGET_URL}/same"),
            MappingRule("b", f"{SOURCE_URL}/b", f"{TARGET_URL}/same"),
        ),
    )
    with pytest.raises(BranchPlanError, match="目标"):
        BranchPlanner().create_plan(_validated_snapshot(), _source(), _target(), conflict)
