"""从已验证只读快照生成确定性的不可变 BranchPlan。

本模块只做内存规划：它把配置映射转换为有依赖顺序的 ``BranchCopy`` 集合，
对已验证源节点上的 ``svn:externals`` 生成 ``PropertyChange``，并通过 codec
共享的规范 payload 计算内容寻址 plan ID。planner 不连接 SVN、不调用 ObjectStore，
也不接受绝对本地路径或 URL 用户信息。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from branch.config import BranchConfig, MappingRule
from branch.externals import ExternalRewriteError, ExternalRewriteRule, rewrite_externals
from branch.model import (
    BranchCopy,
    BranchSource,
    BranchTarget,
    BranchValidationError,
    PropertyChange,
)
from branch.validator import ValidatedBranchSnapshot


class BranchPlanError(BranchValidationError):
    """无法从快照和映射生成安全 BranchPlan 时抛出的异常。"""


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE_PATTERN = re.compile(r"[A-Za-z]:")


def _validate_public_endpoint(value: str, field_name: str) -> str:
    """拒绝映射端点中的凭据、fragment 和路径逃逸。"""
    if not isinstance(value, str) or not value.strip():
        raise BranchPlanError(f"{field_name} 必须是非空字符串")
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        raise BranchPlanError(f"{field_name} 不得包含 URL 用户信息")
    if parsed.fragment:
        raise BranchPlanError(f"{field_name} 不得包含 URL fragment")
    path = parsed.path if parsed.scheme else value
    if any(segment == ".." for segment in path.replace("\\", "/").split("/")):
        raise BranchPlanError(f"{field_name} 不得路径逃逸")
    return value.rstrip("/")


def _prefix_matches(prefix: str, value: str) -> bool:
    """判断 mapping 前缀是否命中完整 URL/path 边界。"""
    return value == prefix or value.startswith(prefix + "/")


def _relative_path(root: str, value: str, fallback: str, field_name: str) -> str:
    """把映射端点转换为 root 下的安全相对路径。"""
    if not _prefix_matches(root, value):
        raise BranchPlanError(f"{field_name} 不在对应 root 下")
    suffix = value[len(root) :].lstrip("/") or fallback.replace(".", "/")
    if (
        not suffix
        or suffix.startswith("/")
        or "\\" in suffix
        or _DRIVE_PATTERN.match(suffix) is not None
        or any(part in {"", ".", ".."} for part in suffix.split("/"))
    ):
        raise BranchPlanError(f"{field_name} 不是安全相对路径")
    return suffix


def _normalized_rules(mapping: BranchConfig | Sequence[MappingRule]) -> tuple[MappingRule, ...]:
    """规范化 BranchConfig 或规则序列，并拒绝非法元素。"""
    if isinstance(mapping, BranchConfig):
        rules = tuple(mapping.mappings)
    else:
        rules = tuple(mapping)
    if not rules or not all(isinstance(rule, MappingRule) for rule in rules):
        raise BranchPlanError("mapping 必须包含至少一条 MappingRule")
    for rule in rules:
        _validate_public_endpoint(rule.source_prefix, f"mapping {rule.name}.source")
        _validate_public_endpoint(rule.target_prefix, f"mapping {rule.name}.target")
    return tuple(
        sorted(
            rules,
            key=lambda item: (
                item.source_prefix.encode("utf-8"),
                item.target_prefix.encode("utf-8"),
                item.name.encode("utf-8"),
            ),
        )
    )


@dataclass(frozen=True, slots=True, repr=False)
class BranchPlan:
    """描述一次固定仓库 revision 的分支复制与属性变更计划。

    参数：
        plan_id: 规范 payload 的 SHA-256；planner 创建时自动计算，codec 读取时重算。
        source_revision: 源 copy 使用的固定 revision。
        expected_repository_revision: 规划时目标仓库的 revision。
        source_root: 源根 URL。
        target_root: 目标根 URL。
        copies: 按父节点优先排序的不可变复制集合。
        property_changes: 按路径排序的 externals 属性变化集合。
        repository_uuid: 源和目标共同的仓库 UUID。
        mapping_version: 映射规则版本标识。
        schema_version: plan payload schema 版本，当前为 1。

    返回：
        无；对象冻结，且不包含执行方法。

    异常：
        字段类型、revision、仓库身份或重复目标不合法时抛 ``BranchPlanError``。

    约束与副作用：
        计划只描述未来 mutation；构造不检查远端状态、不写文件、不保存凭据。
    """

    plan_id: str
    source_revision: int
    expected_repository_revision: int
    source_root: str
    target_root: str
    copies: tuple[BranchCopy, ...]
    property_changes: tuple[PropertyChange, ...]
    repository_uuid: str
    mapping_version: str = "1"
    schema_version: int = 1

    def __post_init__(self) -> None:
        """校验计划的跨字段不变量并规范集合为 tuple。"""
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise BranchPlanError("plan_id 必须是非空字符串")
        for value, field_name in (
            (self.source_revision, "source_revision"),
            (self.expected_repository_revision, "expected_repository_revision"),
        ):
            if type(value) is not int or value <= 0:
                raise BranchPlanError(f"{field_name} 必须是正整数")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise BranchPlanError("schema_version 必须是 1")
        if not isinstance(self.mapping_version, str) or not self.mapping_version.strip():
            raise BranchPlanError("mapping_version 必须是非空字符串")
        _validate_public_endpoint(self.source_root, "source_root")
        _validate_public_endpoint(self.target_root, "target_root")
        if not isinstance(self.repository_uuid, str) or not self.repository_uuid.strip():
            raise BranchPlanError("repository_uuid 必须是非空字符串")
        copies = tuple(self.copies)
        changes = tuple(self.property_changes)
        if not all(isinstance(item, BranchCopy) for item in copies):
            raise BranchPlanError("copies 必须只包含 BranchCopy")
        if not all(isinstance(item, PropertyChange) for item in changes):
            raise BranchPlanError("property_changes 必须只包含 PropertyChange")
        target_paths = [item.target_path for item in copies]
        if len(set(target_paths)) != len(target_paths):
            raise BranchPlanError("复制目标路径冲突")
        for item in copies:
            if item.source.revision != self.source_revision:
                raise BranchPlanError("copy revision 与 source_revision 不一致")
            if item.source.repository_uuid != self.repository_uuid:
                raise BranchPlanError("copy source repository UUID 不一致")
            if item.target.repository_uuid != self.repository_uuid:
                raise BranchPlanError("copy target repository UUID 不一致")
        object.__setattr__(self, "copies", copies)
        object.__setattr__(self, "property_changes", changes)


def plan_payload_without_id(plan: BranchPlan) -> dict[str, object]:
    """返回不含 ``plan_id`` 的规范身份 payload。"""
    return {
        "schema_version": plan.schema_version,
        "mapping_version": plan.mapping_version,
        "repository_uuid": plan.repository_uuid,
        "source_revision": plan.source_revision,
        "expected_repository_revision": plan.expected_repository_revision,
        "source_root": plan.source_root,
        "target_root": plan.target_root,
        "copies": [
            {
                "source": {
                    "url": item.source.url,
                    "repository_uuid": item.source.repository_uuid,
                    "revision": item.source.revision,
                },
                "target": {
                    "url": item.target.url,
                    "repository_uuid": item.target.repository_uuid,
                },
                "source_path": item.source_path,
                "target_path": item.target_path,
            }
            for item in plan.copies
        ],
        "property_changes": [
            {
                "path": item.path,
                "property_name": item.property_name,
                "old_value": item.old_value,
                "new_value": item.new_value,
            }
            for item in plan.property_changes
        ],
    }


def compute_plan_id(plan: BranchPlan) -> str:
    """根据规范 JSON 身份 payload 计算 BranchPlan SHA-256 ID。"""
    import json

    canonical = json.dumps(
        plan_payload_without_id(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _external_value_digest(value: str) -> str:
    """把 externals 原文绑定为不泄露内容的旧值摘要。"""
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


class BranchPlanner:
    """从 validated snapshot、源目标引用和 mapping 生成单个 BranchPlan。"""

    def create_plan(
        self,
        snapshot: ValidatedBranchSnapshot,
        source: BranchSource,
        target: BranchTarget,
        mapping: BranchConfig | Sequence[MappingRule],
    ) -> BranchPlan:
        """生成确定性复制和 externals 属性计划。

        参数：
            snapshot: ``BranchPreconditionValidator`` 返回的成对只读快照。
            source: 与快照完全匹配的固定源引用。
            target: 与快照完全匹配的目标引用。
            mapping: 已规范化 ``BranchConfig`` 或规则序列。

        返回：
            单个不可变 ``BranchPlan``，plan ID 由完整内容自动计算。

        异常：
            快照漂移、规则不在 root、目标重复、external 闭包失败或秘密路径输入
            会抛 ``BranchPlanError``。

        约束与副作用：
            只计算内存对象；不调用 provider、SVN、ObjectStore 或本地文件系统。
        """
        self._validate_inputs(snapshot, source, target)
        rules = _normalized_rules(mapping)
        copies = self._build_copies(source, target, rules)
        property_changes = self._build_property_changes(snapshot, copies, mapping, rules)
        mapping_version = str(mapping.schema_version) if isinstance(mapping, BranchConfig) else "1"
        pending = BranchPlan(
            plan_id="pending",
            source_revision=source.revision,
            expected_repository_revision=snapshot.expected_repository_revision,
            source_root=source.url,
            target_root=target.url,
            copies=copies,
            property_changes=property_changes,
            repository_uuid=source.repository_uuid,
            mapping_version=mapping_version,
        )
        return replace(pending, plan_id=compute_plan_id(pending))

    def plan(
        self,
        snapshot: ValidatedBranchSnapshot,
        source: BranchSource,
        target: BranchTarget,
        mapping: BranchConfig | Sequence[MappingRule],
    ) -> BranchPlan:
        """提供 ``create_plan`` 的简短兼容别名。"""
        return self.create_plan(snapshot, source, target, mapping)

    def _validate_inputs(
        self,
        snapshot: ValidatedBranchSnapshot,
        source: BranchSource,
        target: BranchTarget,
    ) -> None:
        """拒绝 planner 输入中的引用不匹配和 revision 漂移。"""
        if not isinstance(snapshot, ValidatedBranchSnapshot):
            raise BranchPlanError("snapshot 必须是已验证的 ValidatedBranchSnapshot")
        if not isinstance(source, BranchSource) or not isinstance(target, BranchTarget):
            raise BranchPlanError("source/target 类型无效")
        if source.repository_uuid != target.repository_uuid:
            raise BranchPlanError("source 和 target 必须属于同一仓库")
        if source.repository_uuid != snapshot.repository_uuid:
            raise BranchPlanError("snapshot repository UUID 漂移")
        if source.url != snapshot.source.node.url or target.url != snapshot.target.node.url:
            raise BranchPlanError("snapshot URL 漂移")
        if source.revision != snapshot.source_revision:
            raise BranchPlanError("source revision 漂移")
        if snapshot.source.node.node_type.value != "directory":
            raise BranchPlanError("源节点必须是目录")
        if snapshot.target.node.exists:
            raise BranchPlanError("目标节点已存在")

    def _build_copies(
        self,
        source: BranchSource,
        target: BranchTarget,
        rules: tuple[MappingRule, ...],
    ) -> tuple[BranchCopy, ...]:
        """按父节点优先顺序从规则构造并检查 BranchCopy 集合。"""
        copies: list[BranchCopy] = []
        for rule in rules:
            source_path = _relative_path(
                source.url, rule.source_prefix, rule.name, "source mapping"
            )
            target_path = _relative_path(
                target.url, rule.target_prefix, rule.name, "target mapping"
            )
            copies.append(
                BranchCopy(
                    source=source,
                    target=target,
                    source_path=source_path,
                    target_path=target_path,
                )
            )
        copies.sort(
            key=lambda item: (
                item.source_path.count("/"),
                item.source_path.encode("utf-8"),
                item.target_path.encode("utf-8"),
            )
        )
        if len({item.target_path for item in copies}) != len(copies):
            raise BranchPlanError("复制目标路径冲突")
        return tuple(copies)

    def _build_property_changes(
        self,
        snapshot: ValidatedBranchSnapshot,
        copies: tuple[BranchCopy, ...],
        mapping: BranchConfig | Sequence[MappingRule],
        rules: tuple[MappingRule, ...],
    ) -> tuple[PropertyChange, ...]:
        """重写源 externals 并将变化映射到对应目标路径。"""
        if isinstance(mapping, BranchConfig):
            allowlist = mapping.allowlist
            unmatched_policy = mapping.unmatched_policy
        else:
            allowlist = ()
            unmatched_policy = "preserve"
        changes: list[PropertyChange] = []
        external_rules = tuple(
            ExternalRewriteRule(rule.name, rule.source_prefix, rule.target_prefix) for rule in rules
        )
        for summary in snapshot.source.external_properties:
            target_path = self._target_property_path(summary.path, copies)
            try:
                result = rewrite_externals(
                    summary.value,
                    external_rules,
                    allowed_repositories=allowlist,
                    unmatched_policy=unmatched_policy,
                )
            except ExternalRewriteError as exc:
                raise BranchPlanError(f"externals 重写失败: {summary.path}") from exc
            if result.rendered != summary.value:
                changes.append(
                    PropertyChange(
                        path=target_path,
                        property_name="svn:externals",
                        old_value=_external_value_digest(summary.value),
                        new_value=result.rendered,
                    )
                )
        return tuple(
            sorted(
                changes,
                key=lambda item: (item.path.encode("utf-8"), item.property_name.encode("utf-8")),
            )
        )

    def _target_property_path(self, source_path: str, copies: tuple[BranchCopy, ...]) -> str:
        """把 source externals 路径映射到最具体的目标 copy 路径。"""
        candidates = [
            item
            for item in copies
            if source_path == item.source_path or source_path.startswith(item.source_path + "/")
        ]
        if not candidates:
            raise BranchPlanError(f"externals 路径未被 mapping 覆盖: {source_path}")
        selected = max(candidates, key=lambda item: len(item.source_path.encode("utf-8")))
        suffix = source_path[len(selected.source_path) :].lstrip("/")
        return selected.target_path if not suffix else f"{selected.target_path}/{suffix}"


__all__ = [
    "BranchPlan",
    "BranchPlanError",
    "BranchPlanner",
    "compute_plan_id",
    "plan_payload_without_id",
]
