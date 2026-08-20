"""发布 application 用例的阶段化编排。

发布边界固定为上传不可变对象、远端哈希验证、CAS 激活。每个阶段由计划 21 的
单职责服务注入；用例不生成旧协议文本、不直接访问 CDN，也不在上传失败后递归
重启完整发布。CAS/校验异常转换为可持久化的 FAILED 或 CONFLICTED 结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from application.model import RunState


class ReleaseUploader(Protocol):
    """发布对象上传服务所需的最小协议。"""

    def upload(self, plan: object, *, cancellation: object = None) -> object:
        """上传不可变对象并返回回执。"""
        ...


class ReleaseVerifier(Protocol):
    """远端对象验证服务所需的最小协议。"""

    def verify(self, bundle: object, plan: object) -> object:
        """验证对象并返回不可伪造凭证。"""
        ...


class ReleaseActivator(Protocol):
    """版本入口 CAS 激活服务所需的最小协议。"""

    def activate(self, plan: object, verification: object) -> object:
        """使用验证凭证执行一次 CAS。"""
        ...


@dataclass(frozen=True, slots=True)
class PublishReleaseResult:
    """发布阶段结果，包含已上传回执和错误摘要。"""

    state: RunState
    upload_report: object | None
    verification: object | None
    activation: object | None
    error: str | None


class PublishReleaseUseCase:
    """以固定事务顺序组合发布单职责服务。"""

    def __init__(
        self,
        uploader: ReleaseUploader,
        verifier: ReleaseVerifier,
        activator: ReleaseActivator,
    ) -> None:
        """绑定上传、远端验证和激活依赖，不执行任何阶段。"""
        self._uploader = uploader
        self._verifier = verifier
        self._activator = activator

    def run(
        self,
        bundle: object,
        plan: object,
        *,
        cancellation: object = None,
    ) -> PublishReleaseResult:
        """执行上传→验证→激活并将失败映射为稳定运行状态。

        参数：
            bundle: 已组装的 ReleaseBundle。
            plan: 与 Bundle 绑定的 UploadPlan。
            cancellation: 可选的上传取消上下文。

        返回：
            全部阶段成功为 SUCCEEDED；CAS 冲突为 CONFLICTED；其他异常为 FAILED。

        异常：
            依赖对象自身的编程错误可传播；业务阶段异常被摘要到结果中。

        约束与副作用：
            验证失败时绝不调用激活；已上传对象保留给恢复流程，不删除或重试 CAS。
        """
        uploaded: object | None = None
        verification: object | None = None
        try:
            uploaded = self._uploader.upload(plan, cancellation=cancellation)
            verification = self._verifier.verify(bundle, plan)
            activation = self._activator.activate(plan, verification)
            return PublishReleaseResult(
                RunState.SUCCEEDED, uploaded, verification, activation, None
            )
        except Exception as exc:
            message = str(exc)
            state = (
                RunState.CONFLICTED if "CAS" in message or "冲突" in message else RunState.FAILED
            )
            return PublishReleaseResult(state, uploaded, verification, None, message)

    def publish(self, *args: object, **kwargs: object) -> PublishReleaseResult:
        """``run`` 的领域命令别名。"""
        return self.run(*args, **kwargs)  # type: ignore[arg-type]


__all__ = [
    "PublishReleaseResult",
    "PublishReleaseUseCase",
    "ReleaseActivator",
    "ReleaseUploader",
    "ReleaseVerifier",
]
