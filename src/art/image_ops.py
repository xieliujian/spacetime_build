"""确定性 RGBA 图像内存操作。

本模块不依赖 Pillow，不读写磁盘，也不修改传入 ``RasterImage``。所有尺寸、边界、
透明阈值、采样器和输出命名均显式校验，以便在没有美术工具链的节点上运行单元和
Golden 测试。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from art.image_codec import RasterImage


def _validate_dimension(value: object, field_name: str) -> int:
    """校验正整数尺寸。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} 必须是正整数")
    return value


def _pixel(image: RasterImage, x: int, y: int) -> bytes:
    """读取一个 RGBA 像素。"""
    offset = (y * image.width + x) * 4
    return image.pixels[offset : offset + 4]


@dataclass(frozen=True, slots=True)
class AlphaPolicy:
    """定义 Alpha 提取的曲线和是否半尺寸输出。"""

    gamma: float = 1.0
    half_size: bool = False

    def __post_init__(self) -> None:
        """校验 gamma 为有限正数。"""
        if isinstance(self.gamma, bool) or not isinstance(self.gamma, (int, float)):
            raise TypeError("gamma 必须是数值")
        if not math.isfinite(float(self.gamma)) or self.gamma <= 0:
            raise ValueError("gamma 必须是有限正数")
        if not isinstance(self.half_size, bool):
            raise TypeError("half_size 必须是 bool")


@dataclass(frozen=True, slots=True)
class SpriteFrame:
    """描述图集 JSON 中的一块未旋转矩形。"""

    name: str
    x: int
    y: int
    width: int
    height: int
    rotated: bool = False
    scale: float = 1.0

    def __post_init__(self) -> None:
        """校验名称、矩形边界参数、旋转和缩放规则。"""
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("frame name 必须非空")
        if any(character in self.name for character in ("/", "\\", "\r", "\n")):
            raise ValueError("frame name 不得包含路径分隔符")
        for value, field_name in (
            (self.x, "x"),
            (self.y, "y"),
            (self.width, "width"),
            (self.height, "height"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} 必须是非负整数")
        if self.width == 0 or self.height == 0:
            raise ValueError("frame width/height 必须为正数")
        if self.rotated:
            raise ValueError("不支持 rotated frame")
        if isinstance(self.scale, bool) or not isinstance(self.scale, (int, float)):
            raise TypeError("scale 必须是数值")
        if not math.isfinite(float(self.scale)) or self.scale <= 0:
            raise ValueError("scale 必须是有限正数")


def crop(image: RasterImage, x: int, y: int, width: int, height: int) -> RasterImage:
    """裁剪图像内的安全矩形并返回新像素对象。"""
    if not isinstance(image, RasterImage):
        raise TypeError("image 必须是 RasterImage")
    _validate_dimension(width, "width")
    _validate_dimension(height, "height")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
        raise TypeError("x/y 必须是整数")
    if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
        raise ValueError("crop 矩形越出图像边界")
    rows = [
        image.pixels[(y + row) * image.width * 4 + x * 4 :][: width * 4] for row in range(height)
    ]
    return RasterImage(width, height, b"".join(rows))


def pad(
    image: RasterImage,
    left: int,
    top: int,
    right: int,
    bottom: int,
    *,
    fill: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> RasterImage:
    """以固定 RGBA 颜色扩展图像边缘。"""
    if not isinstance(image, RasterImage):
        raise TypeError("image 必须是 RasterImage")
    margins = (left, top, right, bottom)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in margins):
        raise ValueError("pad 边距必须是非负整数")
    if (
        not isinstance(fill, tuple)
        or len(fill) != 4
        or any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255
            for value in fill
        )
    ):
        raise ValueError("fill 必须是四个 0..255 整数")
    width = image.width + left + right
    height = image.height + top + bottom
    output = bytearray(bytes(fill) * (width * height))
    for row in range(image.height):
        start = ((row + top) * width + left) * 4
        output[start : start + image.width * 4] = image.pixels[
            row * image.width * 4 : (row + 1) * image.width * 4
        ]
    return RasterImage(width, height, bytes(output))


