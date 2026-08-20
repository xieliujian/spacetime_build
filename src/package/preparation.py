"""包体隔离 Unity 工程准备计划与执行。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ports.workspace import WorkspaceLease


@dataclass(frozen=True, slots=True)
class PackageWorkspacePlan:
    """绑定源快照、隔离租约和可选 StreamingAssets 输入的准备计划。"""

    source_snapshot: Path
    workspace: Path
    streaming_assets: Path | None


@dataclass(frozen=True, slots=True)
class PackageWorkspaceResult:
    """隔离工程准备结果。"""

    project_path: Path
    streaming_assets_path: Path | None


class PackageWorkspacePreparer:
    """只在 WorkspaceLease 内复制包体输入，保持源快照只读。"""

    @staticmethod
    def plan(
        source_snapshot: Path,
        lease: WorkspaceLease,
        *,
        streaming_assets: Path | None = None,
    ) -> PackageWorkspacePlan:
        """生成隔离工程准备计划。

        参数：
            source_snapshot: 固定源码/Unity 工程目录。
            lease: 已取得的独占包体工作区租约。
            streaming_assets: 可选额外输入文件或目录。

        返回：
            ``PackageWorkspacePlan``；计划阶段不写任何目录。

        异常：
            路径类型、源目录存在性或源目录与租约重叠时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只冻结路径；源目录不会被修改，实际复制仅由 ``execute`` 写入租约目录。
        """
        if not isinstance(source_snapshot, Path) or not source_snapshot.is_absolute():
            raise ValueError("source_snapshot 必须是绝对 Path")
        if not isinstance(lease, WorkspaceLease):
            raise TypeError("lease 必须是 WorkspaceLease")
        source = source_snapshot.resolve()
        workspace = lease.path.resolve()
        if not source.is_dir():
            raise ValueError("source_snapshot 必须是目录")
        if source == workspace or workspace in source.parents or source in workspace.parents:
            raise ValueError("source_snapshot 不得与 package workspace 重叠")
        if streaming_assets is not None:
            if not isinstance(streaming_assets, Path) or not streaming_assets.is_absolute():
                raise ValueError("streaming_assets 必须是绝对 Path")
            if not streaming_assets.exists():
                raise ValueError("streaming_assets 不存在")
        return PackageWorkspacePlan(
            source, workspace, streaming_assets.resolve() if streaming_assets else None
        )

    @staticmethod
    def execute(plan: PackageWorkspacePlan) -> PackageWorkspaceResult:
        """执行计划，将源工程和额外资源复制到租约目录。

        参数：
            plan: 已验证的工作区准备计划。

        返回：
            新工程路径和 StreamingAssets 目标路径。

        异常：
            目标已有内容、复制失败或计划类型错误时抛出 ``TypeError`` / ``OSError``。

        约束与副作用：
            只写 ``plan.workspace`` 下的精确目标；不删除源目录、不调用外部工具。
        """
        if not isinstance(plan, PackageWorkspacePlan):
            raise TypeError("plan 必须是 PackageWorkspacePlan")
        plan.workspace.mkdir(parents=True, exist_ok=True)
        project_path = plan.workspace / "project"
        if project_path.exists():
            raise FileExistsError(f"package project 目标已存在: {project_path}")
        shutil.copytree(plan.source_snapshot, project_path, symlinks=False)
        streaming_target: Path | None = None
        if plan.streaming_assets is not None:
            streaming_target = project_path / "Assets" / "StreamingAssets"
            streaming_target.mkdir(parents=True, exist_ok=True)
            if plan.streaming_assets.is_dir():
                for child in plan.streaming_assets.iterdir():
                    destination = streaming_target / child.name
                    if child.is_dir():
                        shutil.copytree(child, destination, symlinks=False)
                    else:
                        shutil.copy2(child, destination)
            else:
                shutil.copy2(plan.streaming_assets, streaming_target / plan.streaming_assets.name)
        return PackageWorkspaceResult(project_path, streaming_target)
