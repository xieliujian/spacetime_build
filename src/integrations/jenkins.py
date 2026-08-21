"""Jenkins HTTP Job 适配器。

适配器只把结构化 ``CiJobRequest`` 转为受控路径和参数，队列项、构建号与状态转换均在
本模块完成。认证通过 ``HttpTransport`` 的秘密 binding 处理，Jenkins 客户端不接触明文。
"""

from __future__ import annotations

import json
import re
from typing import cast
from urllib.parse import quote, urlencode

from configuration.model import SecretRef
from core.errors import ToolExecutionError
from integrations.secrets import SecretLeaseGuard
from ports.ci import CiJobClient, CiJobHandle, CiJobRequest, CiJobState, CiJobStatus
from ports.http import (
    HttpMethod,
    HttpRequest,
    HttpResponse,
    HttpTransport,
    SecretHttpBinding,
    SecretHttpTarget,
)
from ports.secrets import SecretLeaseRequest, SecretProvider


class JenkinsJobClient(CiJobClient):
    """通过注入的 HTTP 端口访问 Jenkins REST API。"""

    def __init__(
        self,
        base_url: str,
        transport: HttpTransport,
        *,
        credential: SecretRef | None = None,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        """校验 Jenkins 根 URL 并保存可选的短期凭据依赖。"""
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须是 http(s) URL")
        if credential is not None and not isinstance(credential, SecretRef):
            raise TypeError("credential 必须是 SecretRef 或 None")
        if credential is None and secret_provider is not None:
            raise ValueError("未提供 credential 时不得绑定 secret_provider")
        if credential is not None and not callable(getattr(secret_provider, "acquire", None)):
            raise ValueError("使用 credential 时必须提供 SecretProvider")
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._credential = credential
        self._secret_provider = secret_provider
        self._authorization_binding = SecretHttpBinding(
            "jenkins-authorization",
            SecretHttpTarget.AUTHORIZATION,
            "Authorization",
        )

    def _send(
        self,
        method: HttpMethod,
        path: str,
        body: bytes = b"",
        *,
        check_status: bool = True,
    ) -> HttpResponse:
        """在单次 HTTP 请求边界申请并释放 Jenkins 凭据租约。"""
        lease: SecretLeaseGuard | None = None
        bindings: tuple[SecretHttpBinding, ...] = ()
        if self._credential is not None:
            provider = self._secret_provider
            if provider is None:
                raise RuntimeError("Jenkins 凭据 provider 未装配")
            raw_lease = provider.acquire(
                SecretLeaseRequest(
                    self._credential,
                    "jenkins-http",
                    (self._authorization_binding.binding_id,),
                )
            )
            lease = SecretLeaseGuard(raw_lease)
            bindings = (self._authorization_binding,)
        try:
            response = self._transport.send(
                HttpRequest(
                    method,
                    self._base_url + path,
                    headers=(("Content-Type", "application/x-www-form-urlencoded"),),
                    body=body,
                    secret_bindings=bindings,
                    secret_lease=lease,
                )
            )
        finally:
            if lease is not None:
                lease.close()
        if check_status and response.status_code >= 400:
            raise ToolExecutionError(f"Jenkins HTTP 请求失败: {response.status_code}")
        return response

    def trigger(self, request: CiJobRequest) -> CiJobHandle:
        """触发参数化 Job 并从 Location 解析队列 ID。"""
        if not isinstance(request, CiJobRequest):
            raise TypeError("request 必须是 CiJobRequest")
        job = quote(request.job_name, safe="")
        body = urlencode(dict(request.parameters)).encode("utf-8")
        response = self._send(HttpMethod.POST, f"/job/{job}/buildWithParameters", body)
        location = next(
            (value for name, value in response.headers if name.casefold() == "location"), ""
        )
        match = re.search(r"/queue/item/([1-9][0-9]*)/?$", location)
        if match is None:
            raise ToolExecutionError("Jenkins 响应缺少合法队列 Location")
        return CiJobHandle(request.job_name, match.group(1))

    def get_status(self, handle: CiJobHandle) -> CiJobStatus:
        """查询队列或构建状态并转换为稳定状态枚举。"""
        if not isinstance(handle, CiJobHandle):
            raise TypeError("handle 必须是 CiJobHandle")
        job = quote(handle.job_name, safe="")
        if handle.build_id is None:
            response = self._send(HttpMethod.GET, f"/queue/item/{handle.queue_id}/api/json")
            payload = self._json(response)
            if payload.get("cancelled"):
                return CiJobStatus(handle, CiJobState.CANCELLED)
            executable_raw = payload.get("executable")
            if not isinstance(executable_raw, dict):
                return CiJobStatus(handle, CiJobState.QUEUED)
            executable = cast(dict[str, object], executable_raw)
            build_number = executable.get("number")
            if not isinstance(build_number, int):
                return CiJobStatus(handle, CiJobState.QUEUED)
            new_handle = CiJobHandle(handle.job_name, handle.queue_id, build_number)
            return CiJobStatus(new_handle, CiJobState.RUNNING)
        response = self._send(HttpMethod.GET, f"/job/{job}/{handle.build_id}/api/json")
        payload = self._json(response)
        if payload.get("building"):
            return CiJobStatus(handle, CiJobState.RUNNING)
        result = payload.get("result")
        result_text = result if isinstance(result, str) else None
        if result_text == "SUCCESS":
            return CiJobStatus(handle, CiJobState.SUCCESS, result_text)
        if result_text == "ABORTED":
            return CiJobStatus(handle, CiJobState.CANCELLED, result_text)
        return CiJobStatus(handle, CiJobState.FAILED, result_text)

    def cancel(self, handle: CiJobHandle) -> bool:
        """取消队列项或运行中构建，终态重复取消返回 False。"""
        if not isinstance(handle, CiJobHandle):
            raise TypeError("handle 必须是 CiJobHandle")
        if handle.build_id is None:
            path = f"/queue/cancelItem?id={quote(handle.queue_id, safe='')}"
        else:
            job = quote(handle.job_name, safe="")
            path = f"/job/{job}/{handle.build_id}/stop"
        response = self._send(HttpMethod.POST, path, check_status=False)
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise ToolExecutionError(f"Jenkins 取消请求失败: {response.status_code}")
        return True

    @staticmethod
    def _json(response: HttpResponse) -> dict[str, object]:
        """解析 Jenkins JSON 对象并拒绝非对象响应。"""
        try:
            payload_raw: object = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolExecutionError("Jenkins 响应不是合法 UTF-8 JSON") from exc
        if not isinstance(payload_raw, dict):
            raise ToolExecutionError("Jenkins 响应 JSON 根节点不是对象")
        return cast(dict[str, object], payload_raw)
