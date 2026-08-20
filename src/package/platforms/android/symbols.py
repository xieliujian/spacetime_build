"""Android native symbols 的确定性 ZIP 归档。"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from core.artifacts import BlobRef


@dataclass(frozen=True, slots=True)
class AndroidSymbolArchive:
    """符号归档字节、文件列表和内容寻址 Blob。"""

    content: bytes
    files: tuple[str, ...]
    blob: BlobRef


class AndroidSymbolCollector:
    """按 ABI 收集 libil2cpp.so 和 mapping 等符号并生成稳定归档。"""

    @staticmethod
    def collect(root: Path, abis: tuple[str, ...]) -> AndroidSymbolArchive:
        """收集每个 ABI 目录下的文件并使用固定 ZIP 元数据归档。

        参数：
            root: 符号根目录。
            abis: 已由 Android 选项确认的 ABI 标签。

        返回：
            内容寻址 ``AndroidSymbolArchive``。

        异常：
            路径、ABI、符号目录或缺少 ``libil2cpp.so`` 时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只读符号目录；ZIP 条目按 UTF-8 路径排序、时间戳固定，不删除输入。
        """
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("root 必须是绝对 Path")
        if not isinstance(abis, tuple) or not abis:
            raise ValueError("abis 必须是非空 tuple")
        names: list[str] = []
        for abi in sorted(set(abis)):
            if not isinstance(abi, str) or not abi or "/" in abi or "\\" in abi:
                raise ValueError("ABI 必须是单一路径段")
            directory = root / abi
            if not directory.is_dir() or not (directory / "libil2cpp.so").is_file():
                raise ValueError(f"ABI 缺少 libil2cpp.so: {abi}")
            for path in directory.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                names.append(path.relative_to(root).as_posix())
        ordered = tuple(sorted(set(names), key=lambda name: name.encode("utf-8")))
        output = io.BytesIO()
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name in ordered:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                archive.writestr(
                    info,
                    (root / name).read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        content = output.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        return AndroidSymbolArchive(
            content, ordered, BlobRef(f"blobs/{digest}", digest, len(content))
        )
