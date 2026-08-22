"""旧系统历史产物隔离双跑与旧客户端 Parser 验收。

本模块不直接调用参考工作区、SVN 写入口或未知旧命令。调用方必须注入旧系统 runner，
并提供位于同一隔离根内的固定源码快照、历史产物目录和新产物目录；服务读取两边文件
生成摘要，使用现有严格 Parser 验收 AssetBundle 数据库和六字段文件列表，然后给出
确定性路径差异。缺少历史目录或旧 runner 时返回 ``PENDING``，不把 Python Golden 当成
真实历史验收。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from compatibility.assetbundle_parser import LegacyAssetBundleDbParser
from compatibility.file_list_parser import LegacyFileListParser
from compatibility.line_endings import LineEnding
from core.errors import CompatibilityError


class LegacyAcceptanceStatus(str, Enum):
    """历史双跑验收状态。"""

    PASSED = "passed"
    PENDING = "pending"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LegacyCapturedFile:
    """一份历史或新产物的相对路径摘要。"""

    logical_path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class LegacyCapture:
    """一个隔离产物根目录的确定性文件摘要集合。"""

    root: Path
    files: tuple[LegacyCapturedFile, ...]
    tree_sha256: str


class LegacyBuildRunner(Protocol):
    """旧系统双跑所需的最小执行端口。"""

    def run(self, source_snapshot: Path, output_root: Path) -> None:
        """在隔离输出根生成一次旧系统产物。"""
        ...


@dataclass(frozen=True, slots=True)
class LegacyDualRunResult:
    """双跑差异和 Parser 验收结果。"""

    status: LegacyAcceptanceStatus
    baseline: LegacyCapture | None
    candidate: LegacyCapture | None
    added_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]
    changed_paths: tuple[str, ...]
    parser_errors: tuple[str, ...]
    summary: str


class LegacyDualRunService:
    """在隔离目录内执行旧系统双跑并验收协议文件。"""

    def run(
        self,
        *,
        isolation_root: Path,
        source_snapshot: Path,
        historical_output: Path,
        candidate_output: Path,
        runner: LegacyBuildRunner | None,
        line_ending: LineEnding,
    ) -> LegacyDualRunResult:
        """读取历史结果、执行候选结果、比较摘要并运行两边 Parser。"""
        try:
            self._validate_isolation(
                isolation_root,
                source_snapshot,
                historical_output,
                candidate_output,
            )
            if not historical_output.is_dir():
                return self._pending("历史产物目录不存在，等待输入快照")
            if runner is None:
                return self._pending("未提供旧系统 runner，无法执行真实双跑")
            if candidate_output.exists():
                raise ValueError("candidate_output 必须是尚不存在的隔离输出目录")
            candidate_output.mkdir(parents=True)
            runner.run(source_snapshot, candidate_output)
            baseline = capture_legacy_directory(historical_output)
            candidate = capture_legacy_directory(candidate_output)
            parser_errors = _parser_errors(historical_output, line_ending)
            parser_errors += _parser_errors(candidate_output, line_ending)
            added, removed, changed = _diff_captures(baseline, candidate)
            if parser_errors or added or removed or changed:
                return LegacyDualRunResult(
                    LegacyAcceptanceStatus.FAILED,
                    baseline,
                    candidate,
                    added,
                    removed,
                    changed,
                    parser_errors,
                    "历史双跑或旧客户端 Parser 验收失败",
                )
            return LegacyDualRunResult(
                LegacyAcceptanceStatus.PASSED,
                baseline,
                candidate,
                (),
                (),
                (),
                (),
                "历史双跑摘要一致且旧客户端 Parser 全部通过",
            )
        except Exception as exc:
            return LegacyDualRunResult(
                LegacyAcceptanceStatus.FAILED,
                None,
                None,
                (),
                (),
                (),
                (str(exc),),
                "历史双跑入口失败",
            )

    @staticmethod
    def _validate_isolation(
        isolation_root: Path,
        source_snapshot: Path,
        historical_output: Path,
        candidate_output: Path,
    ) -> None:
        """确认源码、历史和候选目录均在隔离根内且互不重叠。"""
        paths = (isolation_root, source_snapshot, historical_output, candidate_output)
        if any(not isinstance(path, Path) or not path.is_absolute() for path in paths):
            raise ValueError("双跑路径必须全部是绝对 Path")
        root = isolation_root.resolve(strict=False)
        resolved = tuple(path.resolve(strict=False) for path in paths[1:])
        if any(path != root and root not in path.parents for path in resolved):
            raise ValueError("双跑路径必须位于 isolation_root 内")
        if len(set(resolved)) != len(resolved):
            raise ValueError("source、historical 和 candidate 路径不得重复")
        if source_snapshot.resolve(strict=False) == candidate_output.resolve(strict=False):
            raise ValueError("源码快照不得作为候选输出目录")

    @staticmethod
    def _pending(summary: str) -> LegacyDualRunResult:
        """创建不含半成品目录的 PENDING 结果。"""
        return LegacyDualRunResult(
            LegacyAcceptanceStatus.PENDING,
            None,
            None,
            (),
            (),
            (),
            (),
            summary,
        )


def capture_legacy_directory(root: Path) -> LegacyCapture:
    """读取隔离目录下所有普通文件并计算稳定树摘要。"""
    if not isinstance(root, Path) or not root.is_absolute() or not root.is_dir():
        raise ValueError("root 必须是存在的绝对目录")
    files: list[LegacyCapturedFile] = []
    for path in sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix().encode("utf-8"),
    ):
        if path.is_symlink():
            raise ValueError(f"历史产物不得包含符号链接: {path}")
        if not path.is_file():
            continue
        logical_path = path.relative_to(root).as_posix()
        content = path.read_bytes()
        files.append(
            LegacyCapturedFile(logical_path, hashlib.sha256(content).hexdigest(), len(content))
        )
    tree = hashlib.sha256()
    for item in files:
        encoded = item.logical_path.encode("utf-8")
        tree.update(len(encoded).to_bytes(8, "big"))
        tree.update(encoded)
        tree.update(bytes.fromhex(item.sha256))
        tree.update(item.size.to_bytes(8, "big"))
    return LegacyCapture(root, tuple(files), tree.hexdigest())


def _parser_errors(root: Path, line_ending: LineEnding) -> tuple[str, ...]:
    """解析根目录中所有已知旧客户端协议文件并收集稳定错误摘要。"""
    errors: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        logical_path = path.relative_to(root).as_posix()
        try:
            content = path.read_bytes()
            name = path.name
            if name.startswith("assetbundledb_") and name.endswith(".txt"):
                LegacyAssetBundleDbParser(line_ending).parse(content)
            elif name.startswith("file_list_") and name.endswith(".txt"):
                match = re.fullmatch(r"file_list_([1-9][0-9]*)\.txt", name)
                if match is None:
                    raise CompatibilityError("file_list 文件名缺少合法版本号")
                LegacyFileListParser(line_ending).parse(
                    content, expected_list_version=int(match.group(1))
                )
        except Exception as exc:
            errors.append(f"{logical_path}: {exc}")
    return tuple(errors)


def _diff_captures(
    baseline: LegacyCapture,
    candidate: LegacyCapture,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """按相对路径和摘要生成 added/removed/changed 差异。"""
    baseline_by_path = {item.logical_path: item for item in baseline.files}
    candidate_by_path = {item.logical_path: item for item in candidate.files}
    added = tuple(sorted(candidate_by_path.keys() - baseline_by_path.keys()))
    removed = tuple(sorted(baseline_by_path.keys() - candidate_by_path.keys()))
    changed = tuple(
        sorted(
            path
            for path in baseline_by_path.keys() & candidate_by_path.keys()
            if baseline_by_path[path] != candidate_by_path[path]
        )
    )
    return added, removed, changed


__all__ = [
    "LegacyAcceptanceStatus",
    "LegacyBuildRunner",
    "LegacyCapture",
    "LegacyCapturedFile",
    "LegacyDualRunResult",
    "LegacyDualRunService",
    "capture_legacy_directory",
]
