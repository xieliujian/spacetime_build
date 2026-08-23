"""验证不依赖 Pillow 的确定性 RGBA 图像操作。"""

from __future__ import annotations

import pytest

from art.image_codec import RasterImage
from art.image_ops import (
    AlphaPolicy,
    SpriteFrame,
    crop,
    extract_alpha,
    pad,
    resize_nearest,
    split,
    trim_transparent,
)


def _image() -> RasterImage:
    """创建 2x2 RGBA 测试像素。"""
    return RasterImage(
        2,
        2,
        bytes(
            (
                255,
                0,
                0,
                255,
                0,
                255,
                0,
                128,
                0,
                0,
                255,
                0,
                0,
                0,
                0,
                0,
            )
        ),
    )


def test_crop_pad_resize_and_trim_are_pure_and_deterministic() -> None:
    """基础尺寸变换应返回新对象并保持明确像素规则。"""
    image = _image()
    cropped = crop(image, 0, 0, 1, 2)
    padded = pad(cropped, 1, 0, 0, 1, fill=(1, 2, 3, 4))
    resized = resize_nearest(padded, 4, 6)
    trimmed = trim_transparent(resized, threshold=0)

    assert cropped.width == 1 and cropped.height == 2
    assert padded.width == 2 and padded.height == 3
    assert resized.width == 4 and resized.height == 6
    assert trimmed.width == 4 and trimmed.height == 6
    assert image.width == 2 and image.height == 2


def test_alpha_and_split_validate_bounds_rotation_and_names() -> None:
    """Alpha 提取和拆图必须拒绝不可复现或越界输入。"""
    image = _image()
    alpha = extract_alpha(image, AlphaPolicy(gamma=1.0, half_size=True))
    assert (alpha.width, alpha.height) == (1, 1)
    assert alpha.pixels[3] == 255

    frames = split(
        image,
        (
            SpriteFrame("b", 1, 0, 1, 1),
            SpriteFrame("a", 0, 0, 1, 1),
        ),
    )
    assert tuple(name for name, _ in frames) == ("a", "b")
    with pytest.raises(ValueError):
        split(image, (SpriteFrame("rotated", 0, 0, 1, 1, rotated=True),))
    with pytest.raises(ValueError):
        crop(image, 0, 0, 3, 1)
