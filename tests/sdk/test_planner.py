"""SDK hook 依赖排序、冲突和确定性计划测试。"""

import pytest

from core.artifacts import BlobRef
from core.platforms import BuildPlatform
from sdk.model import SdkDescriptor, SdkOperation, SdkOperationKind, SdkStage
from sdk.planner import SdkHookPlanner


def _descriptor(
    sdk_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    inputs: tuple[BlobRef, ...] = (),
    secret_refs: tuple[str, ...] = (),
    target: str,
    conflict_key: str,
) -> SdkDescriptor:
    """构造无 payload 的 SDK descriptor。"""
    return SdkDescriptor(
        sdk_id,
        "1.0.0",
        BuildPlatform.ANDROID,
        SdkStage.PRE_BUILD,
        inputs,
        (target,),
        (SdkOperation(SdkOperationKind.SET_PROPERTY, target, sdk_id, conflict_key),),
        secret_refs,
        (),
        depends_on,
    )


def test_planner_topologically_sorts_and_is_deterministic() -> None:
    """Given 无序 descriptor，When plan，Then 依赖先执行且 plan id 稳定。"""
    dependency = _descriptor("base", target="manifest/base", conflict_key="base")
    child = _descriptor(
        "child",
        depends_on=("base",),
        target="manifest/child",
        conflict_key="child",
    )

    first = SdkHookPlanner.plan((child, dependency))
    second = SdkHookPlanner.plan((dependency, child))

    assert tuple(item.sdk_id for item in first.descriptors) == ("base", "child")
    assert first.plan_id == second.plan_id


def test_planner_identity_binds_blob_metadata() -> None:
    """验证 payload Blob 的定位、摘要和大小变化会改变计划 ID。"""
    first_blob = BlobRef("blobs/" + "a" * 64, "a" * 64, 4)
    second_blob = BlobRef("blobs/" + "b" * 64, "b" * 64, 4)

    first = SdkHookPlanner.plan(
        (_descriptor("sdk", inputs=(first_blob,), target="manifest/sdk", conflict_key="sdk"),)
    )
    second = SdkHookPlanner.plan(
        (_descriptor("sdk", inputs=(second_blob,), target="manifest/sdk", conflict_key="sdk"),)
    )

    assert first.plan_id != second.plan_id


def test_planner_identity_binds_secret_references() -> None:
    """验证 SecretRef 引用名变化会改变计划 ID，但不需要读取秘密值。"""
    first = SdkHookPlanner.plan(
        (
            _descriptor(
                "sdk",
                secret_refs=("secret://sdk/first",),
                target="manifest/sdk",
                conflict_key="sdk",
            ),
        )
    )
    second = SdkHookPlanner.plan(
        (
            _descriptor(
                "sdk",
                secret_refs=("secret://sdk/second",),
                target="manifest/sdk",
                conflict_key="sdk",
            ),
        )
    )

    assert first.plan_id != second.plan_id


def test_planner_rejects_cycle_and_exclusive_conflicts() -> None:
    """验证依赖环和独占 conflict key/target 冲突在规划阶段失败。"""
    with pytest.raises(ValueError, match="循环"):
        SdkHookPlanner.plan(
            (
                _descriptor("a", depends_on=("b",), target="manifest/a", conflict_key="a"),
                _descriptor("b", depends_on=("a",), target="manifest/b", conflict_key="b"),
            )
        )
    with pytest.raises(ValueError, match="冲突"):
        SdkHookPlanner.plan(
            (
                _descriptor("a", target="manifest/shared", conflict_key="shared"),
                _descriptor("b", target="manifest/other", conflict_key="shared"),
            )
        )
