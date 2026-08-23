"""验证图集规划、固定工具参数和结构化输出校验。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from art.atlas import AtlasBuilder, AtlasPlanner, AtlasRule
from core.errors import ArtifactValidationError
from ports.process import ProcessOutcome, ProcessRequest, ProcessResult


class _Runner:
    """写入固定图集输出的进程替身。"""

    def __init__(self, *, extra: bool = False) -> None:
        """保存是否生成计划外文件。"""
        self.extra = extra
        self.requests: list[ProcessRequest] = []

    def run(self, request: ProcessRequest, cancellation: object = None) -> ProcessResult:
        """创建 sheet/data 并返回成功结果。"""
        self.requests.append(request)
        request.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        request.stdout_path.write_text("ok", encoding="utf-8")
        request.stderr_path.write_text("", encoding="utf-8")
        output_root = request.working_directory
        (output_root / "ui.png").write_bytes(b"png")
        (output_root / "ui.json").write_text(
            json.dumps(
                {
                    "frames": {
                        "a.png": {
                            "frame": {"x": 0, "y": 0, "w": 1, "h": 1},
                            "rotated": False,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        if self.extra:
            (output_root / "unexpected.bin").write_bytes(b"bad")
        return ProcessResult(
            ProcessOutcome.COMPLETED,
            0,
            0.01,
            request.stdout_path,
            request.stderr_path,
            2,
            0,
        )


def _plan(tmp_path: Path):
    """创建两个 sprite 的图集计划。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "b.png").write_bytes(b"b")
    (source / "a.png").write_bytes(b"a")
    output = tmp_path / "output"
    rule = AtlasRule("ui", "1", 1024, 1024, output_name="ui")
    return AtlasPlanner(tmp_path / "TexturePacker.exe").plan(
        (source / "b.png", source / "a.png"), output, rule
    )


def test_atlas_plan_is_sorted_and_uses_explicit_no_rotation(tmp_path: Path) -> None:
    """计划应固定输入顺序、输出位置和 rotation 选项。"""
    plan = _plan(tmp_path)

    assert plan.input_paths[0].name == "a.png"
    assert "--disable-rotation" in plan.arguments
    assert plan.sheet_path == tmp_path / "output" / "ui.png"


def test_atlas_builder_validates_process_outputs_and_frames(tmp_path: Path) -> None:
    """工具成功时必须存在 sheet/data，且 frame 不能旋转或越界。"""
    runner = _Runner()
    plan = _plan(tmp_path)
    result = AtlasBuilder(runner).build(plan)

    assert result.sheet_path.is_file()
    assert result.frames[0].name == "a.png"
    assert runner.requests[0].arguments == plan.arguments


def test_atlas_builder_rejects_unplanned_files(tmp_path: Path) -> None:
    """计划外文件不能被静默带入后续产物。"""
    runner = _Runner(extra=True)
    with pytest.raises(ArtifactValidationError):
        AtlasBuilder(runner).build(_plan(tmp_path))
