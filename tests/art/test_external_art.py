"""验证 PSD、场景烘焙、资产检查和报告适配边界。"""

from __future__ import annotations

from pathlib import Path

import pytest

from art.asset_checks import AssetCheckRule, AssetChecker
from art.psd import PsdExportRequest, PsdExporter
from art.report import ArtReport
from art.scene_bake import SceneBakeRequest, SceneBakeTask
from core.errors import ToolExecutionError
from ports.unity import UnityBatchRequest, UnityBatchResult


class _Unity:
    """保存 Unity 请求并返回成功结果的替身。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.requests: list[UnityBatchRequest] = []

    def run(self, request: UnityBatchRequest, cancellation: object = None) -> UnityBatchResult:
        """记录请求并返回成功结果。"""
        self.requests.append(request)
        request.log_path.parent.mkdir(parents=True, exist_ok=True)
        request.log_path.write_text("bake ok", encoding="utf-8")
        return UnityBatchResult(True, 0, request.log_path)


def test_psd_request_is_safe_and_missing_optional_tool_is_explicit(tmp_path: Path) -> None:
    """PSD 请求只允许绝对输入和逻辑输出，缺少 psd-tools 时明确失败。"""
    source = tmp_path / "source.psd"
    source.write_bytes(b"not-a-psd")
    request = PsdExportRequest(source, tmp_path / "output", (("Layer", "ui/layer.png"),))
    assert request.layer_mapping[0][1] == "ui/layer.png"
    with pytest.raises(ToolExecutionError):
        PsdExporter().export(request)


def test_scene_bake_uses_fixed_unity_arguments_and_preserves_source(tmp_path: Path) -> None:
    """场景烘焙只经 Unity 端口执行，并验证源快照前后一致。"""
    source = tmp_path / "source"
    source.mkdir()
    scene = source / "Scenes" / "main.unity"
    scene.parent.mkdir()
    scene.write_text("scene", encoding="utf-8")
    request = SceneBakeRequest(
        executable=tmp_path / "Unity.exe",
        project_path=tmp_path / "project",
        source_root=source,
        output_root=tmp_path / "output",
        scenes=("Scenes/main.unity",),
        method="Spacetime.Art.Bake",
        log_path=tmp_path / "logs/bake.log",
    )
    unity = _Unity()
    result = SceneBakeTask(unity).build(request)

    assert result.success
    assert "--scene" in unity.requests[0].arguments
    assert "Scenes/main.unity" in unity.requests[0].arguments
    assert result.source_digest_before == result.source_digest_after


def test_asset_checker_returns_deterministic_diagnostics_and_fix_plan(tmp_path: Path) -> None:
    """资产检查对空文件给出错误，且结果不修改源目录。"""
    root = tmp_path / "assets"
    root.mkdir()
    (root / "broken.fbx").write_bytes(b"")
    result = AssetChecker().check(root, AssetCheckRule("1"))

    assert result.diagnostics[0].code == "EMPTY_ASSET"
    assert result.fix_plan.actions == ()
    assert (root / "broken.fbx").read_bytes() == b""


def test_report_is_canonical_and_contains_tool_versions() -> None:
    """同一请求和诊断应生成相同规范报告字节。"""
    report = ArtReport("task", "snapshot", "rules", (("Pillow", "missing"),), ())
    assert report.to_json_bytes() == report.to_json_bytes()
    assert b'"tool_versions"' in report.to_json_bytes()
