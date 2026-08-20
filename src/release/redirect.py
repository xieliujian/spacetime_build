"""Redirect 兼容策略的纯规划模型。

本模块只读取已经验证的 ``ReleaseSnapshot`` 元数据，按版本化策略确定哪些
AssetBundle 进入哪个桶，并计算稳定的容器路径、偏移和长度。它不读取 Blob 字节，
也不修改快照；容器内容由 ``redirect_container`` 在另一层按本计划构建。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from core.artifacts import BlobRef
from release.snapshots import ReleaseArtifactClass, ReleaseSnapshot, ReleaseSnapshotEntry

REDIRECT_STRATEGY_VERSION = "redirect-v1"


@dataclass(frozen=True, slots=True)
class RedirectSlicePlan:
    """描述一个原始 AssetBundle 在 Redirect 容器中的切片位置。"""

    logical_path: str
    source_blob: BlobRef
    offset: int
    length: int
    container_logical_path: str = ""

    def __post_init__(self) -> None:
        """校验切片引用和边界，确保构建器可按计划读取完整 Blob。"""
        if not isinstance(self.logical_path, str) or not self.logical_path:
            raise ValueError("logical_path 必须是非空字符串")
        if not isinstance(self.source_blob, BlobRef):
            raise TypeError("source_blob 必须是 BlobRef")
        if not isinstance(self.offset, int) or isinstance(self.offset, bool) or self.offset < 0:
            raise ValueError("offset 必须是非负整数")
        if not isinstance(self.length, int) or isinstance(self.length, bool) or self.length <= 0:
            raise ValueError("length 必须是正整数")
        if self.length != self.source_blob.size:
            raise ValueError("Redirect slice length 必须等于源 Blob 大小")
        if self.container_logical_path and not isinstance(self.container_logical_path, str):
            raise TypeError("container_logical_path 必须是字符串")


@dataclass(frozen=True, slots=True)
class RedirectContainerPlan:
    """描述一个容器及其按偏移顺序排列的切片。"""

    container_logical_path: str
    slices: tuple[RedirectSlicePlan, ...]

    def __post_init__(self) -> None:
        """校验桶内切片连续、无重叠且路径唯一。"""
        if not isinstance(self.container_logical_path, str) or not self.container_logical_path:
            raise ValueError("container_logical_path 必须是非空字符串")
        if not isinstance(self.slices, tuple):
            raise TypeError("slices 必须是 tuple")
        expected_offset = 0
        paths: set[str] = set()
        for item in self.slices:
            if not isinstance(item, RedirectSlicePlan):
                raise TypeError("slices 的每一项必须是 RedirectSlicePlan")
            if item.offset != expected_offset:
                raise ValueError("Redirect 容器切片 offset 必须连续")
            if (
                item.container_logical_path
                and item.container_logical_path != self.container_logical_path
            ):
                raise ValueError("Redirect slice 引用的容器路径不一致")
            if item.logical_path in paths:
                raise ValueError("Redirect 容器内逻辑路径不得重复")
            paths.add(item.logical_path)
            expected_offset += item.length


@dataclass(frozen=True, slots=True)
class RedirectPlan:
    """一个版本化 Redirect 规划的不可变结果。"""

    strategy_version: str
    slices: tuple[RedirectSlicePlan, ...]
    containers: tuple[RedirectContainerPlan, ...]

    def __post_init__(self) -> None:
        """校验策略版本、切片容器映射和确定性集合结构。"""
        if self.strategy_version != REDIRECT_STRATEGY_VERSION:
            raise ValueError("不支持的 Redirect 策略版本")
        container_paths = {item.container_logical_path for item in self.containers}
        if len(container_paths) != len(self.containers):
            raise ValueError("Redirect 容器路径不得重复")
        for item in self.slices:
            if not isinstance(item, RedirectSlicePlan):
                raise TypeError("slices 的值必须是 RedirectSlicePlan")
            if item.container_logical_path not in container_paths:
                raise ValueError("Redirect slice 必须引用已规划容器")


@dataclass(frozen=True, slots=True)
class _RedirectPolicy:
    """单类资源的 Redirect 固定策略。"""

    resource_type: str
    threshold: int
    normal_prefix: str
    normal_buckets: int
    base_buckets: int
    baseback_buckets: int | None
    exclusions: tuple[str, ...]


_POLICIES = (
    _RedirectPolicy(
        "scene",
        100 * 1024,
        "scene/redirect/",
        31,
        61,
        41,
        ("-stream.assetbundle", "_dependdb.assetbundle"),
    ),
    _RedirectPolicy("story", 200 * 1024, "story/redirect/", 7, 7, None, ()),
    _RedirectPolicy(
        "ui",
        200 * 1024,
        "ui/redirect/",
        51,
        11,
        None,
        ("ui/extend.assetbundle", "ui/ui_prepare_data.assetbundle"),
    ),
    _RedirectPolicy(
        "texture",
        200 * 1024,
        "texture/redirect/",
        51,
        7,
        None,
        ("texture/icon/icondatabase.assetbundle",),
    ),
    _RedirectPolicy(
        "character",
        200 * 1024,
        "character/redirect/",
        51,
        17,
        None,
        (
            "character/characterdatabase.assetbundle",
            "character/character_gpuskin_dependdb.assetbundle",
            "character/character_expression.assetbundle",
        ),
    ),
    _RedirectPolicy(
        "particle",
        200 * 1024,
        "particle/redirect/",
        51,
        17,
        None,
        ("particle/particle_dependdb.assetbundle",),
    ),
)


class RedirectPlanner:
    """按固定兼容策略为快照生成 Redirect 切片计划。"""

    @staticmethod
    def plan(snapshot: ReleaseSnapshot) -> RedirectPlan:
        """计算稳定的 Redirect 容器、切片偏移和长度。

        参数：
            snapshot: 已通过领域交叉引用校验的单变体发布快照。

        返回：
            只包含可 Redirect AssetBundle 的 ``RedirectPlan``；计划不含容器字节。

        异常：
            输入类型错误或快照路径无法匹配已知策略时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            按 UTF-8 路径排序和 SHA1 取模，纯内存执行，不读 Blob、不写对象存储。
        """
        if not isinstance(snapshot, ReleaseSnapshot):
            raise TypeError("snapshot 必须是 ReleaseSnapshot")
        groups: dict[tuple[str, int], list[ReleaseSnapshotEntry]] = {}
        for snapshot_entry in snapshot.entries:
            if snapshot_entry.artifact_class is not ReleaseArtifactClass.ASSET_BUNDLE:
                continue
            entry = snapshot_entry.release_entry
            policy = _policy_for_path(entry.logical_path)
            if policy is None or not entry.logical_path.endswith(".assetbundle"):
                continue
            if any(entry.logical_path.endswith(suffix) for suffix in policy.exclusions):
                continue
            if entry.original_size < policy.threshold:
                continue
            prefix, bucket_count = _bucket_config(policy, entry.logical_path)
            bucket = (
                int.from_bytes(hashlib.sha1(entry.logical_path.encode("utf-8")).digest(), "big")
                % bucket_count
            )
            groups.setdefault((prefix, bucket), []).append(snapshot_entry)

        plans: list[RedirectContainerPlan] = []
        slice_refs: list[RedirectSlicePlan] = []
        for prefix, bucket in sorted(
            groups, key=lambda value: (value[0].encode("utf-8"), value[1])
        ):
            ordered = sorted(
                groups[(prefix, bucket)],
                key=lambda value: value.release_entry.logical_path.encode("utf-8"),
            )
            container_path = f"{prefix}{bucket}.assetbundle"
            offset = 0
            container_slices: list[RedirectSlicePlan] = []
            for raw in ordered:
                entry = raw.release_entry
                item = RedirectSlicePlan(
                    entry.logical_path,
                    entry.source_blob,
                    offset,
                    entry.original_size,
                    container_path,
                )
                container_slices.append(item)
                slice_refs.append(item)
                offset += item.length
            plans.append(RedirectContainerPlan(container_path, tuple(container_slices)))
        return RedirectPlan(REDIRECT_STRATEGY_VERSION, tuple(slice_refs), tuple(plans))


def _policy_for_path(logical_path: str) -> _RedirectPolicy | None:
    """根据明确的资源目录选择 Redirect 策略。"""
    for policy in _POLICIES:
        if logical_path.startswith(policy.resource_type + "/"):
            return policy
    return None


def _bucket_config(policy: _RedirectPolicy, logical_path: str) -> tuple[str, int]:
    """根据 base/baseback 目录选择策略中的前缀与桶数。"""
    if logical_path.startswith(policy.resource_type + "/baseback/"):
        if policy.baseback_buckets is None:
            return policy.normal_prefix, policy.normal_buckets
        return policy.normal_prefix.replace(
            "redirect/", "redirect_baseback/"
        ), policy.baseback_buckets
    if logical_path.startswith(policy.resource_type + "/base/"):
        return policy.normal_prefix.replace("redirect/", "redirect_base/"), policy.base_buckets
    return policy.normal_prefix, policy.normal_buckets
