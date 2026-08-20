"""Windows 包体模型测试。"""

import pytest

from configuration.model import SecretRef
from package.platforms.windows.model import (
    HardwareTokenSigningOptions,
    PfxSigningOptions,
    WindowsArchitecture,
    WindowsInstallScope,
    WindowsOutputKind,
    WindowsPackageOptions,
    WindowsProfile,
    WindowsRuntimePolicy,
)


def test_windows_options_normalize_outputs_and_keep_public_signing_summary() -> None:
    """验证 Windows 选项保留 x86_64、去重排序输出并支持 PFX 引用。"""
    options = WindowsPackageOptions(
        architecture=WindowsArchitecture.X86_64,
        outputs=(
            WindowsOutputKind.INSTALLER,
            WindowsOutputKind.PORTABLE,
            WindowsOutputKind.PORTABLE,
        ),
        install_scope=WindowsInstallScope.USER,
        runtime_policy=WindowsRuntimePolicy.BUNDLED,
        signing=PfxSigningOptions(
            SecretRef("secret://windows/pfx"),
            SecretRef("secret://windows/password"),
            "a" * 64,
        ),
        profile=WindowsProfile.PRODUCTION,
    )

    assert options.architecture is WindowsArchitecture.X86_64
    assert options.outputs == (WindowsOutputKind.INSTALLER, WindowsOutputKind.PORTABLE)
    assert options.signing is not None
    assert options.signing.certificate_thumbprint == "a" * 64
    assert "secret://" not in repr(options)


def test_windows_options_support_hardware_token_signing_without_secret_values() -> None:
    """验证 UKey 配置只保存 provider、设备选择器、指纹和 PIN 引用。"""
    signing = HardwareTokenSigningOptions(
        "LingRenProvider",
        "device-01",
        "b" * 64,
        SecretRef("secret://windows/pin"),
    )
    options = WindowsPackageOptions(
        WindowsArchitecture.X86_64,
        (WindowsOutputKind.PORTABLE,),
        WindowsInstallScope.MACHINE,
        WindowsRuntimePolicy.SYSTEM,
        signing,
        WindowsProfile.PRODUCTION,
    )

    assert options.signing == signing
    assert isinstance(options.signing, HardwareTokenSigningOptions)
    assert options.signing.pin_ref.reveal_locator() == "secret://windows/pin"


def test_windows_options_reject_unsigned_production_and_invalid_combinations() -> None:
    """验证 production 不能无签名，且空输出、弱指纹和无效枚举均被拒绝。"""
    with pytest.raises(ValueError, match="unsigned"):
        WindowsPackageOptions(
            WindowsArchitecture.X86_64,
            (WindowsOutputKind.PORTABLE,),
            WindowsInstallScope.USER,
            WindowsRuntimePolicy.BUNDLED,
            None,
            WindowsProfile.PRODUCTION,
        )
    with pytest.raises(ValueError):
        WindowsPackageOptions(
            WindowsArchitecture.X86_64,
            (),
            WindowsInstallScope.USER,
            WindowsRuntimePolicy.BUNDLED,
            None,
            WindowsProfile.TEST,
        )
    with pytest.raises(ValueError):
        PfxSigningOptions(
            SecretRef("secret://windows/pfx"),
            SecretRef("secret://windows/password"),
            "not-a-thumbprint",
        )
