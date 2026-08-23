"""受限图像 codec 和纯 RGBA 内存表示。

``RasterImage`` 不依赖第三方库，供 resize、裁边、Alpha 和拆图保持像素级确定性。
磁盘 PNG/JPEG 读取写入在调用时惰性导入 Pillow；缺少可选依赖只报告工具错误，导入
本模块本身不会触发安装、扫描或文件 I/O。
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.errors import ArtifactValidationError, ToolExecutionError


class MetadataPolicy(str, Enum):
    """图像元数据处理策略。"""

    STRIP = "strip"


@dataclass(frozen=True, slots=True)
class RasterImage:
    """保存宽、高和紧凑 RGBA 字节的不可变图像。"""

    width: int
    height: int
    pixels: bytes

    def __post_init__(self) -> None:
        """校验尺寸、像素总量和有限资源上限。"""
        for value, name in ((self.width, "width"), (self.height, "height")):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数")
        if not isinstance(self.pixels, bytes):
            raise TypeError("pixels 必须是 bytes")
        expected = self.width * self.height * 4
        if len(self.pixels) != expected:
            raise ValueError(f"pixels 长度必须为 {expected}")

    @property
    def pixel_count(self) -> int:
        """返回像素总数。"""
        return self.width * self.height


def pillow_version() -> str | None:
    """返回当前 Pillow 版本；未安装时返回 ``None``。"""
    try:
        return importlib.metadata.version("Pillow")
    except importlib.metadata.PackageNotFoundError:
        return None


def _require_pillow() -> object:
    """惰性载入 Pillow 并转换缺失依赖为工具错误。"""
    try:
        from PIL import Image
    except ImportError as exc:
        raise ToolExecutionError("图像 codec 需要安装受控版本 Pillow") from exc
    return Image


class ImageCodec:
    """使用白名单元数据策略读写受限 RGBA 图像。"""

    def __init__(self, *, max_pixels: int = 16_777_216) -> None:
        """保存正整数像素上限。"""
        if isinstance(max_pixels, bool) or not isinstance(max_pixels, int) or max_pixels <= 0:
            raise ValueError("max_pixels 必须是正整数")
        self._max_pixels = max_pixels

    def read(self, path: Path) -> RasterImage:
        """读取图像并强制转换为 RGBA，同时执行像素上限检查。"""
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path 必须是绝对 Path")
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(path)
        image_module = _require_pillow()
        try:
            with image_module.open(path) as image:
                width, height = image.size
                if width * height > self._max_pixels:
                    raise ArtifactValidationError("图像像素数超过上限")
                converted = image.convert("RGBA")
                return RasterImage(width, height, converted.tobytes())
        except ArtifactValidationError:
            raise
        except Exception as exc:
            raise ArtifactValidationError(f"图像无法解析: {path.name}") from exc

    def write_png(
        self,
        image: RasterImage,
        path: Path,
        *,
        metadata_policy: MetadataPolicy = MetadataPolicy.STRIP,
    ) -> None:
        """以固定 PNG 参数写出 RGBA 图像，不保留 EXIF/ICC 等可变元数据。"""
        if not isinstance(image, RasterImage):
            raise TypeError("image 必须是 RasterImage")
        if image.pixel_count > self._max_pixels:
            raise ArtifactValidationError("图像像素数超过上限")
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("path 必须是绝对 Path")
        if not isinstance(metadata_policy, MetadataPolicy):
            raise TypeError("metadata_policy 必须是 MetadataPolicy")
        path.parent.mkdir(parents=True, exist_ok=True)
        image_module = _require_pillow()
        try:
            output = image_module.frombytes("RGBA", (image.width, image.height), image.pixels)
            output.save(path, format="PNG", optimize=False, compress_level=9)
        except Exception as exc:
            raise ToolExecutionError(f"PNG 写入失败: {path.name}") from exc


__all__ = ["ImageCodec", "MetadataPolicy", "RasterImage", "pillow_version"]
