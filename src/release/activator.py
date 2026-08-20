"""通过 CAS 原子更新版本入口的发布激活服务。

激活器要求先由 ``RemoteReleaseVerifier`` 签发 ``VerifiedReleaseBundle``，再以
``UploadPlan.expected_generation`` 和版本入口规范字节执行 compare-and-swap。CAS
冲突不会重试；若远端已经指向相同入口内容，则按幂等成功返回。普通对象不会被删除。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from core.errors import PublishError
from ports.storage import CompareAndSwapRequest, ObjectStore
from release.activation import VerifiedReleaseBundle
from release.upload_plan import UploadPlan


@dataclass(frozen=True, slots=True)
class ActivationResult:
    """CAS 激活结果摘要。"""

    applied: bool
    idempotent: bool
    generation: int
    sha256: str


class ReleaseActivator:
    """使用验证凭证和 CAS 更新版本入口。"""

    def __init__(self, object_store: ObjectStore) -> None:
        """保存版本入口对象存储端口。"""
        self._object_store = object_store

    def activate(
        self,
        plan: UploadPlan,
        verification: VerifiedReleaseBundle,
    ) -> ActivationResult:
        """验证 bundle 绑定后执行一次 CAS 激活。

        参数：
            plan: 包含版本入口和 expected generation 的上传计划。
            verification: 远端对象全部通过后的不可伪造验证凭证。

        返回：
            applied 表示本次 CAS 是否写入；idempotent 表示已是相同入口内容。

        异常：
            凭证类型/Bundle 不一致或 CAS 失败时抛出 ``PublishError``。

        约束与副作用：
            只更新版本入口；不重试冲突，不删除不可变对象。
        """
        if not isinstance(plan, UploadPlan):
            raise TypeError("plan 必须是 UploadPlan")
        if not isinstance(verification, VerifiedReleaseBundle):
            raise PublishError("激活必须提供 VerifiedReleaseBundle")
        if verification.bundle_id != plan.bundle_id:
            raise PublishError("验证凭证 bundle_id 与上传计划不一致")
        content = plan.version_entry.content
        digest = hashlib.sha256(content).hexdigest()
        result = self._object_store.compare_and_swap(
            CompareAndSwapRequest(
                key=plan.version_entry.key,
                expected_generation=plan.expected_generation,
                content=content,
            )
        )
        if result.applied:
            if result.sha256 != digest:
                raise PublishError("CAS 成功但入口摘要回执不一致")
            return ActivationResult(True, False, result.generation, digest)
        if result.sha256 == digest and result.generation > plan.expected_generation:
            return ActivationResult(False, True, result.generation, digest)
        raise PublishError(
            "版本入口 CAS 冲突："
            f"expected_generation={plan.expected_generation}, actual_generation={result.generation}"
        )
