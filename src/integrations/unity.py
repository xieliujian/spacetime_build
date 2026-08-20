"""Unity batchmode 结构化命令适配器。"""

from __future__ import annotations

from ports.process import CancellationToken, ProcessRequest, ProcessRunner
from ports.unity import UnityBatchRequest, UnityBatchResult


class UnityBatchRunner:
    """把 Unity 批处理请求转换为安全的参数序列并执行。"""

    def __init__(self, process_runner: ProcessRunner) -> None:
        """保存进程端口依赖。"""
        self._process_runner = process_runner

    def run(
        self,
        request: UnityBatchRequest,
        cancellation: CancellationToken | None = None,
    ) -> UnityBatchResult:
        """执行 Unity 并校验退出码和声明的输出文件。"""
        if not isinstance(request, UnityBatchRequest):
            raise TypeError("request 必须是 UnityBatchRequest")
        request.log_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path = request.log_path.with_name(request.log_path.name + ".stderr")
        arguments = (
            "-batchmode",
            "-quit",
            "-projectPath",
            str(request.project_path),
            "-logFile",
            str(request.log_path),
            "-executeMethod",
            request.method,
            *request.arguments,
        )
        process_request = ProcessRequest(
            executable=request.executable,
            arguments=arguments,
            working_directory=request.project_path,
            stdout_path=request.log_path,
            stderr_path=stderr_path,
            timeout_seconds=request.timeout_seconds,
        )
        result = self._process_runner.run(process_request, cancellation)
        missing = tuple(path for path in request.expected_outputs if not path.is_file())
        success = result.exit_code == 0 and result.outcome.value == "completed" and not missing
        return UnityBatchResult(success, result.exit_code, request.log_path, missing)
