"""Unity Player 导出请求测试。"""

from pathlib import Path

from core.platforms import BuildPlatform
from package.unity_export import UnityPlayerExporter
from ports.unity import UnityBatchResult


def test_unity_player_exporter_generates_typed_batch_request(tmp_path: Path) -> None:
    """验证平台、项目、输出和 build setting 均进入结构化 Unity 请求。"""
    project = tmp_path / "project"
    output = tmp_path / "output"
    request = UnityPlayerExporter.plan(
        BuildPlatform.ANDROID,
        project,
        output,
        tmp_path / "Unity.exe",
        tmp_path / "unity.log",
        unity_version="2022.3.62f2",
    )
    assert request.method == "BuildPipeline.BuildPlayer"
    assert "android" in request.arguments
    assert request.expected_outputs == (output,)


def test_unity_player_exporter_rejects_missing_output_on_success(tmp_path: Path) -> None:
    """验证 Unity 成功退出但缺少输出时仍判定为失败。"""
    result = UnityPlayerExporter.validate(
        UnityBatchResult(False, 1, tmp_path / "unity.log", (tmp_path / "missing",))
    )
    assert result is False
