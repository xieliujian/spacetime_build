"""Unity batchmode 调用的端口契约。

该端口只描述结构化业务参数和预期输出，Unity 命令行参数的具体排列由适配器负责，资源
任务不得直接拼接 ``-executeMethod`` 或旧项目参数。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UnityBatchRequest:
    """一次 Unity 批处理执行请求。"""

    executable: Path
    project_path: Path
    method: str
    arguments: tuple[str, ...]
    log_path: Path
    timeout_seconds: float
    expected_outputs: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        """校验 Unity 路径、方法、参数、日志和预期输出。"""
        for name in ("executable", "project_path", "log_path"):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{name} 必须是绝对 Path")
        if (
            not isinstance(self.method, str)
            or not self.method
            or any(c in self.method for c in "\r\n")
        ):
            raise ValueError("method 必须是非空且不含换行的字符串")
        if not isinstance(self.arguments, tuple) or any(
            not isinstance(v, str) for v in self.arguments
        ):
            raise TypeError("arguments 必须是 tuple[str, ...]")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是正数")
        if not isinstance(self.expected_outputs, tuple):
            raise TypeError("expected_outputs 必须是 tuple")
        if any(
            not isinstance(path, Path) or not path.is_absolute() for path in self.expected_outputs
        ):
            raise ValueError("expected_outputs 必须全部是绝对 Path")


@dataclass(frozen=True, slots=True)
class UnityBatchResult:
    """Unity 批处理结果摘要。"""

    success: bool
    exit_code: int | None
    log_path: Path
    missing_outputs: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        """校验结果状态与输出路径。"""
        if not isinstance(self.success, bool):
            raise TypeError("success 必须是 bool")
        if not isinstance(self.log_path, Path) or not self.log_path.is_absolute():
            raise ValueError("log_path 必须是绝对 Path")
        if self.success and (self.exit_code != 0 or self.missing_outputs):
            raise ValueError("成功结果必须是零退出且没有缺失输出")
