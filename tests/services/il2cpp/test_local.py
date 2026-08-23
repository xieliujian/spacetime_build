"""验证 IL2CPP 本地执行器的真实进程、输出和 CAS 边界。"""

from __future__ import annotations

from pathlib import Path

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from ports.process import ProcessOutcome, ProcessRequest, ProcessResult
from ports.storage import PutObjectRequest, StoredObject
from services.il2cpp.local import Il2CppLocalExecutor
from services.il2cpp.model import Il2CppBuildRequest, Il2CppExecutionMode
from services.il2cpp.planner import Il2CppPlanner, Il2CppToolchain


class _Runner:
    """在执行目录生成 IL2CPP 输出的进程替身。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.requests: list[ProcessRequest] = []

    def run(self, request: ProcessRequest, cancellation: object = None) -> ProcessResult:
        """生成预期归档并返回成功结果。"""
        self.requests.append(request)
        output = request.working_directory / "il2cpp-output.zip"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"il2cpp")
        request.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        request.stdout_path.write_bytes(b"")
        request.stderr_path.write_bytes(b"")
        return ProcessResult(
            ProcessOutcome.COMPLETED,
            0,
            0.01,
            request.stdout_path,
            request.stderr_path,
            0,
            0,
        )


class _Store:
    """返回一致 Blob 回执的内存对象存储。"""

    def put(self, request: PutObjectRequest) -> StoredObject:
        """返回请求摘要。"""
        return StoredObject(request.key, request.sha256, len(request.content))


def _plan(tmp_path: Path):
    """创建 local IL2CPP 命令计划。"""
    input_digest = "a" * 64
    request = Il2CppBuildRequest(
        "req-1",
        BuildPlatform.WINDOWS,
        "x86_64",
        BlobRef(f"blobs/{input_digest}", input_digest, 10),
        "2022.3.62f2",
        "toolchain-1",
        Il2CppExecutionMode.LOCAL,
        None,
    )
    toolchain = Il2CppToolchain(
        request.unity_version,
        str(tmp_path / "Unity.exe"),
        "unity-il2cpp-v1",
        (),
        (("unity", request.unity_version),),
    )
    return Il2CppPlanner.plan(request, tmp_path / "workspace", toolchain=toolchain)


def test_local_executor_runs_plan_and_commits_output_snapshot(tmp_path: Path) -> None:
    """成功 Unity 进程和普通输出应产生 CAS Blob。"""
    runner = _Runner()
    executor = Il2CppLocalExecutor(
        runner,
        executable=tmp_path / "Unity.exe",
        blob_committer=__import__(
            "resource.blob_committer", fromlist=["BlobCommitter"]
        ).BlobCommitter(_Store()),
        log_root=tmp_path / "logs",
    )

    result = executor.run(_plan(tmp_path))

    assert result.status.value == "succeeded"
    assert result.output_snapshot is not None
    assert runner.requests[0].arguments[0] == "-batchmode"
