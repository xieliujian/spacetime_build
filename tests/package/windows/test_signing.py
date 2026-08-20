"""Windows Authenticode 签名计划测试。"""

from pathlib import Path

import pytest

from configuration.model import SecretRef
from package.platforms.windows.model import (
    PfxSigningOptions,
    WindowsArchitecture,
    WindowsInstallScope,
    WindowsOutputKind,
    WindowsPackageOptions,
    WindowsProfile,
    WindowsRuntimePolicy,
)
from package.platforms.windows.signing import (
    WindowsSigningPlanner,
    WindowsSigningStage,
)


def _options() -> WindowsPackageOptions:
    """构造生产签名计划的固定输入。"""
    return WindowsPackageOptions(
        WindowsArchitecture.X86_64,
        (WindowsOutputKind.PORTABLE, WindowsOutputKind.INSTALLER),
        WindowsInstallScope.USER,
        WindowsRuntimePolicy.BUNDLED,
        PfxSigningOptions(
            SecretRef("secret://windows/pfx"),
            SecretRef("secret://windows/password"),
            "a" * 64,
        ),
        WindowsProfile.PRODUCTION,
    )


def test_windows_signing_plan_is_sha256_stage_specific_and_deterministic(tmp_path: Path) -> None:
    """验证 payload 与 installer 是独立阶段，待签文件按路径确定性排序。"""
    payload = WindowsSigningPlanner.plan(
        _options(),
        WindowsSigningStage.PAYLOAD,
        (tmp_path / "Launcher.exe", tmp_path / "Game.exe"),
        timestamp_url="https://timestamp.example/sign",
        timestamp_allowlist=("https://timestamp.example/sign",),
    )
    installer = WindowsSigningPlanner.plan(
        _options(),
        WindowsSigningStage.INSTALLER,
        (tmp_path / "setup.exe",),
        timestamp_url=None,
    )

    assert payload.algorithm == "sha256"
    assert payload.files == (tmp_path / "Game.exe", tmp_path / "Launcher.exe")
    assert payload.stage is WindowsSigningStage.PAYLOAD
    assert installer.stage is WindowsSigningStage.INSTALLER
    assert payload.files != installer.files


def test_windows_signing_plan_rejects_unsigned_production_and_timestamp_not_allowlisted(
    tmp_path: Path,
) -> None:
    """验证签名计划不接受生产 unsigned 或非 HTTPS/非白名单 timestamp。"""
    unsigned_test = WindowsPackageOptions(
        WindowsArchitecture.X86_64,
        (WindowsOutputKind.PORTABLE,),
        WindowsInstallScope.USER,
        WindowsRuntimePolicy.BUNDLED,
        None,
        WindowsProfile.TEST,
    )
    plan = WindowsSigningPlanner.plan(
        unsigned_test, WindowsSigningStage.PAYLOAD, (tmp_path / "Game.exe",)
    )
    assert plan.signing is None

    with pytest.raises(ValueError):
        WindowsSigningPlanner.plan(
            _options(),
            WindowsSigningStage.PAYLOAD,
            (tmp_path / "Game.exe",),
            timestamp_url="http://timestamp.example/sign",
        )
    with pytest.raises(ValueError, match="allowlist"):
        WindowsSigningPlanner.plan(
            _options(),
            WindowsSigningStage.PAYLOAD,
            (tmp_path / "Game.exe",),
            timestamp_url="https://other.example/sign",
            timestamp_allowlist=("https://timestamp.example/sign",),
        )


def test_windows_signing_plan_rejects_non_string_timestamp_allowlist_entries(
    tmp_path: Path,
) -> None:
    """验证 timestamp allowlist 在排序前收窄元素类型并返回领域错误。"""
    with pytest.raises(TypeError, match="timestamp_allowlist"):
        WindowsSigningPlanner.plan(
            _options(),
            WindowsSigningStage.PAYLOAD,
            (tmp_path / "Game.exe",),
            timestamp_allowlist=(1,),  # type: ignore[arg-type]
        )
