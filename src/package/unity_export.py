"""Unity Player 导出的结构化 Batch 请求和结果校验。"""

from __future__ import annotations

from pathlib import Path

from core.platforms import BuildPlatform
from ports.unity import UnityBatchRequest, UnityBatchResult


class UnityPlayerExporter:
    """创建平台明确的 Unity Player 导出请求，不直接执行 Unity。"""

    @staticmethod
    def plan(
        platform: BuildPlatform,
        project_path: Path,
        output_path: Path,
        unity_executable: Path,
        log_path: Path,
        *,
        unity_version: str,
        timeout_seconds: float = 3600.0,
        build_settings: tuple[tuple[str, str], ...] = (),
    ) -> UnityBatchRequest:
        """生成供 UnityProvider 消费的类型化导出请求。

        参数：
            platform: 唯一共享 BuildPlatform。
            project_path: 隔离 Unity 工程绝对路径。
            output_path: 预期 Player 输出路径。
            unity_executable: 固定 Unity 可执行文件路径。
            log_path: Unity 日志绝对路径。
            unity_version: 期望工具链版本。
            timeout_seconds: 正超时秒数。
            build_settings: 已白名单化的键值设置。

        返回：
            ``UnityBatchRequest``；Unity 命令排列由 integration adapter 负责。

        异常：
            参数类型、路径、版本或重复设置非法时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            纯内存计划；不启动 Unity、不修改项目、不读取环境变量。
        """
        if not isinstance(platform, BuildPlatform):
            raise TypeError("platform 必须是 BuildPlatform")
        for name, value in (
            ("project_path", project_path),
            ("output_path", output_path),
            ("unity_executable", unity_executable),
            ("log_path", log_path),
        ):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{name} 必须是绝对 Path")
        if not isinstance(unity_version, str) or not unity_version:
            raise ValueError("unity_version 必须是非空字符串")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是正数")
        if not isinstance(build_settings, tuple):
            raise TypeError("build_settings 必须是 tuple")
        keys = [key for key, _value in build_settings]
        if len(set(keys)) != len(keys):
            raise ValueError("build_settings 不得重复")
        arguments = (
            "--platform",
            platform.value,
            "--unity-version",
            unity_version,
            "--output",
            output_path.as_posix(),
            *(value for pair in build_settings for value in pair),
        )
        return UnityBatchRequest(
            unity_executable,
            project_path,
            "BuildPipeline.BuildPlayer",
            arguments,
            log_path,
            float(timeout_seconds),
            (output_path,),
        )

    @staticmethod
    def validate(result: UnityBatchResult) -> bool:
        """返回 Unity 结果是否同时满足零退出和完整输出条件。"""
        if not isinstance(result, UnityBatchResult):
            raise TypeError("result 必须是 UnityBatchResult")
        return result.success and result.exit_code == 0 and not result.missing_outputs
