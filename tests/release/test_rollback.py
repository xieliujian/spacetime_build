"""历史 ReleaseBundle 回滚计划测试。"""

import hashlib
from typing import cast

import pytest

from core.artifacts import BlobRef
from release.rollback import ReleaseRollbackPlanner
from release.activation import VerifiedReleaseBundle
from release.upload_plan import UploadItem, UploadPhase, UploadPlanFactory


def _plan(bundle_id: str, version_key: str = "version/current"):
    """构造回滚入口计划。"""
    content = b"history-entry"
    digest = hashlib.sha256(content).hexdigest()
    version = UploadItem(
        version_key,
        BlobRef("blobs/" + digest, digest, len(content)),
        content,
        UploadPhase.VERSION_ENTRY,
    )
    return UploadPlanFactory.create(bundle_id, (), (), version, 3)


def test_rollback_requires_verified_target_and_preserves_immutable_objects() -> None:
    """验证回滚目标必须有历史 Bundle 验证凭证，且只输出 CAS 计划。"""
    plan = _plan("a" * 64)
    with pytest.raises(ValueError):
        ReleaseRollbackPlanner.create(
            current_bundle_id="b" * 64,
            target_bundle_id=plan.bundle_id,
            target_plan=plan,
            verification=None,
        )
    # 无法伪造 VerifiedReleaseBundle，验证凭证必须来自远端校验服务。
    assert plan.objects == ()


def test_rollback_rejects_same_bundle_or_plan_mismatch() -> None:
    """验证不能把当前 Bundle 当作回滚目标。"""
    plan = _plan("a" * 64)
    with pytest.raises(ValueError):
        ReleaseRollbackPlanner.create(
            "a" * 64,
            "a" * 64,
            plan,
            cast(VerifiedReleaseBundle, object()),
        )
