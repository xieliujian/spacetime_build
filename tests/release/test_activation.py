"""验证独立 ``ReleaseActivationRecord`` 与受约束激活状态迁移。

本模块按第二阶段 Task 15 分步覆盖：激活记录只引用 bundle ID、拒绝未知
schema，以及 ``VerifiedReleaseBundle`` / ``advance_activation`` 的合法迁移。
测试不访问 CDN、CAS 或外部系统，也不实现回滚。
"""

from __future__ import annotations

import dataclasses

import pytest

from st.build.core.artifacts import BlobRef
from st.build.core.errors import PublishError
from st.build.release.activation import (
    RELEASE_ACTIVATION_SCHEMA_VERSION,
    ReleaseActivationRecord,
    ReleaseActivationStatus,
    VerifiedReleaseBundle,
    advance_activation,
    verify_release_bundle,
)
from st.build.release.bundle_codec import ReleaseBundleFactory
from st.build.release.bundles import (
    RELEASE_BUNDLE_SCHEMA_VERSION,
    ReleaseBundlePayload,
)
from st.build.release.entries import (
    ReleaseEntry,
    ReleaseObjectOrigin,
    ResourceVariant,
)
from st.build.release.manifest_codec import ReleaseManifestFactory
from st.build.release.manifests import (
    RELEASE_MANIFEST_SCHEMA_VERSION,
    ReleaseManifestPayload,
)
from st.build.release.snapshots import (
    ReleaseArtifactClass,
    ReleaseMembership,
    ReleaseSnapshot,
    ReleaseSnapshotEntry,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_MD5_A = "1" * 32
_BOTH = frozenset({ReleaseMembership.FILE_LIST, ReleaseMembership.ASSET_BUNDLE_DATABASE})


def _blob(sha256: str, *, size: int = 100) -> BlobRef:
    """构造测试用 ``BlobRef``。"""
    return BlobRef(locator=f"sha256:{sha256}", sha256=sha256, size=size)


def _entry(
    *,
    logical_path: str,
    variant: ResourceVariant,
    object_version: str,
    object_origin: ReleaseObjectOrigin = ReleaseObjectOrigin.CURRENT_UPLOAD,
    transfer_sha: str = _SHA_B,
) -> ReleaseEntry:
    """构造测试用 ``ReleaseEntry``。"""
    return ReleaseEntry(
        logical_path=logical_path,
        variant=variant,
        source_blob=_blob(_SHA_A),
        source_md5=_MD5_A,
        original_size=100,
        transfer_blob=_blob(transfer_sha, size=80),
        transfer_size=80,
        list_version=1,
        object_version=object_version,
        file_url=f"https://cdn.example/{logical_path}",
        subpackage_flag=0,
        object_origin=object_origin,
    )


def _ab(release_entry: ReleaseEntry) -> ReleaseSnapshotEntry:
    """包装为 AssetBundle 快照条目。"""
    return ReleaseSnapshotEntry(
        release_entry=release_entry,
        artifact_class=ReleaseArtifactClass.ASSET_BUNDLE,
        memberships=_BOTH,
        assetbundle_dependencies=(),
        redirect_slice=None,
    )


def _make_bundle(*, transfer_sha: str = _SHA_B):
    """组装并经工厂创建的单 MAIN ``ReleaseBundle``。"""
    snapshot = ReleaseSnapshot.create(
        ResourceVariant.MAIN,
        (
            _ab(
                _entry(
                    logical_path="scene/a.ab",
                    variant=ResourceVariant.MAIN,
                    object_version="123",
                    transfer_sha=transfer_sha,
                )
            ),
        ),
    )
    manifest = ReleaseManifestFactory.create(
        ReleaseManifestPayload(
            schema_version=RELEASE_MANIFEST_SCHEMA_VERSION,
            variant=ResourceVariant.MAIN,
            file_list_no=123,
            snapshot=snapshot,
            source_manifest_ids=("src-a",),
        )
    )
    return ReleaseBundleFactory.create(
        ReleaseBundlePayload(
            schema_version=RELEASE_BUNDLE_SCHEMA_VERSION,
            manifests=(manifest,),
            baseline_bundle_id=None,
        )
    )


def _preparing_record(
    *,
    bundle_id: str,
    required_objects_digest: str,
) -> ReleaseActivationRecord:
    """构造 PREPARING 状态的激活记录。"""
    return ReleaseActivationRecord(
        schema_version=RELEASE_ACTIVATION_SCHEMA_VERSION,
        activation_id="act-flow-001",
        bundle_id=bundle_id,
        target="cdn-main",
        expected_generation=3,
        status=ReleaseActivationStatus.PREPARING,
        required_objects_digest=required_objects_digest,
        verified_objects_digest=None,
        error=None,
    )


def test_activation_record_tracks_bundle_state_and_rejects_unknown_schema() -> None:
    """验证激活记录表达生命周期状态、只引用 bundle ID，并拒绝未知 schema。

    测试无参数和返回值。断言：

    - ``ReleaseActivationStatus`` 可表达 preparing/uploading/verifying/
      activating/active/failed/conflicted；
    - 可构造 ``ReleaseActivationRecord(schema_version, activation_id,
      bundle_id, target, expected_generation, status, required_objects_digest,
      verified_objects_digest, error)``，字段只引用 bundle ID 字符串；
    - ``schema_version`` 必须等于 ``RELEASE_ACTIVATION_SCHEMA_VERSION``，未知
      版本抛出 ``PublishError``；
    - ``VerifiedReleaseBundle`` 禁止公开直接构造（私有令牌门禁）；
    - 创建多条不同状态记录不改变所引用的 ``bundle_id``；
    - 记录不可变。

    当 ``st.build.release.activation`` 尚未创建时，测试收集阶段应以
    ``ModuleNotFoundError`` 失败。除导入外不产生外部副作用。
    """
    assert RELEASE_ACTIVATION_SCHEMA_VERSION == 1

    expected_statuses = {
        ReleaseActivationStatus.PREPARING,
        ReleaseActivationStatus.UPLOADING,
        ReleaseActivationStatus.VERIFYING,
        ReleaseActivationStatus.ACTIVATING,
        ReleaseActivationStatus.ACTIVE,
        ReleaseActivationStatus.FAILED,
        ReleaseActivationStatus.CONFLICTED,
    }
    assert {member.value for member in expected_statuses} == {
        "preparing",
        "uploading",
        "verifying",
        "activating",
        "active",
        "failed",
        "conflicted",
    }

    bundle_id = "a" * 64
    required_digest = "req-digest-v1"

    preparing = ReleaseActivationRecord(
        schema_version=RELEASE_ACTIVATION_SCHEMA_VERSION,
        activation_id="act-001",
        bundle_id=bundle_id,
        target="cdn-main",
        expected_generation=7,
        status=ReleaseActivationStatus.PREPARING,
        required_objects_digest=required_digest,
        verified_objects_digest=None,
        error=None,
    )
    assert preparing.bundle_id == bundle_id
    assert preparing.target == "cdn-main"
    assert preparing.expected_generation == 7
    assert preparing.status is ReleaseActivationStatus.PREPARING
    assert preparing.required_objects_digest == required_digest
    assert preparing.verified_objects_digest is None
    assert preparing.error is None

    # 字段集合只引用 bundle_id，不嵌入 ReleaseBundle 对象。
    field_names = {f.name for f in dataclasses.fields(ReleaseActivationRecord)}
    assert "bundle_id" in field_names
    assert "bundle" not in field_names
    assert "release_bundle" not in field_names

    # 创建新状态快照推进记录，不得改写所引用的 ReleaseBundle ID。
    uploading = ReleaseActivationRecord(
        schema_version=RELEASE_ACTIVATION_SCHEMA_VERSION,
        activation_id="act-001",
        bundle_id=bundle_id,
        target="cdn-main",
        expected_generation=7,
        status=ReleaseActivationStatus.UPLOADING,
        required_objects_digest=required_digest,
        verified_objects_digest=None,
        error=None,
    )
    assert uploading.bundle_id == preparing.bundle_id == bundle_id
    assert uploading.status is ReleaseActivationStatus.UPLOADING

    failed = ReleaseActivationRecord(
        schema_version=RELEASE_ACTIVATION_SCHEMA_VERSION,
        activation_id="act-001",
        bundle_id=bundle_id,
        target="cdn-main",
        expected_generation=7,
        status=ReleaseActivationStatus.FAILED,
        required_objects_digest=required_digest,
        verified_objects_digest=None,
        error="upload interrupted",
    )
    assert failed.bundle_id == bundle_id
    assert failed.status is ReleaseActivationStatus.FAILED
    assert failed.error == "upload interrupted"

    with pytest.raises((AttributeError, TypeError)):
        preparing.status = ReleaseActivationStatus.ACTIVE  # type: ignore[misc]

    with pytest.raises(PublishError):
        ReleaseActivationRecord(
            schema_version=999,
            activation_id="act-bad-schema",
            bundle_id=bundle_id,
            target="cdn-main",
            expected_generation=1,
            status=ReleaseActivationStatus.PREPARING,
            required_objects_digest=required_digest,
            verified_objects_digest=None,
            error=None,
        )

    # VerifiedReleaseBundle 只能经验证器私有令牌创建，公开构造必须失败。
    with pytest.raises(TypeError):
        VerifiedReleaseBundle(
            bundle_id=bundle_id,
            required_objects_digest=required_digest,
            verified_objects_digest=required_digest,
        )


def test_activation_record_allows_only_declared_state_transitions() -> None:
    """验证激活状态迁移白名单、失败必填 error 与验证凭证门禁。

    测试无参数和返回值。断言：

    - 只允许 ``PREPARING→UPLOADING→VERIFYING→ACTIVATING→ACTIVE``，以及声明
      阶段到 ``FAILED``、``ACTIVATING→CONFLICTED``；
    - 终态 ``ACTIVE`` / ``FAILED`` / ``CONFLICTED`` 不能继续推进；
    - 进入 ``FAILED`` 必须提供非空 ``error``；
    - ``VERIFYING→ACTIVATING`` 必须提供与当前 bundle ID、必要对象摘要一致的
      ``VerifiedReleaseBundle``；普通集合不能伪造验证完成；
    - ``advance_activation`` 返回新不可变记录且不改变 ``bundle_id``；
    - 哈希不匹配时 ``verify_release_bundle`` 失败。

    当前一步最小 GREEN 未定义 ``advance_activation`` / ``verify_release_bundle``
    时，测试导入应以 ``ImportError`` 失败。除导入外不产生外部副作用。
    """
    bundle = _make_bundle(transfer_sha=_SHA_B)
    remote_ok = {_SHA_B: _SHA_B}
    verification = verify_release_bundle(bundle, remote_ok)
    assert isinstance(verification, VerifiedReleaseBundle)
    assert verification.bundle_id == bundle.bundle_id
    assert verification.verified_objects_digest == verification.required_objects_digest

    # 远端哈希不一致不得签发验证凭证。
    with pytest.raises(PublishError):
        verify_release_bundle(bundle, {_SHA_B: _SHA_C})

    record = _preparing_record(
        bundle_id=bundle.bundle_id,
        required_objects_digest=verification.required_objects_digest,
    )
    original_bundle_id = record.bundle_id

    uploading = advance_activation(record, ReleaseActivationStatus.UPLOADING)
    assert uploading.status is ReleaseActivationStatus.UPLOADING
    assert uploading.bundle_id == original_bundle_id
    assert uploading is not record
    assert record.status is ReleaseActivationStatus.PREPARING

    verifying = advance_activation(uploading, ReleaseActivationStatus.VERIFYING)
    assert verifying.status is ReleaseActivationStatus.VERIFYING

    # 缺少验证凭证不得进入 ACTIVATING。
    with pytest.raises(PublishError):
        advance_activation(verifying, ReleaseActivationStatus.ACTIVATING)

    # 普通集合不能冒充 VerifiedReleaseBundle。
    with pytest.raises(PublishError):
        advance_activation(
            verifying,
            ReleaseActivationStatus.ACTIVATING,
            verification=frozenset(remote_ok.items()),  # type: ignore[arg-type]
        )

    activating = advance_activation(
        verifying,
        ReleaseActivationStatus.ACTIVATING,
        verification=verification,
    )
    assert activating.status is ReleaseActivationStatus.ACTIVATING
    assert activating.bundle_id == original_bundle_id
    assert activating.verified_objects_digest == verification.verified_objects_digest

    active = advance_activation(activating, ReleaseActivationStatus.ACTIVE)
    assert active.status is ReleaseActivationStatus.ACTIVE
    assert active.bundle_id == original_bundle_id

    # 终态不可再推进。
    with pytest.raises(PublishError):
        advance_activation(active, ReleaseActivationStatus.FAILED, error="x")

    # 声明阶段允许失败，且必须带 error。
    failed_from_upload = advance_activation(
        uploading,
        ReleaseActivationStatus.FAILED,
        error="network timeout",
    )
    assert failed_from_upload.status is ReleaseActivationStatus.FAILED
    assert failed_from_upload.error == "network timeout"
    assert failed_from_upload.bundle_id == original_bundle_id

    with pytest.raises(PublishError):
        advance_activation(uploading, ReleaseActivationStatus.FAILED)

    with pytest.raises(PublishError):
        advance_activation(uploading, ReleaseActivationStatus.FAILED, error="")

    # ACTIVATING 可进入 CONFLICTED。
    conflicted = advance_activation(
        activating, ReleaseActivationStatus.CONFLICTED, error="generation mismatch"
    )
    assert conflicted.status is ReleaseActivationStatus.CONFLICTED
    assert conflicted.error == "generation mismatch"

    with pytest.raises(PublishError):
        advance_activation(conflicted, ReleaseActivationStatus.ACTIVE)

    # 跳步与非法边拒绝。
    with pytest.raises(PublishError):
        advance_activation(record, ReleaseActivationStatus.VERIFYING)
    with pytest.raises(PublishError):
        advance_activation(record, ReleaseActivationStatus.ACTIVE)
    with pytest.raises(PublishError):
        advance_activation(verifying, ReleaseActivationStatus.ACTIVE)

    # 凭证 bundle_id / digest 与记录不一致时拒绝。
    other_bundle = _make_bundle(transfer_sha=_SHA_C)
    other_verification = verify_release_bundle(other_bundle, {_SHA_C: _SHA_C})
    with pytest.raises(PublishError):
        advance_activation(
            verifying,
            ReleaseActivationStatus.ACTIVATING,
            verification=other_verification,
        )
