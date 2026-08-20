"""Android APK Gradle 构建计划和输出发现。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.errors import ToolExecutionError
from package.platforms.android.model import AndroidOutputKind, AndroidPackageOptions
from ports.process import CancellationToken, ProcessOutcome, ProcessRequest, ProcessRunner


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

    @staticmethod
    def build(
        plan: AndroidApkBuildPlan,
        process_runner: ProcessRunner,
        gradle_executable: Path,
        *,
        cancellation: CancellationToken | None = None,
    ) -> Path:
        """通过 ProcessRunner 执行固定 Gradle task 并发现唯一 APK。

        参数：
            plan: ``plan`` 生成的 APK 构建计划。
            process_runner: 受控外部进程端口。
            gradle_executable: 绝对 Gradle 可执行文件路径。
            cancellation: 可选协作取消令牌。

        返回：
            已由 Gradle 生成且在输出目录中唯一存在的 APK 路径。

        异常：
            参数非法、Gradle 非零/超时/取消或输出数量不为一时抛出异常；不会返回
            未验证的包体路径。

        约束与副作用：
            参数仅包含固定 task 名；进程和输出目录副作用由注入端口/调用方负责。
        """
        if not isinstance(plan, AndroidApkBuildPlan):
            raise TypeError("plan 必须是 AndroidApkBuildPlan")
        if not isinstance(gradle_executable, Path) or not gradle_executable.is_absolute():
            raise ValueError("gradle_executable 必须是绝对 Path")
        if cancellation is not None and cancellation.is_cancelled:
            raise ToolExecutionError("Gradle APK 构建已取消")
        stdout_path = plan.output_directory.parent / "apk-gradle.stdout.log"
        stderr_path = plan.output_directory.parent / "apk-gradle.stderr.log"
        result = process_runner.run(
            ProcessRequest(
                gradle_executable,
                (plan.gradle_task,),
                plan.workspace,
                stdout_path,
                stderr_path,
            ),
            cancellation,
        )
        if result.outcome is not ProcessOutcome.COMPLETED or result.exit_code != 0:
            raise ToolExecutionError(
                f"Gradle APK 构建失败: outcome={result.outcome.value}, exit_code={result.exit_code}, "
                f"diagnostic={result.diagnostic_message}"
            )
        return AndroidApkBuilder.discover_output(plan)
