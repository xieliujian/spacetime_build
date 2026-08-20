"""发布不可变对象的幂等上传服务。

本模块只处理 ``UploadPlan.objects``，不上传版本入口、不执行远端验证和不调用 CAS。
相同键/相同内容由 ObjectStore 自身幂等处理；明确的临时异常可有限重试，取消会在
下一个新对象前停止并保留已成功上传对象。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.errors import PublishError
from ports.storage import ObjectStore, PutObjectRequest
from release.upload_plan import UploadPlan


class UploadTransientError(PublishError):
    """表示对象上传可安全有限重试的临时错误。"""


class UploadCancelled(PublishError):
    """表示上传在开始下一个对象前被取消。"""


class UploadCancellation(Protocol):
    """上传取消上下文的最小只读协议。"""

    @property
    def is_cancelled(self) -> bool:
        """返回调用方是否请求取消。"""
        ...


@dataclass(frozen=True, slots=True)
class UploadReport:
    """对象上传结果摘要。"""

    uploaded_keys: tuple[str, ...]
    attempts: int


class ReleaseObjectUploader:
    """按上传计划幂等写入不可变对象。"""

    def __init__(self, object_store: ObjectStore, *, max_retries: int = 0) -> None:
        """保存对象存储端口和临时失败最大重试次数。"""
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise ValueError("max_retries 必须是非负整数")
        self._object_store = object_store
        self._max_retries = max_retries

    def upload(
        self,
        plan: UploadPlan,
        *,
        cancellation: UploadCancellation | None = None,
    ) -> UploadReport:
        """上传计划中的普通对象并返回成功摘要。

        参数：
            plan: 已验证上传计划。
            cancellation: 可选取消上下文。

        返回：
            已成功提交的普通对象键和总尝试次数；版本入口不会出现在列表中。

        异常：
            临时失败超过预算、永久失败或取消时抛出 ``PublishError`` 子类。

        约束与副作用：
            严格按计划顺序上传；不验证远端对象、不更新入口、不删除已上传对象。
        """
        if not isinstance(plan, UploadPlan):
            raise TypeError("plan 必须是 UploadPlan")
        uploaded: list[str] = []
        attempts = 0
        for item in plan.objects:
            if cancellation is not None and cancellation.is_cancelled:
                raise UploadCancelled("上传已取消")
            retries = 0
            while True:
                attempts += 1
                try:
                    stored = self._object_store.put(
                        PutObjectRequest(item.key, item.content, item.blob.sha256)
                    )
                    if (
                        stored.key != item.key
                        or stored.sha256 != item.blob.sha256
                        or stored.size != item.blob.size
                    ):
                        raise PublishError(f"对象存储回执不一致: {item.key}")
                    uploaded.append(item.key)
                    break
                except UploadTransientError:
                    if retries >= self._max_retries:
                        raise
                    retries += 1
                except (TimeoutError, ConnectionError) as exc:
                    if retries >= self._max_retries:
                        raise UploadTransientError(f"上传临时失败且重试耗尽: {item.key}") from exc
                    retries += 1
                except PublishError:
                    raise
                except Exception as exc:
                    raise PublishError(f"对象上传失败: {item.key}") from exc
        return UploadReport(tuple(uploaded), attempts)
