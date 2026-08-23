"""图集输入校验、确定性 TexturePacker 计划和结果验证。

规划器只生成固定参数，不启动 TexturePacker；builder 通过 ``ProcessRunner`` 执行一次
并严格校验 sheet、JSON frame 以及计划外文件。输出目录是调用方提供的隔离工作区，
本模块不覆盖输入 sprite，也不提交 CAS。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from art.image_ops import SpriteFrame
from core.errors import ArtifactValidationError, ToolExecutionError
from ports.process import (
    CancellationToken,
    ProcessOutcome,
    ProcessRequest,
    ProcessResult,
    ProcessRunner,
)


def _safe_segment(value: object, field_name: str) -> str:
    """校验一个不含路径语义的图集名称。"""
    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("/", "\\", "\r", "\n", "."))
    ):
        raise ValueError(f"{field_name} 必须是安全名称")
    return value


def _normal_path(value: object, field_name: str) -> Path:
    """校验绝对普通文件路径。"""
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError(f"{field_name} 必须是绝对 Path")
    if value.is_symlink() or not value.is_file():
        raise FileNotFoundError(f"{field_name} 必须是普通文件: {value}")
    return value


@dataclass(frozen=True, slots=True)
class AtlasRule:
    """定义图集最大尺寸、rotation 规则和输出名称。"""

    name: str
    version: str
    max_width: int
    max_height: int
    allow_rotation: bool = False
    output_name: str = "atlas"
    padding: int = 0

    def __post_init__(self) -> None:
        """校验图集规则的资源上限和显式工具选项。"""
        _safe_segment(self.name, "name")
        _safe_segment(self.version, "version")
        _safe_segment(self.output_name, "output_name")
        for value, field_name in (
            (self.max_width, "max_width"),
            (self.max_height, "max_height"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} 必须是正整数")
        if not isinstance(self.allow_rotation, bool):
            raise TypeError("allow_rotation 必须是 bool")
        if isinstance(self.padding, bool) or not isinstance(self.padding, int) or self.padding < 0:
            raise ValueError("padding 必须是非负整数")


@dataclass(frozen=True, slots=True)
class AtlasPlan:
    """保存一次图集工具执行的全部固定输入和输出。"""

    executable: Path
    input_paths: tuple[Path, ...]
    output_root: Path
    sheet_path: Path
    data_path: Path
    arguments: tuple[str, ...]
    rule: AtlasRule
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        """校验路径、输入排序和输出均位于隔离根。"""
        if not isinstance(self.executable, Path) or not self.executable.is_absolute():
            raise ValueError("executable 必须是绝对 Path")
        if not isinstance(self.output_root, Path) or not self.output_root.is_absolute():
            raise ValueError("output_root 必须是绝对 Path")
        if self.output_root.is_symlink():
            raise ValueError("output_root 不得是符号链接")
        if not isinstance(self.input_paths, tuple) or not self.input_paths:
            raise ValueError("input_paths 必须是非空 tuple")
        paths = tuple(_normal_path(path, "input_path") for path in self.input_paths)
        if paths != tuple(sorted(paths, key=lambda item: item.name.encode("utf-8"))):
            raise ValueError("input_paths 必须按文件名 UTF-8 字节序排序")
        names = [path.name.casefold() for path in paths]
        if len(names) != len(set(names)):
            raise ValueError("sprite 文件名不得大小写冲突")
        for path, field_name in ((self.sheet_path, "sheet_path"), (self.data_path, "data_path")):
            if not isinstance(path, Path) or not path.is_absolute():
                raise ValueError(f"{field_name} 必须是绝对 Path")
            resolved = path.resolve(strict=False)
            root = self.output_root.resolve(strict=False)
            if resolved == root or root not in resolved.parents:
                raise ValueError(f"{field_name} 必须位于 output_root 内")
        if self.sheet_path == self.data_path:
            raise ValueError("sheet_path 与 data_path 不得相同")
        if not isinstance(self.arguments, tuple) or not all(
            isinstance(value, str) for value in self.arguments
        ):
            raise TypeError("arguments 必须是 tuple[str, ...]")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds 必须是有限正数")


@dataclass(frozen=True, slots=True)
class AtlasBuildResult:
    """保存图集工具结果和经过验证的 frame 集合。"""

    sheet_path: Path
    data_path: Path
    frames: tuple[SpriteFrame, ...]
    process_result: ProcessResult


class AtlasPlanner:
    """生成固定 TexturePacker 参数的纯内存规划器。"""

    def __init__(self, executable: Path) -> None:
        """保存 TexturePacker 绝对路径。"""
        if not isinstance(executable, Path) or not executable.is_absolute():
            raise ValueError("executable 必须是绝对 Path")
        self._executable = executable

    def plan(
        self,
        input_paths: tuple[Path, ...],
        output_root: Path,
        rule: AtlasRule,
        *,
        timeout_seconds: float = 300.0,
    ) -> AtlasPlan:
        """校验 sprite 并生成确定性图集命令计划。"""
        if not isinstance(rule, AtlasRule):
            raise TypeError("rule 必须是 AtlasRule")
        if not isinstance(input_paths, tuple):
            raise TypeError("input_paths 必须是 tuple[Path, ...]")
        ordered = tuple(
            sorted(
                (_normal_path(path, "input_path") for path in input_paths),
                key=lambda item: item.name.encode("utf-8"),
            )
        )
        if not ordered:
            raise ValueError("input_paths 不得为空")
        names = [path.name.casefold() for path in ordered]
        if len(names) != len(set(names)):
            raise ValueError("sprite 文件名不得大小写冲突")
        if not isinstance(output_root, Path) or not output_root.is_absolute():
            raise ValueError("output_root 必须是绝对 Path")
        if output_root.exists() and (not output_root.is_dir() or output_root.is_symlink()):
            raise ValueError("output_root 必须是普通目录或待创建路径")
        sheet_path = output_root / f"{rule.output_name}.png"
        data_path = output_root / f"{rule.output_name}.json"
        arguments: list[str] = [
            "--format",
            "json",
            "--sheet",
            str(sheet_path),
            "--data",
            str(data_path),
            "--max-width",
            str(rule.max_width),
            "--max-height",
            str(rule.max_height),
            "--padding",
            str(rule.padding),
            "--enable-rotation" if rule.allow_rotation else "--disable-rotation",
        ]
        arguments.extend(str(path) for path in ordered)
        return AtlasPlan(
            self._executable,
            ordered,
            output_root,
            sheet_path,
            data_path,
            tuple(arguments),
            rule,
            timeout_seconds,
        )


class AtlasBuilder:
    """通过 ProcessRunner 执行图集计划并验证 JSON 产物。"""

    def __init__(self, process_runner: ProcessRunner) -> None:
        """保存外部进程端口。"""
        if not callable(getattr(process_runner, "run", None)):
            raise TypeError("process_runner 必须实现 run")
        self._process_runner = process_runner

    def build(
        self,
        plan: AtlasPlan,
        cancellation: CancellationToken | None = None,
    ) -> AtlasBuildResult:
        """执行一次图集工具并验证输出文件和 frame 数据。"""
        if not isinstance(plan, AtlasPlan):
            raise TypeError("plan 必须是 AtlasPlan")
        plan.output_root.mkdir(parents=True, exist_ok=True)
        log_root = plan.output_root / ".spacetime"
        log_root.mkdir(parents=True, exist_ok=True)
        request = ProcessRequest(
            executable=plan.executable,
            arguments=plan.arguments,
            working_directory=plan.output_root,
            stdout_path=log_root / "atlas.stdout.log",
            stderr_path=log_root / "atlas.stderr.log",
            timeout_seconds=plan.timeout_seconds,
        )
        process_result = self._process_runner.run(request, cancellation)
        if process_result.outcome is not ProcessOutcome.COMPLETED or process_result.exit_code != 0:
            raise ToolExecutionError(
                f"TexturePacker 执行失败: {process_result.diagnostic_message or process_result.outcome.value}"
            )
        for path in (plan.sheet_path, plan.data_path):
            if path.is_symlink() or not path.is_file():
                raise ArtifactValidationError(f"图集缺少普通输出: {path.name}")
        self._reject_unplanned_files(plan)
        frames = self._read_frames(plan)
        return AtlasBuildResult(plan.sheet_path, plan.data_path, frames, process_result)

    def _reject_unplanned_files(self, plan: AtlasPlan) -> None:
        """拒绝输出根中除预期文件以外的顶层普通文件。"""
        expected = {plan.sheet_path.name, plan.data_path.name}
        unexpected = tuple(
            path
            for path in plan.output_root.iterdir()
            if path.is_file() and not path.is_symlink() and path.name not in expected
        )
        if unexpected:
            names = ", ".join(path.name for path in sorted(unexpected, key=lambda item: item.name))
            raise ArtifactValidationError(f"图集存在计划外输出: {names}")

    def _read_frames(self, plan: AtlasPlan) -> tuple[SpriteFrame, ...]:
        """读取并校验 TexturePacker JSON frame。"""
        try:
            payload = json.loads(plan.data_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError("图集 data 不是合法 UTF-8 JSON") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("frames"), Mapping):
            raise ArtifactValidationError("图集 data.frames 必须是 JSON object")
        result: list[SpriteFrame] = []
        for name, raw in sorted(
            payload["frames"].items(), key=lambda item: str(item[0]).encode("utf-8")
        ):
            if not isinstance(name, str) or not isinstance(raw, Mapping):
                raise ArtifactValidationError("图集 frame 结构非法")
            frame = raw.get("frame")
            if not isinstance(frame, Mapping):
                raise ArtifactValidationError(f"图集 frame 缺少 rectangle: {name}")
            values = (frame.get("x"), frame.get("y"), frame.get("w"), frame.get("h"))
            if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
                raise ArtifactValidationError(f"图集 frame 尺寸非法: {name}")
            if bool(raw.get("rotated", False)):
                raise ArtifactValidationError(f"图集 frame 不得旋转: {name}")
            x, y, width, height = values
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                raise ArtifactValidationError(f"图集 frame 越界: {name}")
            if x + width > plan.rule.max_width or y + height > plan.rule.max_height:
                raise ArtifactValidationError(f"图集 frame 超过最大尺寸: {name}")
            result.append(SpriteFrame(name, x, y, width, height))
        if not result:
            raise ArtifactValidationError("图集 data.frames 不得为空")
        return tuple(result)


__all__ = ["AtlasBuildResult", "AtlasBuilder", "AtlasPlan", "AtlasPlanner", "AtlasRule"]
