"""Gradle 工程结构只读检查测试。"""

from pathlib import Path

from package.platforms.android.gradle_project import GradleProjectInspector


def test_gradle_project_inspector_checks_required_modules_and_wrapper(tmp_path: Path) -> None:
    """验证完整工程通过，缺少 launcher 时返回结构化失败。"""
    for path in (
        "launcher/build.gradle",
        "unityLibrary/build.gradle",
        "gradle/wrapper/gradle-wrapper.properties",
        "settings.gradle",
        "build.gradle",
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// test", encoding="utf-8")
    report = GradleProjectInspector.inspect(tmp_path)
    assert report.is_valid is True
    (tmp_path / "launcher/build.gradle").unlink()
    report = GradleProjectInspector.inspect(tmp_path)
    assert report.is_valid is False
    assert "launcher/build.gradle" in report.missing_paths
