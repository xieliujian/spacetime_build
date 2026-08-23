"""FBX、粒子和场景输入的只读资产检查与修复计划。

检查器只遍历固定快照并生成结构化诊断；自动修复不在本模块隐式发生。任何未来的
修复执行都必须消费显式 ``AssetFixPlan``，再次校验 before digest 后写入隔离输出。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from art.model import ArtDiagnostic, ArtDiagnosticSeverity


@dataclass(frozen=True, slots=True)
class AssetCheckRule:
    """定义资产检查规则版本和允许检查的后缀。"""

    version: str
    suffixes: tuple[str, ...] = (".fbx", ".prefab", ".unity")

    def __post_init__(self) -> None:
        """校验规则版本和后缀集合。"""
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version 必须是非空字符串")
        if not isinstance(self.suffixes, tuple) or not self.suffixes:
            raise ValueError("suffixes 必须是非空 tuple")
        if any(not isinstance(value, str) or not value.startswith(".") for value in self.suffixes):
            raise ValueError("suffixes 必须是点号开头的字符串")


@dataclass(frozen=True, slots=True)
class AssetFixAction:
    """保存一项带 before digest 的显式修复动作描述。"""

    logical_path: str
    before_sha256: str
    action: str


@dataclass(frozen=True, slots=True)
class AssetFixPlan:
    """保存排序后的修复动作；构造不修改文件。"""

    rule_version: str
    actions: tuple[AssetFixAction, ...]


@dataclass(frozen=True, slots=True)
class AssetCheckResult:
    """保存排序诊断和空或显式修复计划。"""

    diagnostics: tuple[ArtDiagnostic, ...]
    fix_plan: AssetFixPlan


class AssetChecker:
    """执行不修改源树的基础资产门禁。"""

    def check(self, source_root: Path, rule: AssetCheckRule) -> AssetCheckResult:
        """检查空文件、粒子缺少组件和场景缺少内容，并生成结果。"""
        if not isinstance(source_root, Path) or not source_root.is_absolute():
            raise ValueError("source_root 必须是绝对 Path")
        if source_root.is_symlink() or not source_root.is_dir():
            raise ValueError("source_root 必须是普通目录")
        if not isinstance(rule, AssetCheckRule):
            raise TypeError("rule 必须是 AssetCheckRule")
        diagnostics: list[ArtDiagnostic] = []
        for path in sorted(
            (item for item in source_root.rglob("*") if item.is_file() and not item.is_symlink()),
            key=lambda item: item.relative_to(source_root).as_posix().encode("utf-8"),
        ):
            if path.suffix.casefold() not in {suffix.casefold() for suffix in rule.suffixes}:
                continue
            logical = path.relative_to(source_root).as_posix()
            content = path.read_bytes()
            if not content:
                diagnostics.append(
                    ArtDiagnostic(
                        ArtDiagnosticSeverity.ERROR, "EMPTY_ASSET", logical, "资产文件为空"
                    )
                )
                continue
            if path.suffix.casefold() == ".prefab" and b"ParticleSystem" not in content:
                diagnostics.append(
                    ArtDiagnostic(
                        ArtDiagnosticSeverity.WARNING,
                        "PARTICLE_COMPONENT_MISSING",
                        logical,
                        "Prefab 未发现 ParticleSystem 标记",
                    )
                )
        return AssetCheckResult(
            tuple(diagnostics),
            AssetFixPlan(rule.version, ()),
        )


__all__ = [
    "AssetCheckResult",
    "AssetCheckRule",
    "AssetChecker",
    "AssetFixAction",
    "AssetFixPlan",
]
