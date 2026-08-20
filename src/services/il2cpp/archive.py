"""IL2CPP 输入输出归档的确定性编码和受限解包实现。

本模块只处理已经位于隔离工作区的普通文件，不执行 IL2CPP 工具，也不跟随符号链接。
归档成员使用 UTF-8 字节序排序、固定时间和固定权限，确保相同目录内容得到相同字节；
解包先完整校验成员路径、大小、数量和摘要，再通过 staging 目录原子替换目标目录。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

from core.artifacts import BlobRef


@dataclass(frozen=True, slots=True)
class Il2CppArchiveLimits:
    """限制归档文件数量、单文件大小和解压后的总大小。

    参数：
        max_files: 允许的最大普通文件数。
        max_file_size: 允许的单个文件最大字节数。
        max_total_size: 允许的所有文件解包后最大总字节数。

    返回：
        一个不可变限制对象。

    异常：
        任一限制不是非负整数时抛出 ``ValueError``。

    约束与副作用：
        只保存内存配置，不访问文件系统；零值表示拒绝任何非空输入。
    """

    max_files: int = 100_000
    max_file_size: int = 4 * 1024 * 1024 * 1024
    max_total_size: int = 16 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        """校验所有归档资源上限为非负整数。"""
        for field_name in ("max_files", "max_file_size", "max_total_size"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} 必须是非负整数")


@dataclass(frozen=True, slots=True)
class Il2CppArchiveEntry:
    """记录一个归档普通文件的逻辑路径、大小和 SHA-256 摘要。

    参数：
        path: 归档内的正斜杠相对路径。
        size: 文件字节数。
        sha256: 文件内容的小写 SHA-256。

    返回：
        一个不可变文件表项。

    异常：
        路径、大小或摘要不合法时抛出 ``ValueError``。

    约束与副作用：
        不读取路径指向的文件；路径只能表达归档逻辑成员名。
    """

    path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        """校验归档成员路径、大小和摘要格式。"""
        _validate_member_path(self.path)
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise ValueError("archive entry size 必须是非负整数")
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ValueError("archive entry sha256 必须是 64 位小写十六进制")


@dataclass(frozen=True, slots=True)
class Il2CppArchive:
    """描述已经写入磁盘的 IL2CPP 归档及其内容摘要。

    参数：
        path: 归档文件的绝对路径。
        blob: 归档文件本身的内容寻址摘要。
        entries: 按逻辑路径 UTF-8 字节序排列的普通文件表。

    返回：
        一个不可变归档结果。

    异常：
        路径不是绝对 ``Path``、Blob 类型错误或文件表集合错误时抛出 ``TypeError``
        或 ``ValueError``。

    约束与副作用：
        对象只表示已完成的归档；实际文件写入由 ``Il2CppArchiveCodec.create`` 完成。
    """

    path: Path
    blob: BlobRef
    entries: tuple[Il2CppArchiveEntry, ...]

    def __post_init__(self) -> None:
        """校验归档结果结构并确保文件表排序。"""
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("archive path 必须是绝对 Path")
        if not isinstance(self.blob, BlobRef):
            raise TypeError("archive blob 必须是 BlobRef")
        if not isinstance(self.entries, tuple) or not all(
            isinstance(item, Il2CppArchiveEntry) for item in self.entries
        ):
            raise TypeError("archive entries 必须是 tuple[Il2CppArchiveEntry, ...]")
        ordered = tuple(sorted(self.entries, key=lambda item: item.path.encode("utf-8")))
        if ordered != self.entries:
            raise ValueError("archive entries 必须按 UTF-8 路径排序")


class Il2CppArchiveCodec:
    """创建确定性 IL2CPP ZIP 并安全解包到新目录。"""

    @staticmethod
    def create(
        root: Path,
        output: Path,
        *,
        limits: Il2CppArchiveLimits = Il2CppArchiveLimits(),
    ) -> Il2CppArchive:
        """收集普通文件并原子写入确定性 ZIP 归档。

        参数：
            root: 已存在的绝对输入目录，不允许包含符号链接。
            output: 需要创建的绝对 ZIP 路径；同名目标会被原子替换。
            limits: 文件数、单文件和总字节上限。

        返回：
            包含 ZIP ``BlobRef`` 和成员文件表的 ``Il2CppArchive``。

        异常：
            输入目录、路径、链接、重复成员、上限或文件读取失败时抛出 ``ValueError``
            或底层 ``OSError``；失败时不保留临时归档。

        约束与副作用：
            只读取 root 并写入 output 同目录临时文件；不会修改输入文件。
        """
        _validate_absolute_directory(root, "root")
        _validate_absolute_path(output, "output")
        if not isinstance(limits, Il2CppArchiveLimits):
            raise TypeError("limits 必须是 Il2CppArchiveLimits")
        files = _collect_files(root, limits)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        entries: list[Il2CppArchiveEntry] = []
        try:
            with zipfile.ZipFile(
                temporary,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
                allowZip64=True,
            ) as archive:
                for relative, content in files:
                    digest = hashlib.sha256(content).hexdigest()
                    entries.append(Il2CppArchiveEntry(relative, len(content), digest))
                    info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.create_system = 3
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, content, compresslevel=9)
            os.replace(temporary, output)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        archive_bytes = output.read_bytes()
        archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
        blob = BlobRef(f"sha256:{archive_sha256}", archive_sha256, len(archive_bytes))
        return Il2CppArchive(output, blob, tuple(entries))

    @staticmethod
    def extract(
        archive_path: Path,
        destination: Path,
        *,
        limits: Il2CppArchiveLimits = Il2CppArchiveLimits(),
    ) -> tuple[Il2CppArchiveEntry, ...]:
        """校验 ZIP 成员并原子解包到一个此前不存在的目录。

        参数：
            archive_path: 已存在的绝对 ZIP 文件。
            destination: 不得已存在的绝对目标目录。
            limits: 归档文件数、单文件和总解包大小上限。

        返回：
            按逻辑路径排序的解包文件表。

        异常：
            ZIP 损坏、路径逃逸、符号链接、重复路径、摘要/大小异常或超限时抛出
            ``ValueError``；任何异常都清理 staging 目录。

        约束与副作用：
            目标目录只在全部成员读取并校验后通过 ``os.replace`` 出现，避免留下半解包树。
        """
        _validate_absolute_path(archive_path, "archive_path")
        _validate_absolute_path(destination, "destination")
        if not archive_path.is_file():
            raise ValueError("archive_path 必须是已存在的普通文件")
        if destination.exists():
            raise ValueError("destination 必须不存在")
        if not isinstance(limits, Il2CppArchiveLimits):
            raise TypeError("limits 必须是 Il2CppArchiveLimits")
        staging = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
        entries: list[Il2CppArchiveEntry] = []
        try:
            staging.mkdir(parents=True, exist_ok=False)
            with zipfile.ZipFile(archive_path, mode="r", allowZip64=True) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir()]
                if len(infos) > limits.max_files:
                    raise ValueError("归档文件数量超过限制")
                seen: set[str] = set()
                total_size = 0
                for info in infos:
                    relative = _validate_member_path(info.filename)
                    folded = relative.casefold()
                    if folded in seen:
                        raise ValueError(f"归档存在大小写折叠重复路径: {relative}")
                    seen.add(folded)
                    if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                        raise ValueError(f"归档不允许符号链接: {relative}")
                    if info.file_size > limits.max_file_size:
                        raise ValueError("归档单文件大小超过限制")
                    total_size += info.file_size
                    if total_size > limits.max_total_size:
                        raise ValueError("归档总解包大小超过限制")
                    content = archive.read(info)
                    if len(content) != info.file_size:
                        raise ValueError(f"归档文件大小校验失败: {relative}")
                    digest = hashlib.sha256(content).hexdigest()
                    target = _safe_child(staging, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    entries.append(Il2CppArchiveEntry(relative, len(content), digest))
            os.replace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return tuple(sorted(entries, key=lambda item: item.path.encode("utf-8")))


def _validate_absolute_path(value: Path, field_name: str) -> None:
    """校验输入路径是绝对 ``Path``，但不访问其目标内容。"""
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError(f"{field_name} 必须是绝对 Path")


def _validate_absolute_directory(value: Path, field_name: str) -> None:
    """校验输入路径是已存在的绝对普通目录。"""
    _validate_absolute_path(value, field_name)
    if not value.is_dir():
        raise ValueError(f"{field_name} 必须是已存在目录")


def _validate_member_path(value: object) -> str:
    """校验 ZIP 成员为无逃逸的正斜杠相对路径并返回规范文本。"""
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ValueError("归档成员路径必须是非空正斜杠相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"归档成员路径含非法段: {value}")
    if len(path.parts) != len(value.split("/")):
        raise ValueError(f"归档成员路径不规范: {value}")
    return value


def _collect_files(root: Path, limits: Il2CppArchiveLimits) -> list[tuple[str, bytes]]:
    """读取并校验目录下的普通文件，返回按路径排序的内存快照。"""
    candidates = sorted(
        root.rglob("*"),
        key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
    )
    files: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    total_size = 0
    for candidate in candidates:
        relative = _validate_member_path(candidate.relative_to(root).as_posix())
        if candidate.is_symlink():
            raise ValueError(f"归档不允许符号链接: {relative}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise ValueError(f"归档只支持普通文件: {relative}")
        folded = relative.casefold()
        if folded in seen:
            raise ValueError(f"归档存在大小写折叠重复路径: {relative}")
        seen.add(folded)
        content = candidate.read_bytes()
        if len(content) > limits.max_file_size:
            raise ValueError("归档单文件大小超过限制")
        total_size += len(content)
        if total_size > limits.max_total_size:
            raise ValueError("归档总大小超过限制")
        files.append((relative, content))
        if len(files) > limits.max_files:
            raise ValueError("归档文件数量超过限制")
    return files


def _safe_child(root: Path, relative: str) -> Path:
    """把已校验成员路径绑定到 root，并再次检查解析后的路径不逃逸。"""
    target = root.joinpath(*relative.split("/"))
    try:
        target.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"归档成员路径逃逸: {relative}") from exc
    return target


__all__ = ["Il2CppArchive", "Il2CppArchiveCodec", "Il2CppArchiveEntry", "Il2CppArchiveLimits"]
