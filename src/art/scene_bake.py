"""Unity 场景烘焙任务。

场景烘焙只接受固定源快照和显式场景集合，经 ``UnityBatchRunner`` 端口执行；执行前后
对源树计算确定性摘要，任何源树污染都会失败。模块不执行 SVN update/commit，也不把
Unity 日志或临时路径登记为 CAS 产物。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.errors import ArtifactValidationError, ToolExecutionError
from ports.process import CancellationToken
from ports.unity import UnityBatchRequest, UnityBatchResult


def _tree_digest(root: Path) -> str:
    """按逻辑路径和文件内容计算源树 SHA-256。"""
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file() and not item.is_symlink()),
        key=lambda item: item.relative_to(root).as_posix().encode("utf-8"),
    ):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _scene_path(value: object) -> str:
    """校验场景为安全 slash 相对路径。"""
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ValueError("scene 必须是安全相对路径")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("scene 不得路径逃逸")
    return value


@dataclass(frozen=True, slots=True)
class SceneBakeRequest:
    """描述一次 Unity 场景烘焙请求和源树保护边界。"""

    executable: Path
    project_path: Path
    source_root: Path
    output_root: Path
    scenes: tuple[str, ...]
    method: str
    log_path: Path
    timeout_seconds: float = 600.0
    expected_outputs: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        """校验所有绝对目录、场景和预期输出。"""
        for value, field_name in (
            (self.executable, "executable"),
            (self.project_path, "project_path"),
            (self.source_root, "source_root"),
            (self.output_root, "output_root"),
            (self.log_path, "log_path"),
        ):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{field_name} 必须是绝对 Path")
        if self.source_root.is_symlink() or not self.source_root.is_dir():
            raise ValueError("source_root 必须是普通目录")
        if not isinstance(self.scenes, tuple) or not self.scenes:
            raise ValueError("scenes 必须是非空 tuple")
        scenes = tuple(_scene_path(value) for value in self.scenes)
        if scenes != tuple(sorted(set(scenes), key=lambda item: item.encode("utf-8"))):
            raise ValueError("scenes 必须唯一且按 UTF-8 字节序排列")
        if (
            not isinstance(self.method, str)
            or not self.method
            or any(character in self.method for character in ("\r", "\n"))
        ):
            raise ValueError("method 必须是不含换行的非空字符串")
        if isinstance(self.expected_outputs, tuple):
            root = self.output_root.resolve(strict=False)
            for output in self.expected_outputs:
                if not isinstance(output, Path) or not output.is_absolute():
                    raise ValueError("expected_outputs 必须全部是绝对 Path")
                resolved = output.resolve(strict=False)
                if root not in resolved.parents:
                    raise ValueError("expected_outputs 必须位于 output_root 内")
        else:
            raise TypeError("expected_outputs 必须是 tuple")


@dataclass(frozen=True, slots=True)
class SceneBakeResult:
    """保存 Unity 结果和源树前后摘要。"""

    success: bool
    unity_result: UnityBatchResult
    source_digest_before: str
    source_digest_after: str


class SceneBakeTask:
    """执行受控 Unity 场景烘焙并验证源树未变。"""

    def __init__(self, unity_runner: object) -> None:
        """保存实现 ``run`` 的 Unity 批处理端口。"""
        if not callable(getattr(unity_runner, "run", None)):
            raise TypeError("unity_runner 必须实现 run")
        self._unity_runner = unity_runner

    def build(
        self,
        request: SceneBakeRequest,
        cancellation: CancellationToken | None = None,
    ) -> SceneBakeResult:
        """执行场景烘焙并在源树发生变化时失败。"""
        if not isinstance(request, SceneBakeRequest):
            raise TypeError("request 必须是 SceneBakeRequest")
        request.output_root.mkdir(parents=True, exist_ok=True)
        before = _tree_digest(request.source_root)
        arguments: list[str] = ["--output-root", str(request.output_root)]
        for scene in request.scenes:
            arguments.extend(("--scene", scene))
        unity_request = UnityBatchRequest(
            request.executable,
            request.project_path,
            request.method,
            tuple(arguments),
            request.log_path,
            request.timeout_seconds,
            request.expected_outputs,
        )
        result = self._unity_runner.run(unity_request, cancellation)
        after = _tree_digest(request.source_root)
        if before != after:
            raise ArtifactValidationError("Unity 场景烘焙修改了源快照")
        if not result.success:
            raise ToolExecutionError("Unity 场景烘焙失败")
        return SceneBakeResult(True, result, before, after)


__all__ = ["SceneBakeRequest", "SceneBakeResult", "SceneBakeTask"]
