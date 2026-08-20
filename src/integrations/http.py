"""基于标准库 urllib 的有界 HTTP 传输适配器。"""

from __future__ import annotations

from urllib import request as urllib_request

from core.errors import ToolExecutionError
from ports.http import HttpRequest, HttpResponse, HttpTransport, SecretHttpTarget


class UrllibHttpTransport(HttpTransport):
    """使用 urllib 发送请求，并在读取时限制响应体大小。"""

    def send(self, request: HttpRequest) -> HttpResponse:
        """绑定短期凭据、发送 HTTP 请求并在 finally 关闭租约。"""
        if not isinstance(request, HttpRequest):
            raise TypeError("request 必须是 HttpRequest")
        headers = dict(request.headers)
        lease = request.secret_lease
        try:
            for binding in request.secret_bindings:
                if lease is None:
                    raise ValueError("存在 HTTP secret binding 但未提供租约")
                value = lease.resolve(binding.binding_id)
                if binding.target is SecretHttpTarget.AUTHORIZATION:
                    headers["Authorization"] = value
                else:
                    headers[binding.slot] = value
            http_request = urllib_request.Request(
                request.url,
                data=request.body or None,
                headers=headers,
                method=request.method.value,
            )
            with urllib_request.urlopen(http_request, timeout=request.timeout_seconds) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > request.max_response_bytes:
                    raise ToolExecutionError("HTTP 响应超过配置大小限制")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(64 * 1024, request.max_response_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > request.max_response_bytes:
                        raise ToolExecutionError("HTTP 响应超过配置大小限制")
                    chunks.append(chunk)
                return HttpResponse(
                    response.status, tuple(response.headers.items()), b"".join(chunks)
                )
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError("HTTP 请求执行失败") from exc
        finally:
            if lease is not None:
                lease.close()
