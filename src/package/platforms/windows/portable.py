"""Windows 便携包的确定性 ZIP 归档。

本模块只负责把已经准备好的 Windows Player payload 编码为可审计的 ZIP 字节，
不负责签名、不读取证书或秘密，也不启动任何外部工具。payload 通过显式的
``signed`` 和 ``WindowsProfile`` 字段表达测试 unsigned 与生产签名输入边界；
生产 payload 必须在进入归档器前已经完成签名验证。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from core.artifacts import BlobRef
from package.platforms.windows.model import WindowsProfile
from package.platforms.windows.path_rules import validate_windows_relative_path

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class WindowsPortablePayload:
    """描述一个待归档的 Windows payload 及其签名边界。

    参数：
        root: 已存在的绝对 payload 根目录；目录内的普通文件和空目录会被归档。
        signed: 调用方对 payload 已完成签名并通过验证的声明。该字段不替代真正的
            Authenticode 检查，真实签名检查由前置签名阶段负责。
        profile: ``TEST`` 允许显式 unsigned 测试输入，``PRODUCTION`` 必须要求
            ``signed=True``。

    返回：
        一个不可变 payload 输入对象。

    异常：
        根目录不是绝对目录、根目录本身是符号链接、签名标记或 profile 类型错误时
        抛出 ``TypeError`` 或 ``ValueError``；生产 profile 的 unsigned 输入必定拒绝。

    约束与副作用：
        只检查路径和内存字段，不读取目录内容、不执行签名工具、不读取秘密。
    """

    root: Path
    signed: bool
    profile: WindowsProfile

    def __post_init__(self) -> None:
        """校验 payload 根目录和 unsigned 输入边界。"""
        if not isinstance(self.root, Path) or not self.root.is_absolute():
            raise ValueError("payload root 必须是绝对 Path")
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("payload root 必须是非符号链接目录")
        if not isinstance(self.signed, bool):
            raise TypeError("signed 必须是 bool")
        if not isinstance(self.profile, WindowsProfile):
            raise TypeError("profile 必须是 WindowsProfile")
        if self.profile is WindowsProfile.PRODUCTION and not self.signed:
            raise ValueError("production payload 不允许 unsigned")


@dataclass(frozen=True, slots=True)
class WindowsPortableArchive:
    """保存确定性 ZIP 字节、逻辑条目和内容寻址摘要。

    参数：
        content: 完整 ZIP 文件字节，包含固定时间、权限、压缩参数和条目顺序。
        files: ZIP 内按 UTF-8 字节序排列的文件及目录条目；目录以 ``/`` 结尾。
        blob: 指向归档内容的 ``BlobRef``，其哈希和大小由 ``content`` 计算。
        signed: 输入 payload 的签名声明，便于下游审计 unsigned 测试包边界。
        profile: 生成该归档时使用的 Windows profile。

    返回：
        一个不可变的归档摘要对象。

    异常：
        归档器内部只创建经过校验的实例；调用方若传入不一致字段会得到标准数据类
        行为，归档构造不负责重新读取文件。

    约束与副作用：
        对象只保存内存数据，不写文件、不上传 Blob，也不执行签名工具。
    """

    content: bytes
    files: tuple[str, ...]
    blob: BlobRef
    signed: bool
    profile: WindowsProfile


class WindowsPortableBuilder:
    """把 Windows payload 编码为稳定、安全且可寻址的便携 ZIP。"""

    @staticmethod
    def validate_archive_path(value: str) -> None:
        """公开校验 ZIP 逻辑路径，供布局计划在归档前复用同一安全契约。

        参数：
            value: 预期为正斜杠分隔的相对 ZIP 路径；目录尾斜杠会被视为目录标记。

        返回：
            ``None``；校验通过时表示路径不会触发 zip-slip。

        异常：
            绝对路径、盘符路径、反斜杠、空段、``.``、``..`` 或保留设备名会抛出
            ``ValueError``。

        约束与副作用：
            只做纯内存校验，不访问文件系统、不修改输入，也不执行外部工具。
        """
        _validate_zip_path(value, allow_trailing_slash=True)

    @staticmethod
    def build(payload: WindowsPortablePayload) -> WindowsPortableArchive:
        """读取 payload 并返回确定性 ZIP 归档，不修改输入目录。

        参数：
            payload: 已通过签名边界校验的 Windows payload；生产 profile 必须已签名。

        返回：
            包含 ZIP 字节、稳定条目列表和 ``BlobRef`` 的归档摘要。

        异常：
            payload 类型错误、生产 unsigned、发现符号链接、非普通文件、非法 ZIP
            路径或大小写折叠后的重复路径时抛出 ``TypeError`` 或 ``ValueError``。

        约束与副作用：
            只读 payload；目录条目、文件条目、时间戳、权限、压缩算法和排序全部固定，
            不执行签名、安装器、外部进程或持久化写入。
        """
        if not isinstance(payload, WindowsPortablePayload):
            raise TypeError("payload 必须是 WindowsPortablePayload")
        if payload.profile is WindowsProfile.PRODUCTION and not payload.signed:
            raise ValueError("production payload 不允许 unsigned")

        entries = _collect_entries(payload.root)
        output = io.BytesIO()
        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for name, source, is_directory in entries:
                info = _zip_info(name, is_directory=is_directory)
                content = b"" if is_directory else source.read_bytes()
                archive.writestr(info, content)

        content = output.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        names = tuple(item[0] for item in entries)
        return WindowsPortableArchive(
            content,
            names,
            BlobRef(f"blobs/{digest}", digest, len(content)),
            payload.signed,
            payload.profile,
        )


def _collect_entries(root: Path) -> tuple[tuple[str, Path, bool], ...]:
    """收集并校验 payload 条目，返回稳定的目录优先无关路径列表。

    参数：
        root: 已由 ``WindowsPortablePayload`` 校验的 payload 根目录。

    返回：
        ``(ZIP 名称, 源路径, 是否目录)`` 元组，按 ZIP 名称 UTF-8 字节序排序。

    异常：
        符号链接、特殊文件、非法路径组件、保留设备名或 Windows 大小写折叠重复
        路径会抛出 ``ValueError``。

    约束与副作用：
        仅枚举目录元数据；不跟随链接，不读取文件内容，不改变源树。显式保留空目录
        条目，避免归档后目录结构丢失。
    """
    candidates = sorted(
        root.rglob("*"), key=lambda path: path.relative_to(root).as_posix().encode("utf-8")
    )
    entries: list[tuple[str, Path, bool]] = []
    seen: set[str] = set()
    for candidate in candidates:
        relative = candidate.relative_to(root).as_posix()
        _validate_zip_path(relative)
        if candidate.is_symlink():
            raise ValueError(f"payload 不允许符号链接: {relative}")
        if candidate.is_dir():
            name = f"{relative}/"
            is_directory = True
        elif candidate.is_file():
            name = relative
            is_directory = False
        else:
            raise ValueError(f"payload 只支持普通文件和目录: {relative}")

        identity = name.rstrip("/").casefold()
        if identity in seen:
            raise ValueError(f"payload 存在大小写折叠重复路径: {relative}")
        seen.add(identity)
        entries.append((name, candidate, is_directory))
    return tuple(sorted(entries, key=lambda item: item[0].encode("utf-8")))


def _validate_zip_path(value: str, *, allow_trailing_slash: bool = False) -> None:
    """校验 ZIP 条目为 Windows 可提取的正斜杠相对路径。

    参数：
        value: 不带目录尾斜杠的 payload 相对路径。

    返回：
        ``None``；通过校验时表示该路径不会产生 zip-slip。

    异常：
        空路径、绝对路径、盘符路径、反斜杠、空段、``.``、``..`` 或 Windows 保留
        设备名会抛出 ``ValueError``。

    约束与副作用：
        纯内存校验，不访问文件系统；路径逻辑只使用 ``/``，不依赖运行平台解析规则。
    """
    try:
        validate_windows_relative_path(
            value,
            label="ZIP 路径",
            allow_trailing_slash=allow_trailing_slash,
        )
    except ValueError as exc:
        raise ValueError(f"ZIP 路径非法: {value!r}") from exc


def _zip_info(name: str, *, is_directory: bool) -> zipfile.ZipInfo:
    """创建所有 ZIP 元数据均固定的条目信息。

    参数：
        name: 已通过安全校验的 ZIP 条目名。
        is_directory: 是否为保留空目录的目录条目。

    返回：
        设置固定时间戳、权限、编码标志、压缩类型和创建系统的 ``ZipInfo``。

    异常：
        ``name`` 不满足 ZIP 路径约束时抛出 ``ValueError``。

    约束与副作用：
        不读取文件、不访问外部系统；固定元数据使相同 payload 产生相同 ZIP 字节。
    """
    _validate_zip_path(name.rstrip("/"))
    info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.flag_bits = 0x800
    info.internal_attr = 0
    if is_directory:
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = (0o40755 << 16) | 0x10
    else:
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
    return info


__all__ = [
    "WindowsPortableArchive",
    "WindowsPortableBuilder",
    "WindowsPortablePayload",
]
