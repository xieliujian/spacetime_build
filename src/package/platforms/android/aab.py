"""Android App Bundle Gradle 构建计划和输出发现。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.errors import ToolExecutionError
from package.platforms.android.model import AndroidOutputKind, AndroidPackageOptions
from ports.process import CancellationToken, ProcessOutcome, ProcessRequest, ProcessRunner


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

    @staticmethod
    def build(
        plan: AndroidAabBuildPlan,
        process_runner: ProcessRunner,
        gradle_executable: Path,
        *,
        cancellation: CancellationToken | None = None,
    ) -> Path:
        """通过 ProcessRunner 执行固定 Gradle task 并发现唯一 AAB。

        参数：
            plan: ``plan`` 生成的 AAB 构建计划。
            process_runner: 受控外部进程端口。
            gradle_executable: 绝对 Gradle 可执行文件路径。
            cancellation: 可选协作取消令牌。

        返回：
            已由 Gradle 生成且唯一发现的 AAB 路径。

        异常：
            参数非法、Gradle 失败、取消或输出数量不为一时抛出异常。

        约束与副作用：
            只传入固定 bundle task；进程和输出目录副作用由注入端口负责。
        """
        if not isinstance(plan, AndroidAabBuildPlan):
            raise TypeError("plan 必须是 AndroidAabBuildPlan")
        if not isinstance(gradle_executable, Path) or not gradle_executable.is_absolute():
            raise ValueError("gradle_executable 必须是绝对 Path")
        if cancellation is not None and cancellation.is_cancelled:
            raise ToolExecutionError("Gradle AAB 构建已取消")
        stdout_path = plan.output_directory.parent / "aab-gradle.stdout.log"
        stderr_path = plan.output_directory.parent / "aab-gradle.stderr.log"
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
                f"Gradle AAB 构建失败: outcome={result.outcome.value}, exit_code={result.exit_code}, "
                f"diagnostic={result.diagnostic_message}"
            )
        return AndroidAppBundleBuilder.discover_output(plan)
