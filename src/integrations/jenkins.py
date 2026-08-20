"""Jenkins HTTP Job 适配器。

适配器只把结构化 ``CiJobRequest`` 转为受控路径和参数，队列项、构建号与状态转换均在
本模块完成。认证通过 ``HttpTransport`` 的秘密 binding 处理，Jenkins 客户端不接触明文。
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode

from core.errors import ToolExecutionError
from ports.ci import CiJobClient, CiJobHandle, CiJobRequest, CiJobState, CiJobStatus
from ports.http import HttpMethod, HttpRequest, HttpResponse, HttpTransport


class JenkinsJobClient(CiJobClient):
    """通过注入的 HTTP 端口访问 Jenkins REST API。"""

    def __init__(self, base_url: str, transport: HttpTransport) -> None:
        """校验 Jenkins 根 URL 并保存传输依赖。"""
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须是 http(s) URL")
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def _send(self, method: HttpMethod, path: str, body: bytes = b"") -> HttpResponse:
        """发送 Jenkins API 请求并返回响应。"""
        response = self._transport.send(
            HttpRequest(
                method,
                self._base_url + path,
                headers=(("Content-Type", "application/x-www-form-urlencoded"),),
                body=body,
            )
        )
        if response.status_code >= 400:
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
            executable = payload.get("executable")
            if not isinstance(executable, dict) or not isinstance(executable.get("number"), int):
                return CiJobStatus(handle, CiJobState.QUEUED)
            new_handle = CiJobHandle(handle.job_name, handle.queue_id, executable["number"])
            return CiJobStatus(new_handle, CiJobState.RUNNING)
        response = self._send(HttpMethod.GET, f"/job/{job}/{handle.build_id}/api/json")
        payload = self._json(response)
        if payload.get("building"):
            return CiJobStatus(handle, CiJobState.RUNNING)
        result = payload.get("result")
        if result == "SUCCESS":
            return CiJobStatus(handle, CiJobState.SUCCESS, result)
        if result == "ABORTED":
            return CiJobStatus(handle, CiJobState.CANCELLED, result)
        return CiJobStatus(handle, CiJobState.FAILED, result if isinstance(result, str) else None)

    def cancel(self, handle: CiJobHandle) -> bool:
        """取消队列项或运行中构建，终态重复取消返回 False。"""
        if not isinstance(handle, CiJobHandle):
            raise TypeError("handle 必须是 CiJobHandle")
        if handle.build_id is None:
            path = f"/queue/cancelItem?id={quote(handle.queue_id, safe='')}"
        else:
            job = quote(handle.job_name, safe="")
            path = f"/job/{job}/{handle.build_id}/stop"
        response = self._transport.send(HttpRequest(HttpMethod.POST, self._base_url + path))
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise ToolExecutionError(f"Jenkins 取消请求失败: {response.status_code}")
        return True

    @staticmethod
    def _json(response: HttpResponse) -> dict[str, object]:
        """解析 Jenkins JSON 对象并拒绝非对象响应。"""
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolExecutionError("Jenkins 响应不是合法 UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ToolExecutionError("Jenkins 响应 JSON 根节点不是对象")
        return payload
