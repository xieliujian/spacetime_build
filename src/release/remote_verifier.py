"""发布对象远端验证服务。

验证器读取上传计划中的普通对象和版本入口对象，逐项比较远端存在性、SHA256 和大小；
全部通过后再调用已有 ``verify_release_bundle`` 签发不可伪造验证凭证。验证失败不会
执行上传、激活或修补远端对象。
"""

from __future__ import annotations

from core.errors import PublishError
from ports.storage import ObjectStore, StoredObject
from release.activation import VerifiedReleaseBundle, verify_release_bundle
from release.bundles import ReleaseBundle
from release.upload_plan import UploadPlan


class RemoteReleaseVerifier:
    """校验上传计划并签发 ``VerifiedReleaseBundle``。"""

    def __init__(self, object_store: ObjectStore) -> None:
        """保存对象存储读取端口。"""
        self._object_store = object_store

    def verify(self, bundle: ReleaseBundle, plan: UploadPlan) -> VerifiedReleaseBundle:
        """逐项验证远端对象并创建验证凭证。

        参数：
            bundle: 待激活的 ReleaseBundle。
            plan: 与 bundle 绑定的上传计划。

        返回：
            仅在所有计划对象和 bundle 必要传输对象通过时返回验证凭证。

        异常：
            类型、Bundle/计划不一致、对象缺失或哈希/大小不匹配时抛出 ``PublishError``。

        约束与副作用：
            只读对象存储；不上传、不修改入口。
        """
        if not isinstance(bundle, ReleaseBundle) or not isinstance(plan, UploadPlan):
            raise TypeError("bundle 必须是 ReleaseBundle 且 plan 必须是 UploadPlan")
        if bundle.bundle_id != plan.bundle_id:
            raise PublishError("UploadPlan.bundle_id 与 ReleaseBundle.bundle_id 不一致")
        remote_hashes: dict[str, str] = {}
        for item in plan.objects + (plan.version_entry,):
            reference = StoredObject(item.key, item.blob.sha256, item.blob.size)
            observed = self._object_store.verify(reference)
            observed_hash = observed.sha256
            if (
                not observed.exists
                or observed_hash is None
                or observed_hash != item.blob.sha256
                or observed.size != item.blob.size
            ):
                raise PublishError(f"远端对象校验失败: {item.key}")
            remote_hashes[item.blob.sha256] = observed_hash
        return verify_release_bundle(bundle, remote_hashes)
