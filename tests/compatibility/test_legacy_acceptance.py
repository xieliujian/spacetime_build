"""旧系统隔离双跑和 Parser 验收测试。"""

from pathlib import Path

from compatibility.legacy_acceptance import LegacyAcceptanceStatus, LegacyDualRunService
from compatibility.line_endings import LineEnding


_ASSETBUNDLE = b"scene/main.assetbundle\t0\n"
_FILE_LIST = b"scene/main.assetbundle\t1\t6\t" + b"1" * 32 + b"\t1/scene/main.assetbundle\t0\n"


def _write_fixture(root: Path) -> None:
    """写入最小合法历史协议 fixture。"""
    (root / "assetbundledb_scene.txt").write_bytes(_ASSETBUNDLE)
    (root / "file_list_1.txt").write_bytes(_FILE_LIST)


class _Runner:
    """把历史 fixture 复制到候选根的 runner 替身。"""

    def run(self, source_snapshot: Path, output_root: Path) -> None:
        """复制两个固定协议文件，不访问 source 之外的路径。"""
        del source_snapshot
        _write_fixture(output_root)


def test_legacy_dual_run_requires_isolation_and_passes_parser_acceptance(tmp_path: Path) -> None:
    """验证相同历史/候选摘要和旧 Parser 全通过时才返回 PASSED。"""
    isolation = tmp_path / "isolation"
    source = isolation / "source"
    history = isolation / "history"
    candidate = isolation / "candidate"
    source.mkdir(parents=True)
    history.mkdir()
    _write_fixture(history)

    result = LegacyDualRunService().run(
        isolation_root=isolation,
        source_snapshot=source,
        historical_output=history,
        candidate_output=candidate,
        runner=_Runner(),
        line_ending=LineEnding.LF,
    )

    assert result.status is LegacyAcceptanceStatus.PASSED
    assert result.parser_errors == ()
    assert result.added_paths == ()


def test_legacy_dual_run_reports_changed_output_and_parser_error(tmp_path: Path) -> None:
    """验证新输出差异和 malformed 协议不会被摘要隐藏。"""
    isolation = tmp_path / "isolation"
    source = isolation / "source"
    history = isolation / "history"
    candidate = isolation / "candidate"
    source.mkdir(parents=True)
    history.mkdir()
    _write_fixture(history)

    class _BadRunner(_Runner):
        """生成不同 AssetBundle 和非法文件列表的替身。"""

        def run(self, source_snapshot: Path, output_root: Path) -> None:
            """写入差异内容。"""
            del source_snapshot
            (output_root / "assetbundledb_scene.txt").write_bytes(b"scene/changed.assetbundle\t0\n")
            (output_root / "file_list_1.txt").write_bytes(b"malformed")

    result = LegacyDualRunService().run(
        isolation_root=isolation,
        source_snapshot=source,
        historical_output=history,
        candidate_output=candidate,
        runner=_BadRunner(),
        line_ending=LineEnding.LF,
    )

    assert result.status is LegacyAcceptanceStatus.FAILED
    assert "file_list_1.txt" in " ".join(result.parser_errors)
    assert "assetbundledb_scene.txt" in result.changed_paths


def test_legacy_dual_run_is_pending_without_old_runner(tmp_path: Path) -> None:
    """验证没有真实旧系统 runner 时明确保持 PENDING。"""
    isolation = tmp_path / "isolation"
    source = isolation / "source"
    history = isolation / "history"
    candidate = isolation / "candidate"
    source.mkdir(parents=True)
    history.mkdir()
    _write_fixture(history)

    result = LegacyDualRunService().run(
        isolation_root=isolation,
        source_snapshot=source,
        historical_output=history,
        candidate_output=candidate,
        runner=None,
        line_ending=LineEnding.LF,
    )

    assert result.status is LegacyAcceptanceStatus.PENDING


def test_legacy_dual_run_rejects_reference_path_outside_isolation(tmp_path: Path) -> None:
    """验证参考工作区等隔离根外路径不会被 runner 触碰。"""
    isolation = tmp_path / "isolation"
    isolation.mkdir()
    history = isolation / "history"
    history.mkdir()
    _write_fixture(history)

    result = LegacyDualRunService().run(
        isolation_root=isolation,
        source_snapshot=tmp_path / "outside-source",
        historical_output=history,
        candidate_output=isolation / "candidate",
        runner=_Runner(),
        line_ending=LineEnding.LF,
    )

    assert result.status is LegacyAcceptanceStatus.FAILED
    assert "isolation_root" in result.summary or result.parser_errors
