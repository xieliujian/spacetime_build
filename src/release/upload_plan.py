"""发布上传计划的确定性领域模型。

本模块只把已准备好的不可变 Blob、兼容协议 Blob 和版本入口替换内容排列成
``UploadPlan``，不调用对象存储，也不执行 CAS。普通对象按资源、协议两个阶段稳定
排序；版本入口单独保留给 CAS 激活器，避免把可变入口当成普通不可变对象上传。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from core.artifacts import BlobRef
from core.manifest_codec import canonical_json_bytes
from ports.storage import validate_object_key


class UploadPhase(Enum):
    """上传对象的固定阶段。"""

    RESOURCE = "resource"
    PROTOCOL = "protocol"
    VERSION_ENTRY = "version_entry"


@dataclass(frozen=True, slots=True)
class UploadItem:
    """一个待上传或待 CAS 替换的不可变对象。

    职责：
        绑定安全对象键、持久 Blob 身份、待写入字节和上传阶段，供计划、上传和
        激活服务共享；content 不进入 ``BlobRef`` 之外的运行状态。

    参数：
        key: 使用 ``/`` 的相对对象键。
        blob: 内容寻址 Blob 引用。
        content: 必须与 Blob 的 SHA256/size 完全一致的字节。
        phase: 资源、协议或版本入口阶段。

    返回：
        无；不可变上传对象。

    异常：
        键、Blob、阶段或内容身份不一致时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        仅做内存校验，不写对象存储；版本入口 content 仍只能由 CAS 使用。
    """

    key: str
    blob: BlobRef
    content: bytes
    phase: UploadPhase

    def __post_init__(self) -> None:
        """校验对象键、阶段及内容哈希/大小。

        参数：
            无；读取实例字段。

        返回：
            ``None``，表示上传项身份一致。

        异常：
            字段类型或内容摘要不一致时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            仅内存校验；不执行上传或 CAS。
        """
        validate_object_key(self.key)
        if not isinstance(self.blob, BlobRef):
            raise TypeError("blob 必须是 BlobRef")
        if not isinstance(self.content, bytes):
            raise TypeError("content 必须是 bytes")
        if not isinstance(self.phase, UploadPhase):
            raise TypeError("phase 必须是 UploadPhase")
        if hashlib.sha256(self.content).hexdigest() != self.blob.sha256:
            raise ValueError("UploadItem content 与 BlobRef.sha256 不一致")
        if len(self.content) != self.blob.size:
            raise ValueError("UploadItem content 与 BlobRef.size 不一致")


@dataclass(frozen=True, slots=True)
class UploadPlan:
    """绑定发布对象集合、版本入口和确定性 plan_id 的计划。

    参数：
        plan_id: 由 bundle、对象和入口身份计算的 SHA256。
        bundle_id: 目标 ReleaseBundle ID。
        objects: 不含版本入口的普通不可变对象，按阶段/键排序。
        version_entry: 由激活器 CAS 替换的版本入口。
        expected_generation: 入口 CAS 期望代际。

    返回：
        无；不可变计划。

    异常：
        只由工厂创建，公开直接构造不额外伪造身份；字段由工厂保证。

    约束与副作用：
        不调用 ObjectStore，不更新远端入口。
    """

    plan_id: str
    bundle_id: str
    objects: tuple[UploadItem, ...]
    version_entry: UploadItem
    expected_generation: int


class UploadPlanFactory:
    """创建资源、协议和入口阶段顺序固定的上传计划。"""

    @staticmethod
    def create(
        bundle_id: str,
        resource_objects: tuple[UploadItem, ...],
        protocol_objects: tuple[UploadItem, ...],
        version_entry: UploadItem,
        expected_generation: int,
    ) -> UploadPlan:
        """校验对象集合并计算确定性 ``plan_id``。

        参数：
            bundle_id: 目标 ReleaseBundle SHA256。
            resource_objects: 资源阶段上传对象。
            protocol_objects: compatibility 阶段上传对象。
            version_entry: 最终 CAS 入口对象。
            expected_generation: 版本入口预期旧代际。

        返回：
            普通对象按 phase/key 排序、入口单独保留的 ``UploadPlan``。

        异常：
            重复键、阶段错误、入口交叉重复或代际非法时抛出 ``ValueError``。

        约束与副作用：
            纯内存计算；不调用 ObjectStore、不上传、不激活。
        """
        _validate_sha(bundle_id, "bundle_id")
        if not isinstance(expected_generation, int) or isinstance(expected_generation, bool):
            raise ValueError("expected_generation 必须是 int")
        if expected_generation < 0:
            raise ValueError("expected_generation 必须是非负整数")
        if not isinstance(version_entry, UploadItem):
            raise TypeError("version_entry 必须是 UploadItem")
        if version_entry.phase is not UploadPhase.VERSION_ENTRY:
            raise ValueError("version_entry.phase 必须是 VERSION_ENTRY")
        resources = _validate_items(resource_objects, UploadPhase.RESOURCE, "resource_objects")
        protocols = _validate_items(protocol_objects, UploadPhase.PROTOCOL, "protocol_objects")
        objects = tuple(sorted(resources + protocols, key=_sort_key))
        if len({item.key for item in objects}) != len(objects):
            raise ValueError("资源对象和协议对象之间不得重复对象键")
        keys = {item.key for item in objects}
        if version_entry.key in keys:
            raise ValueError("version_entry key 不得与普通上传对象重复")
        payload = {
            "bundle_id": bundle_id,
            "expected_generation": expected_generation,
            "objects": [_item_dict(item) for item in objects],
            "version_entry": _item_dict(version_entry),
        }
        plan_id = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return UploadPlan(plan_id, bundle_id, objects, version_entry, expected_generation)


def _validate_sha(value: str, field_name: str) -> None:
    """校验内容寻址身份为小写 SHA256。"""
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{field_name} 必须是 64 位小写 SHA256")


def _validate_items(
    items: tuple[UploadItem, ...],
    phase: UploadPhase,
    field_name: str,
) -> tuple[UploadItem, ...]:
    """校验单一阶段对象元组并拒绝重复键。"""
    if not isinstance(items, tuple):
        raise TypeError(f"{field_name} 必须是 tuple[UploadItem, ...]")
    for item in items:
        if not isinstance(item, UploadItem):
            raise TypeError(f"{field_name} 必须全部是 UploadItem")
        if item.phase is not phase:
            raise ValueError(f"{field_name} 含错误 UploadPhase")
    if len({item.key for item in items}) != len(items):
        raise ValueError(f"{field_name} 不得含重复对象键")
    return items


def _sort_key(item: UploadItem) -> tuple[int, bytes]:
    """返回按阶段和 UTF-8 对象键排序的键。"""
    phase_order = {UploadPhase.RESOURCE: 0, UploadPhase.PROTOCOL: 1}
    return phase_order[item.phase], item.key.encode("utf-8")


def _item_dict(item: UploadItem) -> dict[str, object]:
    """将上传项转换为不含原始 content 的身份字典。"""
    return {
        "key": item.key,
        "sha256": item.blob.sha256,
        "size": item.blob.size,
        "phase": item.phase.value,
    }
