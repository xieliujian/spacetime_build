"""验证 branch 领域模型的不可变性、引用安全和状态转移约束。"""

from __future__ import annotations

import pytest
from typing import cast

from branch.model import (
    BranchCopy,
    BranchResult,
    BranchSource,
    BranchStatus,
    BranchTarget,
    BranchValidationError,
    PropertyChange,
)


def _source() -> BranchSource:
    """构造一个供模型测试重复使用的固定源码引用。"""
    return BranchSource(
        url="https://svn.example.test/repo/trunk",
        repository_uuid="repo-uuid",
        revision=17,
    )


def _target() -> BranchTarget:
    """构造一个与固定源码引用不同的目标引用。"""
    return BranchTarget(
        url="https://svn.example.test/repo/branches/feature",
        repository_uuid="repo-uuid",
    )


def test_branch_models_are_frozen_and_normalize_sequences() -> None:
    """验证五个公开模型都是不可变对象，并规范化可变序列边界。"""
    source = _source()
    target = _target()
    copy = BranchCopy(
        source=source,
        target=target,
        source_path="project",
        target_path="project",
    )
    change = PropertyChange(
        path="project",
        property_name="svn:externals",
        old_value="old",
        new_value="new",
    )
    result = BranchResult(
        mutation_id="mutation-1",
        status=BranchStatus.PLANNED,
        copies=cast(tuple[BranchCopy, ...], [copy]),
        property_changes=cast(tuple[PropertyChange, ...], [change]),
    )

    assert result.status is BranchStatus.PLANNED
    assert result.copies == (copy,)
    assert result.property_changes == (change,)
    with pytest.raises(AttributeError):
        setattr(result, "status", BranchStatus.APPLIED)


@pytest.mark.parametrize("revision", ["HEAD", "head", 0, -1, True, "17"])
def test_branch_source_rejects_unfixed_or_invalid_revision(revision: object) -> None:
    """验证源码引用只接受严格的正整数固定 revision。"""
    with pytest.raises(BranchValidationError, match="revision"):
        BranchSource(
            url="https://svn.example.test/repo/trunk",
            repository_uuid="repo-uuid",
            revision=cast(int, revision),
        )


@pytest.mark.parametrize(
    "field_values",
    [
        {"url": "", "repository_uuid": "repo-uuid"},
        {"url": "https://svn.example.test/repo/trunk", "repository_uuid": ""},
        {"url": "https://svn.example.test/repo/trunk", "repository_uuid": "repo\n"},
    ],
)
def test_branch_references_reject_empty_or_control_values(
    field_values: dict[str, str],
) -> None:
    """验证源和目标引用拒绝空字符串及控制字符。"""
    with pytest.raises(BranchValidationError):
        BranchSource(
            url=field_values["url"],
            repository_uuid=field_values["repository_uuid"],
            revision=1,
        )


def test_branch_copy_rejects_same_source_and_target_and_path_escape() -> None:
    """验证复制操作拒绝源目标相同以及 source/target 路径逃逸。"""
    source = _source()
    with pytest.raises(BranchValidationError, match="不同"):
        BranchCopy(
            source=source,
            target=BranchTarget(
                url=source.url,
                repository_uuid=source.repository_uuid,
            ),
            source_path="project",
            target_path="project",
        )

    for escaped_path in ("../project", "project/../../x", "/absolute", "project//x"):
        with pytest.raises(BranchValidationError, match="路径"):
            BranchCopy(
                source=source,
                target=_target(),
                source_path=escaped_path,
                target_path="project",
            )


def test_branch_result_transition_is_explicit_and_secret_safe() -> None:
    """验证结果状态只能按允许路径转移，诊断消息不会进入 repr。"""
    result = BranchResult(
        mutation_id="mutation-1",
        status=BranchStatus.PLANNED,
        message="credential=top-secret-value",
    )
    applied = result.transition(BranchStatus.APPLIED)

    assert applied.status is BranchStatus.APPLIED
    assert "top-secret-value" not in repr(result)
    with pytest.raises(BranchValidationError, match="状态"):
        applied.transition(BranchStatus.PLANNED)


def test_property_change_rejects_path_escape_and_empty_property_name() -> None:
    """验证属性变更只能指向安全相对路径和非空属性名。"""
    with pytest.raises(BranchValidationError):
        PropertyChange(
            path="../project",
            property_name="svn:externals",
            old_value=None,
            new_value="value",
        )
    with pytest.raises(BranchValidationError):
        PropertyChange(
            path="project",
            property_name="",
            old_value=None,
            new_value="value",
        )
