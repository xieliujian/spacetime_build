"""已验证历史 ReleaseBundle 的回滚计划。

回滚不重新构建、不删除对象，也不直接执行 CAS；它要求目标 Bundle 和入口替换计划
来自已验证历史发布，并返回供 ``ReleaseActivator`` 使用的不可变计划。
"""

from __future__ import annotations

from dataclasses import dataclass

from release.activation import VerifiedReleaseBundle
from release.upload_plan import UploadPlan


@dataclass(frozen=True, slots=True)
class ReleaseRollbackPlan:
    """绑定当前 Bundle、历史目标和验证凭证的回滚计划。"""

    current_bundle_id: str
    target_bundle_id: str
    target_plan: UploadPlan
    verification: VerifiedReleaseBundle


class ReleaseRollbackPlanner:
    """创建不删除对象的历史 Bundle 回滚计划。"""

    @staticmethod
    def create(
        current_bundle_id: str,
        target_bundle_id: str,
        target_plan: UploadPlan,
        verification: VerifiedReleaseBundle | None,
    ) -> ReleaseRollbackPlan:
        """校验历史目标与验证凭证并返回回滚计划。

        参数：
            current_bundle_id: 当前入口指向的 Bundle ID。
            target_bundle_id: 已验证历史 Bundle ID。
            target_plan: 历史 Bundle 对应入口 CAS 计划。
            verification: 远端对象验证凭证。

        返回：
            可交给激活器的不可变回滚计划。

        异常：
            当前/目标相同、计划身份不符或缺少凭证时抛出 ``ValueError``。

        约束与副作用：
            纯内存校验；不删除对象、不读写版本入口、不执行 CAS。
        """
        if not isinstance(current_bundle_id, str) or not current_bundle_id:
            raise ValueError("current_bundle_id 必须是非空字符串")
        if not isinstance(target_bundle_id, str) or not target_bundle_id:
            raise ValueError("target_bundle_id 必须是非空字符串")
        if current_bundle_id == target_bundle_id:
            raise ValueError("回滚目标不得等于当前 Bundle")
        if not isinstance(target_plan, UploadPlan):
            raise TypeError("target_plan 必须是 UploadPlan")
        if target_plan.bundle_id != target_bundle_id:
            raise ValueError("target_plan.bundle_id 与 target_bundle_id 不一致")
        if not isinstance(verification, VerifiedReleaseBundle):
            raise ValueError("回滚必须提供已验证历史 Bundle 凭证")
        if verification.bundle_id != target_bundle_id:
            raise ValueError("验证凭证与回滚目标 Bundle 不一致")
        return ReleaseRollbackPlan(current_bundle_id, target_bundle_id, target_plan, verification)
