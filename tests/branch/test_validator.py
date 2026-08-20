"""验证分支构建的只读仓库快照和前置条件。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from branch.model import BranchSource, BranchTarget
from branch.validator import (
    BranchPreconditionError,
    BranchPreconditionValidator,
    ExternalPropertySummary,
    RepositoryNodeSnapshot,
    RepositoryNodeType,
    RepositorySnapshot,
    ValidatedBranchSnapshot,
)


SOURCE_URL = "https://svn.example.test/repo/trunk"
TARGET_URL = "https://svn.example.test/repo/branches/feature"
REPOSITORY_UUID = "repo-uuid"


def _source() -> BranchSource:
    """构造固定 revision 的测试源引用。"""
    return BranchSource(SOURCE_URL, REPOSITORY_UUID, 17)


def _target() -> BranchTarget:
    """构造待创建且与源同仓库的测试目标引用。"""
    return BranchTarget(TARGET_URL, REPOSITORY_UUID)


def _snapshot(
    url: str,
    revision: int,
    *,
    exists: bool = True,
    node_type: RepositoryNodeType = RepositoryNodeType.DIRECTORY,
    externals: tuple[ExternalPropertySummary, ...] = (),
    repository_uuid: str = REPOSITORY_UUID,
) -> RepositorySnapshot:
    """构造一个假 SourceProvider 返回的仓库节点快照。"""
    return RepositorySnapshot(
        repository_uuid=repository_uuid,
        revision=revision,
        node=RepositoryNodeSnapshot(
            url=url,
            exists=exists,
            node_type=node_type,
            externals=externals,
        ),
    )


@dataclass
class _ReadOnlyProvider:
    """记录 inspect 调用并明确没有任何写操作入口的假提供器。"""

    source_snapshot: RepositorySnapshot
    target_snapshot: RepositorySnapshot

    def __post_init__(self) -> None:
        """初始化只读调用记录。"""
        self.inspect_calls: list[tuple[str, int | None]] = []

    def inspect(self, url: str, revision: int | None) -> RepositorySnapshot:
        """按 URL 和固定 revision 返回预先准备的只读快照。"""
        self.inspect_calls.append((url, revision))
        return self.source_snapshot if url == SOURCE_URL else self.target_snapshot


def test_validator_returns_typed_snapshot_and_only_calls_read_inspect() -> None:
    """验证 UUID、revision、节点类型和 externals 摘要均被保留，且不产生写调用。"""
    external = ExternalPropertySummary(
        path="project",
        value="https://svn.example.test/repo/trunk/resource resource\n",
    )
    provider = _ReadOnlyProvider(
        _snapshot(SOURCE_URL, 17, externals=(external,)),
        _snapshot(TARGET_URL, 23, exists=False, node_type=RepositoryNodeType.MISSING),
    )

    result = BranchPreconditionValidator(provider).validate(_source(), _target())

    assert isinstance(result, ValidatedBranchSnapshot)
    assert result.source.revision == 17
    assert result.expected_repository_revision == 23
    assert result.source.node.externals[0].sha256 == external.sha256
    assert provider.inspect_calls == [(SOURCE_URL, 17), (TARGET_URL, None)]


@pytest.mark.parametrize(
    "source_snapshot, target_snapshot, expected_message",
    [
        (
            _snapshot(SOURCE_URL, 16),
            _snapshot(TARGET_URL, 23, exists=False, node_type=RepositoryNodeType.MISSING),
            "revision",
        ),
        (
            _snapshot(
                SOURCE_URL,
                17,
                repository_uuid="other-repo",
            ),
            _snapshot(TARGET_URL, 23, exists=False, node_type=RepositoryNodeType.MISSING),
            "UUID",
        ),
        (
            _snapshot(SOURCE_URL, 17, node_type=RepositoryNodeType.FILE),
            _snapshot(TARGET_URL, 23, exists=False, node_type=RepositoryNodeType.MISSING),
            "目录",
        ),
        (
            _snapshot(SOURCE_URL, 17),
            _snapshot(TARGET_URL, 23, exists=True),
            "已存在",
        ),
    ],
)
def test_validator_rejects_snapshot_conflicts(
    source_snapshot: RepositorySnapshot,
    target_snapshot: RepositorySnapshot,
    expected_message: str,
) -> None:
    """验证固定 revision、同仓库、源目录和目标不存在均是硬前置条件。"""
    provider = _ReadOnlyProvider(source_snapshot, target_snapshot)

    with pytest.raises(BranchPreconditionError, match=expected_message):
        BranchPreconditionValidator(provider).validate(_source(), _target())


def test_validator_rejects_cross_repository_references_before_planning() -> None:
    """验证源目标声明的 repository UUID 不一致时不能生成任何已验证快照。"""
    target = BranchTarget(TARGET_URL, "other-repo")
    provider = _ReadOnlyProvider(
        _snapshot(SOURCE_URL, 17),
        _snapshot(TARGET_URL, 23, exists=False, node_type=RepositoryNodeType.MISSING),
    )

    with pytest.raises(BranchPreconditionError, match="同一仓库"):
        BranchPreconditionValidator(provider).validate(_source(), target)


def test_external_summary_rejects_tampered_digest() -> None:
    """验证摘要不是调用方可随意填写的旁路字段，篡改后构造即失败。"""
    with pytest.raises(BranchPreconditionError, match="摘要"):
        ExternalPropertySummary(
            path="project",
            value="^/repo/trunk/resource resource\n",
            sha256="0" * 64,
        )
