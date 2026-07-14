"""验证项目分发包的元数据、文件内容和安装后导入行为。

本模块通过当前解释器调用 pip 构建并安装 wheel，覆盖 setuptools 的 ``src`` 包发现和
动态版本配置。测试只在 Python 3.10+ 执行，所有产物均写入 pytest 临时目录，不修改
源码树、用户环境或旧构建目录。
"""

from __future__ import annotations

import os
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """运行子进程，并在失败时同时报告标准输出和标准错误。

    参数：
        command: 按参数边界拆分后的命令，不经过 shell 解析。
        cwd: 子进程工作目录，用于控制构建上下文并避免意外导入源码树。
        environment: 可选的完整环境变量映射；省略时继承当前进程环境。

    返回：
        成功完成的文本模式子进程结果，可用于继续断言标准输出。

    异常：
        子进程退出码非零或无法启动时调用 ``pytest.fail`` 终止测试，错误信息包含命令、
        退出码、标准输出和标准错误；进程未启动时会明确标记没有产生标准输出。

    约束与副作用：
        函数不启用 shell，也不修改当前进程环境；被调用命令可在传入工作目录或其显式
        目标路径中写文件。
    """
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        pytest.fail(
            "子进程无法启动：\n"
            f"命令：{command!r}\n"
            "退出码：<未产生>\n"
            "标准输出：<未产生>\n"
            f"标准错误：\n{error}"
        )
    if result.returncode != 0:
        pytest.fail(
            "子进程执行失败：\n"
            f"命令：{command!r}\n"
            f"退出码：{result.returncode}\n"
            f"标准输出：\n{result.stdout}\n"
            f"标准错误：\n{result.stderr}"
        )
    return result


def test_source_build_package_is_not_ignored() -> None:
    """验证根忽略规则不会把 ``src/st/build`` 源码包排除在 Git 之外。

    参数：
        无。

    返回：
        无返回值；Git 明确报告目标源码未被忽略即表示契约成立。

    异常：
        已安装 Git 但命令执行异常、当前目录不是仓库或源码被规则匹配时由断言报告失败，
        失败信息包含退出码、标准输出和标准错误；系统没有 Git 时明确跳过。

    约束与副作用：
        测试只读取 Git 忽略配置和索引状态，不修改工作区、索引或用户配置。
    """
    git_executable = shutil.which("git")
    if git_executable is None:
        pytest.skip("当前环境未安装 Git，无法验证源码忽略规则")

    result = subprocess.run(
        [
            git_executable,
            "check-ignore",
            "--verbose",
            "--no-index",
            "--",
            "src/st/build/__init__.py",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 1, (
        "源码包不应被 Git 忽略：\n"
        f"退出码：{result.returncode}\n"
        f"标准输出：\n{result.stdout}\n"
        f"标准错误：\n{result.stderr}"
    )


@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="分发包契约要求使用项目支持的 Python 3.10+ 解释器验证",
)
def test_wheel_metadata_content_and_isolated_import(tmp_path: Path) -> None:
    """验证 wheel 使用源码版本、包含目标包并能在隔离环境导入。

    参数：
        tmp_path: pytest 提供的独立临时目录，用于保存 wheel、安装目标和隔离工作目录。

    返回：
        无返回值；所有分发契约通过断言表达。

    异常：
        wheel 构建、安装或隔离导入失败时，辅助函数会报告子进程的完整标准输出与错误；
        wheel 缺少文件或元数据不匹配时由断言报告失败。

    约束与副作用：
        Python 3.10 以下明确跳过。测试仅向临时目录写入文件，不安装到当前环境；隔离
        导入子进程使用 ``-I`` 且只显式加入临时安装目标，不能从仓库 ``src`` 导入。
    """
    isolated_project = tmp_path / "project"
    isolated_project.mkdir()
    shutil.copy2(PROJECT_ROOT / "pyproject.toml", isolated_project / "pyproject.toml")
    shutil.copy2(PROJECT_ROOT / "README.md", isolated_project / "README.md")
    shutil.copytree(
        PROJECT_ROOT / "src",
        isolated_project / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"),
    )

    wheel_directory = tmp_path / "wheel"
    wheel_directory.mkdir()
    pip_temporary_directory = tmp_path / "pip-temporary"
    pip_temporary_directory.mkdir()
    pip_environment = os.environ.copy()
    for variable_name in ("TMP", "TEMP", "TMPDIR"):
        pip_environment[variable_name] = str(pip_temporary_directory)
    pip_environment["PIP_CACHE_DIR"] = str(tmp_path / "pip-cache")
    _run_checked(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--wheel-dir",
            str(wheel_directory),
        ],
        cwd=isolated_project,
        environment=pip_environment,
    )

    wheel_paths = tuple(wheel_directory.glob("*.whl"))
    assert len(wheel_paths) == 1, f"预期生成一个 wheel，实际为：{wheel_paths!r}"
    wheel_path = wheel_paths[0]

    with zipfile.ZipFile(wheel_path) as wheel_archive:
        archive_names = wheel_archive.namelist()
        assert "st/build/__init__.py" in archive_names

        metadata_names = [name for name in archive_names if name.endswith(".dist-info/METADATA")]
        assert len(metadata_names) == 1, f"预期一个 METADATA，实际为：{metadata_names!r}"
        metadata = BytesParser(policy=default).parsebytes(wheel_archive.read(metadata_names[0]))

    assert metadata["Name"] == "spacetime-build"
    assert metadata["Version"] == "0.1.0"
    assert metadata["Requires-Python"] == ">=3.10"

    install_target = tmp_path / "installed"
    _run_checked(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            str(wheel_path),
            "--no-deps",
            "--target",
            str(install_target),
        ],
        cwd=tmp_path,
        environment=pip_environment,
    )

    isolated_working_directory = tmp_path / "isolated"
    isolated_working_directory.mkdir()
    import_script = (
        "import pathlib, sys; "
        f"target = pathlib.Path({str(install_target)!r}).resolve(); "
        f"sys.path.insert(0, {str(install_target)!r}); "
        "import st.build; "
        "module_path = pathlib.Path(st.build.__file__).resolve(); "
        "assert module_path == target / 'st' / 'build' / '__init__.py', module_path; "
        "assert st.build.__version__ == '0.1.0'; "
        "print(st.build.__version__)"
    )
    isolated_environment = os.environ.copy()
    isolated_environment.pop("PYTHONPATH", None)
    isolated_environment.pop("PYTHONHOME", None)
    import_result = _run_checked(
        [sys.executable, "-I", "-c", import_script],
        cwd=isolated_working_directory,
        environment=isolated_environment,
    )
    assert import_result.stdout.strip() == "0.1.0"
