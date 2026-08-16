"""外部系统稳定端口的公共导出。

本包当前公开进程请求、结果、取消令牌、秘密绑定描述与执行器协议。类型只定义
外部调用边界，不包含具体适配器，不启动外部程序、不解析秘密，也不产生 I/O。
"""

from ports.process import (
    CancellationToken,
    ProcessOutcome,
    ProcessRequest,
    ProcessResult,
    ProcessRunner,
    ProcessTextSink,
    SecretBindingTarget,
    SecretLease,
    SecretProcessBinding,
)

__all__ = [
    "CancellationToken",
    "ProcessOutcome",
    "ProcessRequest",
    "ProcessResult",
    "ProcessRunner",
    "ProcessTextSink",
    "SecretBindingTarget",
    "SecretLease",
    "SecretProcessBinding",
]
