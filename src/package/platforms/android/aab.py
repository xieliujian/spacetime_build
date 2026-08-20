"""Android App Bundle Gradle 构建计划和输出发现。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from package.platforms.android.model import AndroidOutputKind, AndroidPackageOptions


@dataclass(frozen=True, slots=True)
class AndroidAabBuildPlan:
    """AAB 构建的工作区、任务和输出目录。"""

    workspace: Path
    output_directory: Path
    options: AndroidPackageOptions
    gradle_task: str


class AndroidAppBundleBuilder:
    """生成 AAB bundle Gradle 任务并严格发现单一输出。"""

    @staticmethod
    def plan(
        workspace: Path, output_directory: Path, options: AndroidPackageOptions
    ) -> AndroidAabBuildPlan:
        """生成 ``bundleDebug/Release`` 任务计划，不执行 Gradle。"""
        if not isinstance(workspace, Path) or not workspace.is_absolute():
            raise ValueError("workspace 必须是绝对 Path")
        if not isinstance(output_directory, Path) or not output_directory.is_absolute():
            raise ValueError("output_directory 必须是绝对 Path")
        if not isinstance(options, AndroidPackageOptions):
            raise TypeError("options 必须是 AndroidPackageOptions")
        if options.output_kind is not AndroidOutputKind.AAB:
            raise ValueError("AAB builder 只接受 AAB output_kind")
        task = ":launcher:bundle" + options.build_type.value.capitalize()
        return AndroidAabBuildPlan(workspace, output_directory, options, task)

    @staticmethod
    def discover_output(plan: AndroidAabBuildPlan) -> Path:
        """在计划输出目录内发现唯一 AAB 文件。"""
        if not isinstance(plan, AndroidAabBuildPlan):
            raise TypeError("plan 必须是 AndroidAabBuildPlan")
        if not plan.output_directory.is_dir():
            raise ValueError("AAB 输出目录不存在")
        outputs = tuple(
            sorted(plan.output_directory.glob("*.aab"), key=lambda path: path.name.encode("utf-8"))
        )
        if len(outputs) != 1 or not outputs[0].is_file():
            raise ValueError("AAB 输出必须恰有一个普通文件")
        return outputs[0]
