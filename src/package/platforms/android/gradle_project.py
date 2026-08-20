"""Android Gradle 导出工程的只读结构检查。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GradleProjectReport:
    """Gradle 工程结构检查结果。"""

    root: Path
    missing_paths: tuple[str, ...]
    is_valid: bool


class GradleProjectInspector:
    """在不修改工程的前提下检查 Unity Gradle 导出结构。"""

    @staticmethod
    def inspect(root: Path) -> GradleProjectReport:
        """检查 launcher、unityLibrary、wrapper、settings 和根构建脚本。

        参数：
            root: Gradle 工程根目录。

        返回：
            包含稳定相对缺失路径列表的 ``GradleProjectReport``。

        异常：
            root 不是绝对 Path 时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只读取文件存在性；不解析脚本、不执行 Gradle、不写工程。
        """
        if not isinstance(root, Path):
            raise TypeError("root 必须是 Path")
        if not root.is_absolute():
            raise ValueError("root 必须是绝对路径")
        root = root.resolve()
        required = (
            ("launcher/build.gradle", "launcher/build.gradle.kts"),
            ("unityLibrary/build.gradle", "unityLibrary/build.gradle.kts"),
            ("gradle/wrapper/gradle-wrapper.properties",),
            ("settings.gradle", "settings.gradle.kts"),
            ("build.gradle", "build.gradle.kts"),
        )
        missing: list[str] = []
        for alternatives in required:
            if not any(_safe_child(root, candidate).is_file() for candidate in alternatives):
                missing.append(alternatives[0])
        return GradleProjectReport(root, tuple(missing), not missing)


def _safe_child(root: Path, relative_path: str) -> Path:
    """解析受限相对路径并拒绝路径逃逸。"""
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Gradle 工程路径逃逸 root: {relative_path!r}") from exc
    return candidate
