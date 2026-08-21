"""验证 Jenkins HTTP 适配器的 Authorization binding 和短期租约边界。"""

from __future__ import annotations

import pytest

from configuration.model import SecretRef
from integrations.jenkins import JenkinsJobClient
from ports.ci import CiJobRequest
from ports.http import HttpRequest, HttpResponse, SecretHttpTarget
from ports.secrets import SecretLease, SecretLeaseRequest


class _Lease(SecretLease):
    """保存一次 Jenkins 请求的测试租约。"""

    def __init__(self) -> None:
        """初始化关闭计数和解析记录。"""
        self.close_calls = 0
        self.resolve_calls: list[str] = []

    def resolve(self, binding_id: str) -> str:
        """返回测试 Authorization 值并记录 opaque binding。"""
        self.resolve_calls.append(binding_id)
        return "Bearer jenkins-secret"

    def close(self) -> None:
        """记录租约关闭。"""
        self.close_calls += 1

    def __repr__(self) -> str:
        """返回不含秘密的租约表示。"""
        return "JenkinsLease(<redacted>)"


class _SecretProvider:
    """为每次 Jenkins HTTP 操作申请独立租约。"""

    def __init__(self) -> None:
        """初始化租约和申请记录。"""
        self.leases: list[_Lease] = []
        self.requests: list[SecretLeaseRequest] = []

    def acquire(self, request: SecretLeaseRequest) -> SecretLease:
        """记录申请并返回本次操作专用租约。"""
        self.requests.append(request)
        lease = _Lease()
        self.leases.append(lease)
        return lease


class _Transport:
    """验证 Authorization binding 后返回固定 Jenkins 响应。"""

    def __init__(self, responses: list[HttpResponse]) -> None:
        """保存响应序列和请求记录。"""
        self.responses = responses
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        """确认 transport 在发送边界解析秘密。"""
        self.requests.append(request)
        assert len(request.secret_bindings) == 1
        binding = request.secret_bindings[0]
        assert binding.target is SecretHttpTarget.AUTHORIZATION
        assert binding.slot == "Authorization"
        assert request.secret_lease is not None
        assert request.secret_lease.resolve(binding.binding_id) == "Bearer jenkins-secret"
        if not self.responses:
            raise AssertionError("fake Jenkins 响应队列为空")
        return self.responses.pop(0)


def test_jenkins_client_uses_short_lived_authorization_lease_for_each_operation() -> None:
    """验证触发、查询、取消均使用独立 lease 且不泄漏秘密。"""
    provider = _SecretProvider()
    transport = _Transport(
        [
            HttpResponse(201, (("Location", "https://jenkins.example/queue/item/7/"),), b""),
            HttpResponse(200, (), b'{"executable":{"number":12},"result":null,"building":true}'),
            HttpResponse(200, (), b""),
        ]
    )
    client = JenkinsJobClient(
        "https://jenkins.example",
        transport,
        credential=SecretRef("secret://env/JENKINS_TOKEN"),
        secret_provider=provider,
    )

    handle = client.trigger(CiJobRequest("build-job", (("BUILD_ID", "b1"),), "req-1"))
    client.get_status(handle)
    assert client.cancel(handle)

    assert len(provider.requests) == 3
    assert all(request.binding_ids == ("jenkins-authorization",) for request in provider.requests)
    assert all(lease.close_calls == 1 for lease in provider.leases)
    assert all("jenkins-secret" not in repr(request) for request in transport.requests)


def test_jenkins_client_closes_lease_when_transport_fails() -> None:
    """验证 Jenkins transport 异常路径也释放已经获取的租约。"""
    provider = _SecretProvider()

    class FailingTransport:
        """注入网络失败的 transport。"""

        def send(self, request: HttpRequest) -> HttpResponse:
            """抛出不含秘密的连接异常。"""
            raise ConnectionError("jenkins unavailable")

    client = JenkinsJobClient(
        "https://jenkins.example",
        FailingTransport(),
        credential=SecretRef("secret://env/JENKINS_TOKEN"),
        secret_provider=provider,
    )

    with pytest.raises(ConnectionError):
        client.trigger(CiJobRequest("build-job", (), "req-1"))

    assert len(provider.leases) == 1
    assert provider.leases[0].close_calls == 1
