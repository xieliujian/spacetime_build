"""Android APK Gradle 构建计划和输出发现。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from package.platforms.android.model import AndroidOutputKind, AndroidPackageOptions


@dataclass(frozen=True, slots=True)
class AndroidApkBuildPlan:
    """未签名 APK 构建的工作区、任务和输出目录。"""

    workspace: Path
    output_directory: Path
    options: AndroidPackageOptions
    gradle_task: str


class AndroidApkBuilder:
    """生成 APK Gradle 任务并严格发现单一未签名输出。"""

    @staticmethod
    def plan(
        workspace: Path, output_directory: Path, options: AndroidPackageOptions
    ) -> AndroidApkBuildPlan:
        """生成 ``assembleDebug/Release`` 任务计划，不执行 Gradle。"""
        if not isinstance(workspace, Path) or not workspace.is_absolute():
            raise ValueError("workspace 必须是绝对 Path")
        if not isinstance(output_directory, Path) or not output_directory.is_absolute():
            raise ValueError("output_directory 必须是绝对 Path")
        if not isinstance(options, AndroidPackageOptions):
            raise TypeError("options 必须是 AndroidPackageOptions")
        if options.output_kind is not AndroidOutputKind.APK:
            raise ValueError("APK builder 只接受 APK output_kind")
        task = ":launcher:assemble" + options.build_type.value.capitalize()
        return AndroidApkBuildPlan(workspace, output_directory, options, task)

    @staticmethod
    def discover_output(plan: AndroidApkBuildPlan) -> Path:
        """在计划输出目录内发现唯一 APK 文件。"""
        if not isinstance(plan, AndroidApkBuildPlan):
            raise TypeError("plan 必须是 AndroidApkBuildPlan")
        if not plan.output_directory.is_dir():
            raise ValueError("APK 输出目录不存在")
        outputs = tuple(
            sorted(plan.output_directory.glob("*.apk"), key=lambda path: path.name.encode("utf-8"))
        )
        if len(outputs) != 1 or not outputs[0].is_file():
            raise ValueError("APK 输出必须恰有一个普通文件")
        return outputs[0]