def resize_nearest(image: RasterImage, width: int, height: int) -> RasterImage:
    """使用固定 nearest 规则缩放图像，避免依赖 Pillow 默认值。"""
    if not isinstance(image, RasterImage):
        raise TypeError("image 必须是 RasterImage")
    width = _validate_dimension(width, "width")
    height = _validate_dimension(height, "height")
    output = bytearray(width * height * 4)
    for target_y in range(height):
        source_y = min(image.height - 1, target_y * image.height // height)
        for target_x in range(width):
            source_x = min(image.width - 1, target_x * image.width // width)
            target_offset = (target_y * width + target_x) * 4
            output[target_offset : target_offset + 4] = _pixel(image, source_x, source_y)
    return RasterImage(width, height, bytes(output))


def _curve(value: int, gamma: float) -> int:
    """按固定 gamma 把 0..255 Alpha 映射回整数。"""
    return max(0, min(255, int(round((value / 255.0) ** gamma * 255.0))))


def extract_alpha(image: RasterImage, policy: AlphaPolicy = AlphaPolicy()) -> RasterImage:
    """把源 Alpha 提取为不透明灰度图，支持固定 gamma 和半尺寸策略。"""
    if not isinstance(image, RasterImage):
        raise TypeError("image 必须是 RasterImage")
    if not isinstance(policy, AlphaPolicy):
        raise TypeError("policy 必须是 AlphaPolicy")
    width = max(1, image.width // 2) if policy.half_size else image.width
    height = max(1, image.height // 2) if policy.half_size else image.height
    output = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            source_x = x * 2 if policy.half_size else x
            source_y = y * 2 if policy.half_size else y
            values: list[int] = []
            for offset_y in range(2 if policy.half_size else 1):
                for offset_x in range(2 if policy.half_size else 1):
                    check_x = min(image.width - 1, source_x + offset_x)
                    check_y = min(image.height - 1, source_y + offset_y)
                    values.append(_pixel(image, check_x, check_y)[3])
            value = _curve(max(values), float(policy.gamma))
            offset = (y * width + x) * 4
            output[offset : offset + 4] = bytes((value, value, value, 255))
    return RasterImage(width, height, bytes(output))


def trim_transparent(image: RasterImage, *, threshold: int = 0) -> RasterImage:
    """按 Alpha 阈值裁掉透明边界；全透明图返回 1x1 透明图。"""
    if not isinstance(image, RasterImage):
        raise TypeError("image 必须是 RasterImage")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 255:
        raise ValueError("threshold 必须在 0..255")
    visible = [
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if _pixel(image, x, y)[3] > threshold
    ]
    if not visible:
        return RasterImage(1, 1, bytes((0, 0, 0, 0)))
    min_x = min(item[0] for item in visible)
    max_x = max(item[0] for item in visible)
    min_y = min(item[1] for item in visible)
    max_y = max(item[1] for item in visible)
    return crop(image, min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def split(
    image: RasterImage, frames: tuple[SpriteFrame, ...]
) -> tuple[tuple[str, RasterImage], ...]:
    """按稳定名称裁切 JSON 图集 frame，并应用显式 scale。"""
    if not isinstance(image, RasterImage):
        raise TypeError("image 必须是 RasterImage")
    if not isinstance(frames, tuple):
        raise TypeError("frames 必须是 tuple[SpriteFrame, ...]")
    if not all(isinstance(frame, SpriteFrame) for frame in frames):
        raise TypeError("frames 必须全部是 SpriteFrame")
    names = [frame.name for frame in frames]
    if len(names) != len(set(names)):
        raise ValueError("frame name 不得重复")
    result: list[tuple[str, RasterImage]] = []
    for frame in sorted(frames, key=lambda item: item.name.encode("utf-8")):
        piece = crop(image, frame.x, frame.y, frame.width, frame.height)
        if frame.scale != 1.0:
            target_width = max(1, int(round(piece.width * frame.scale)))
            target_height = max(1, int(round(piece.height * frame.scale)))
            piece = resize_nearest(piece, target_width, target_height)
        result.append((frame.name, piece))
    return tuple(result)


__all__ = [
    "AlphaPolicy",
    "SpriteFrame",
    "crop",
    "extract_alpha",
    "pad",
    "resize_nearest",
    "split",
    "trim_transparent",
]
