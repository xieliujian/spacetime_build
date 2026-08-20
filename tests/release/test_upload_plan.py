"""UploadPlan 的确定性顺序与对象身份测试。"""

import hashlib

import pytest

from core.artifacts import BlobRef
from release.upload_plan import (
    UploadItem,
    UploadPhase,
    UploadPlanFactory,
)


def _item(key: str, content: bytes, phase: UploadPhase) -> UploadItem:
    """构造测试上传对象。"""
    digest = hashlib.sha256(content).hexdigest()
    return UploadItem(key, BlobRef(f"blobs/{digest}", digest, len(content)), content, phase)


def test_upload_plan_has_stable_resource_protocol_version_order() -> None:
    """验证输入排列不影响资源、协议、版本入口的固定阶段顺序。"""
    version = _item("version/windows/current", b"version", UploadPhase.VERSION_ENTRY)
    plan = UploadPlanFactory.create(
        bundle_id="a" * 64,
        resource_objects=(
            _item("z.bin", b"z", UploadPhase.RESOURCE),
            _item("a.bin", b"a", UploadPhase.RESOURCE),
        ),
        protocol_objects=(_item("file_list.txt", b"list", UploadPhase.PROTOCOL),),
        version_entry=version,
        expected_generation=4,
    )
    assert tuple(item.key for item in plan.objects) == ("a.bin", "z.bin", "file_list.txt")
    assert plan.version_entry.key == "version/windows/current"
    assert (
        plan.plan_id
        == UploadPlanFactory.create(
            bundle_id="a" * 64,
            resource_objects=tuple(reversed(plan.objects[:2])),
            protocol_objects=plan.objects[2:],
            version_entry=version,
            expected_generation=4,
        ).plan_id
    )


def test_upload_plan_rejects_duplicate_or_cross_phase_keys() -> None:
    """验证对象键不能重复，版本入口不能同时作为普通对象。"""
    duplicate = _item("same", b"same", UploadPhase.RESOURCE)
    with pytest.raises(ValueError):
        UploadPlanFactory.create(
            bundle_id="a" * 64,
            resource_objects=(duplicate,),
            protocol_objects=(_item("same", b"same", UploadPhase.PROTOCOL),),
            version_entry=_item("version", b"v", UploadPhase.VERSION_ENTRY),
            expected_generation=0,
        )
