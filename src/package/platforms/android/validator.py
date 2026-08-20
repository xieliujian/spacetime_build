"""Android APK/AAB ZIP 结构和 ABI 安全验证。"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from package.platforms.android.model import AndroidOutputKind, AndroidPackageOptions


@dataclass(frozen=True, slots=True)
class AndroidValidationReport:
    """Android 包体验证结果。"""

    is_valid: bool
    entries: tuple[str, ...]


class AndroidPackageValidator:
    """验证包体扩展名、ZIP 路径、Manifest 和请求 ABI。"""

    @staticmethod
    def validate(path: Path, options: AndroidPackageOptions) -> AndroidValidationReport:
        """执行不依赖 Android SDK 的基础包体验证。

        参数：
            path: 已构建 APK/AAB 文件路径。
            options: 与包体请求绑定的 Android 选项。

        返回：
            ``AndroidValidationReport``；基础检查通过时 ``is_valid`` 为 True。

        异常：
            路径、扩展名、ZIP 重复/逃逸、Manifest 或 ABI 缺失时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只读 ZIP，不执行签名工具、不解压到文件系统、不修改包体。
        """
        if not isinstance(path, Path) or not path.is_file():
            raise ValueError("包体 path 必须是存在的普通文件")
        if not isinstance(options, AndroidPackageOptions):
            raise TypeError("options 必须是 AndroidPackageOptions")
        expected_suffix = ".apk" if options.output_kind is AndroidOutputKind.APK else ".aab"
        if path.suffix.casefold() != expected_suffix:
            raise ValueError("包体扩展名与 output_kind 不一致")
        try:
            with zipfile.ZipFile(path) as archive:
                names = tuple(archive.namelist())
                _validate_names(names)
                if len(set(names)) != len(names):
                    raise ValueError("包体 ZIP 存在重复条目")
                if not any(name.endswith("AndroidManifest.xml") for name in names):
                    raise ValueError("包体缺少 AndroidManifest.xml")
                for abi in options.abis:
                    prefix = (
                        f"lib/{abi.value}/"
                        if options.output_kind is AndroidOutputKind.APK
                        else f"base/lib/{abi.value}/"
                    )
                    if not any(name.startswith(prefix) for name in names):
                        raise ValueError(f"包体缺少请求 ABI: {abi.value}")
        except zipfile.BadZipFile as exc:
            raise ValueError("Android 包体不是合法 ZIP") from exc
        return AndroidValidationReport(True, names)


def _validate_names(names: tuple[str, ...]) -> None:
    """拒绝绝对、反斜杠和点段 ZIP 条目。"""
    for name in names:
        if not isinstance(name, str) or not name or name.startswith(("/", "\\")) or "\\" in name:
            raise ValueError("包体 ZIP 条目路径非法")
        if any(segment in {"", ".", ".."} for segment in name.split("/")):
            raise ValueError("包体 ZIP 条目存在路径逃逸")
