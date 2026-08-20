"""验证第二层本地和受控外部适配器。

测试通过 fake ProcessRunner、fake HttpTransport 和临时目录隔离外部副作用，重点确认
路径边界、秘密租约生命周期、确定性源码摘要、Unity 参数组装、Jenkins 句柄转换以及
对象存储的不可变写入和 CAS 冲突语义。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from configuration.model import SecretRef
from integrations.jenkins import JenkinsJobClient
from integrations.secrets import EnvironmentSecretProvider
from integrations.storage import FileSystemObjectStore
from integrations.unity import UnityBatchRunner
from integrations.workspace import LocalWorkspaceProvider
from ports.ci import CiJobHandle, CiJobRequest, CiJobState
from ports.http import HttpRequest, HttpResponse
from ports.process import CancellationToken, ProcessOutcome, ProcessRequest, ProcessResult
from ports.secrets import SecretLeaseRequest
from ports.storage import CompareAndSwapRequest, PutObjectRequest
from ports.unity import UnityBatchRequest
from ports.workspace import WorkspaceRequest


class _FakeRunner:
    """记录进程请求并返回预先设定的结果。"""

    def __init__(self, result: ProcessResult) -> None:
        """保存 fake 结果和调用记录。"""
        self.result = result
        self.requests: list[ProcessRequest] = []
        self.cancellations: list[CancellationToken | None] = []

    def run(
        self, request: ProcessRequest, cancellation: CancellationToken | None = None
    ) -> ProcessResult:
        """记录请求并返回结果。"""
        self.requests.append(request)
        self.cancellations.append(cancellation)
        return self.result


class _FakeTransport:
    """按顺序返回 HTTP 响应并记录请求。"""

    def __init__(self, responses: list[HttpResponse]) -> None:
        """保存响应队列。"""
        self.responses = responses
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        """记录请求并返回队首响应。"""
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("fake HTTP 响应队列为空")
        return self.responses.pop(0)


def _process_result(tmp_path: Path, exit_code: int = 0) -> ProcessResult:
    """创建适合 fake runner 的完成结果。"""
    return ProcessResult(
        ProcessOutcome.COMPLETED,
        exit_code,
        0.01,
        tmp_path / "stdout.log",
        tmp_path / "stderr.log",
        0,
        0,
    )


def test_workspace_provider_is_exclusive_and_removes_successful_lease(tmp_path: Path) -> None:
    """验证工作区同一 build 不能重复租用，成功释放会清理目录。"""
    provider = LocalWorkspaceProvider()
    lease = provider.acquire(WorkspaceRequest(tmp_path, "build-1", preserve_on_failure=False))
    assert lease.path.is_dir()
    with pytest.raises(FileExistsError):
        provider.acquire(WorkspaceRequest(tmp_path, "build-1"))
    provider.release(lease, failed=False)
    assert not lease.path.exists()


def test_environment_secret_lease_closes_and_rejects_unknown_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证环境凭据只通过租约解析，关闭后不能继续使用。"""
    monkeypatch.setenv("SE_BUILD_TOKEN", "secret-value")
    provider = EnvironmentSecretProvider()
    lease = provider.acquire(
        SecretLeaseRequest(SecretRef("secret://env/SE_BUILD_TOKEN"), "test", ("token",))
    )
    assert lease.resolve("token") == "secret-value"
    with pytest.raises(KeyError):
        lease.resolve("other")
    lease.close()
    with pytest.raises(RuntimeError):
        lease.resolve("token")


def test_filesystem_object_store_is_idempotent_and_cas_detects_conflict(tmp_path: Path) -> None:
    """验证对象重复上传幂等、内容冲突失败、验证和 CAS 代际检查。"""
    store = FileSystemObjectStore(tmp_path)
    content = b"payload"
    digest = hashlib.sha256(content).hexdigest()
    request = PutObjectRequest("data/1/file.bin", content, digest)
    stored = store.put(request)
    assert store.put(request) == stored
    with pytest.raises(ValueError):
        store.put(
            PutObjectRequest("data/1/file.bin", b"other", hashlib.sha256(b"other").hexdigest())
        )
    assert store.verify(stored).exists
    result = store.compare_and_swap(CompareAndSwapRequest("version/current", 0, b"v1"))
    assert result.applied and result.generation == 1
    conflict = store.compare_and_swap(CompareAndSwapRequest("version/current", 0, b"v2"))
    assert not conflict.applied and conflict.generation == 1


def test_unity_runner_builds_structured_batchmode_request(tmp_path: Path) -> None:
    """验证 Unity 适配器固定 batchmode 参数并检查预期输出。"""
    executable = tmp_path / "Unity.exe"
    project = tmp_path / "project"
    project.mkdir()
    log_path = tmp_path / "unity.log"
    expected = tmp_path / "output.bin"
    expected.write_bytes(b"output")
    runner = _FakeRunner(_process_result(tmp_path))
    result = UnityBatchRunner(runner).run(
        UnityBatchRequest(executable, project, "Build.Entry", ("x=1",), log_path, 30, (expected,))
    )
    assert result.success
    assert runner.requests[0].arguments[:4] == ("-batchmode", "-quit", "-projectPath", str(project))
    assert "-executeMethod" in runner.requests[0].arguments


def test_unity_runner_forwards_cancellation_token_to_process_port(tmp_path: Path) -> None:
    """验证 Unity 适配器将取消令牌交给进程端口，而不是吞掉取消语义。"""
    project = tmp_path / "project"
    project.mkdir()
    expected = tmp_path / "output.bin"
    expected.write_bytes(b"output")
    runner = _FakeRunner(_process_result(tmp_path))
    cancellation = CancellationToken()
    UnityBatchRunner(runner).run(
        UnityBatchRequest(
            tmp_path / "Unity.exe",
            project,
            "Build.Entry",
            (),
            tmp_path / "unity.log",
            30,
            (expected,),
        ),
        cancellation,
    )

    assert runner.cancellations == [cancellation]


def test_jenkins_client_converts_queue_location_and_cancels_running_build() -> None:
    """验证 Jenkins 触发、状态和取消请求只通过 HTTP 端口完成。"""
    transport = _FakeTransport(
        [
            HttpResponse(201, (("Location", "https://jenkins.example/queue/item/7/"),), b""),
            HttpResponse(200, (), b'{"executable":{"number":12},"result":null,"building":true}'),
            HttpResponse(200, (), b""),
        ]
    )
    client = JenkinsJobClient("https://jenkins.example", transport)
    handle = client.trigger(CiJobRequest("build-job", (("BUILD_ID", "b1"),), "req-1"))
    status = client.get_status(handle)
    assert handle == CiJobHandle("build-job", "7")
    assert status.handle == CiJobHandle("build-job", "7", 12)
    assert status.state is CiJobState.RUNNING
    assert client.cancel(handle)
    assert transport.requests[0].method.value == "POST"
    assert transport.requests[-1].method.value == "POST"
