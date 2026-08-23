"""验证 UploadPlan 的严格本地确定性 codec。"""

from __future__ import annotations

import hashlib

import pytest

from core.artifacts import BlobRef
from release.upload_plan import UploadItem, UploadPhase, UploadPlanFactory
from release.upload_plan_codec import read_upload_plan, write_upload_plan


def _item(key: str, content: bytes, phase: UploadPhase) -> UploadItem:
    """构造内容身份一致的上传项。"""
    digest = hashlib.sha256(content).hexdigest()
    return UploadItem(key, BlobRef(f"blobs/{digest}", digest, len(content)), content, phase)


def _plan():
    """构造包含普通对象和版本入口的计划。"""
    return UploadPlanFactory.create(
        "a" * 64,
        (_item("resource/a", b"resource", UploadPhase.RESOURCE),),
        (_item("protocol/a", b"protocol", UploadPhase.PROTOCOL),),
        _item("version/current", b"entry", UploadPhase.VERSION_ENTRY),
        3,
    )


def test_upload_plan_codec_round_trips_and_recomputes_id(tmp_path) -> None:
    """验证写入再读取保留内容、阶段、代际和确定性 plan_id。"""
    path = tmp_path / "upload-plan.json"
    original = _plan()

    write_upload_plan(original, path)

    restored = read_upload_plan(path)
    assert restored == original
    assert path.read_bytes() == path.read_bytes()


def test_upload_plan_codec_rejects_tampered_id(tmp_path) -> None:
    """验证文件中的 plan_id 不能覆盖重算身份。"""
    path = tmp_path / "upload-plan.json"
    write_upload_plan(_plan(), path)
    content = path.read_text(encoding="utf-8").replace("a" * 64, "b" * 64, 1)
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        read_upload_plan(path)
