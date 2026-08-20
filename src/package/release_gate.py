"""客户端包体使用已验证 ReleaseBundle 的只读前置门禁。"""

from __future__ import annotations

from core.errors import PublishError
from release.activation import VerifiedReleaseBundle


class PackageReleaseGate:
    """验证包体请求引用的 ReleaseBundle 已完成远端对象验证。"""

    @staticmethod
    def require_verified(
        release_bundle_id: str,
        verification: VerifiedReleaseBundle | None,
    ) -> VerifiedReleaseBundle:
        """返回匹配验证凭证，否则拒绝进入包体构建。

        参数：
            release_bundle_id: 包体请求声明的 ReleaseBundle 内容寻址 ID。
            verification: 发布阶段签发的不可伪造验证凭证。

        返回：
            与请求 ID 一致的 ``VerifiedReleaseBundle``。

        异常：
            凭证缺失、类型错误或 Bundle ID 不一致时抛出 ``PublishError``。

        约束与副作用：
            只读校验，不激活、修改或重新验证 ReleaseBundle。
        """
        if not isinstance(verification, VerifiedReleaseBundle):
            raise PublishError("包体构建必须使用已验证 ReleaseBundle")
        if verification.bundle_id != release_bundle_id:
            raise PublishError("包体请求的 ReleaseBundle ID 与验证凭证不一致")
        return verification
