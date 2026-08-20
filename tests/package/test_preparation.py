"""包体隔离工程准备计划测试。"""

from pathlib import Path

import pytest

from package.preparation import PackageWorkspacePreparer
from ports.workspace import WorkspaceLease


def test_workspace_preparer_copies_only_into_lease_workspace(tmp_path: Path) -> None:
    """验证准备计划和执行不会修改源快照。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "project.txt").write_text("source", encoding="utf-8")
    lease = WorkspaceLease("pkg-1", tmp_path / "workspace", "lease-1")
    lease.path.mkdir()
    plan = PackageWorkspacePreparer.plan(source, lease, streaming_assets=source / "project.txt")
    result = PackageWorkspacePreparer.execute(plan)
    assert result.project_path == lease.path / "project"
    assert (result.project_path / "project.txt").read_text(encoding="utf-8") == "source"
    assert (source / "project.txt").read_text(encoding="utf-8") == "source"


def test_workspace_preparer_rejects_source_inside_workspace(tmp_path: Path) -> None:
    """验证源目录与输出租约重叠时失败。"""
    lease = WorkspaceLease("pkg-1", tmp_path / "workspace", "lease-1")
    lease.path.mkdir()
    with pytest.raises(ValueError):
        PackageWorkspacePreparer.plan(lease.path, lease)
