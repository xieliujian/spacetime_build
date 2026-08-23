"""PSD 图层映射和导出边界。

本模块不在导入时加载 ``psd_tools``。调用导出时才解析 PSD，按显式图层映射和稳定
顺序写入隔离输出根；缺少可选依赖、图层缺失、重复输出或解析失败均转换为明确的
``ToolExecutionError`` / ``ArtifactValidationError``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from art.image_codec import ImageCodec
from core.errors import ArtifactValidationError, ToolExecutionError


def _logical_path(value: object, field_name: str) -> str:
    """校验 slash 分隔的逻辑输出路径。"""
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise ValueError(f"{field_name} 必须是安全逻辑路径")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field_name} 含非法路径段")
    return value


@dataclass(frozen=True, slots=True)
class PsdExportRequest:
    """描述一次 PSD 可见图层到 PNG 逻辑路径的映射。"""

    source_path: Path
    output_root: Path
    layer_mapping: tuple[tuple[str, str], ...]
    max_layers: int = 1_000

    def __post_init__(self) -> None:
        """校验输入文件、输出根和映射唯一性。"""
        if not isinstance(self.source_path, Path) or not self.source_path.is_absolute():
            raise ValueError("source_path 必须是绝对 Path")
        if self.source_path.is_symlink() or not self.source_path.is_file():
            raise FileNotFoundError(self.source_path)
        if not isinstance(self.output_root, Path) or not self.output_root.is_absolute():
            raise ValueError("output_root 必须是绝对 Path")
        if not isinstance(self.layer_mapping, tuple) or not self.layer_mapping:
            raise ValueError("layer_mapping 必须是非空 tuple")
        normalized = []
        outputs: set[str] = set()
        for item in self.layer_mapping:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("layer_mapping 每项必须是 (layer, path)")
            layer_name, output_path = item
            if not isinstance(layer_name, str) or not layer_name.strip():
                raise ValueError("图层名称必须非空")
            normalized_path = _logical_path(output_path, "output_path")
            if normalized_path in outputs:
                raise ValueError("PSD 输出路径不得重复")
            outputs.add(normalized_path)
            normalized.append((layer_name, normalized_path))
        object.__setattr__(
            self,
            "layer_mapping",
            tuple(sorted(normalized, key=lambda item: item[1].encode("utf-8"))),
        )
        if (
            isinstance(self.max_layers, bool)
            or not isinstance(self.max_layers, int)
            or self.max_layers <= 0
        ):
            raise ValueError("max_layers 必须是正整数")


@dataclass(frozen=True, slots=True)
class PsdExportResult:
    """保存 PSD 导出的逻辑路径和实际普通文件路径。"""

    outputs: tuple[tuple[str, Path], ...]


class PsdExporter:
    """惰性使用 psd-tools 导出显式可见图层。"""

    def export(self, request: PsdExportRequest) -> PsdExportResult:
        """读取 PSD、渲染映射图层并写入隔离输出根。"""
        if not isinstance(request, PsdExportRequest):
            raise TypeError("request 必须是 PsdExportRequest")
        try:
            from psd_tools import PSDImage
        except ImportError as exc:
            raise ToolExecutionError("PSD 导出需要安装受控版本 psd-tools") from exc
        try:
            psd = PSDImage.open(request.source_path)
            layers = list(psd.descendants())
            if len(layers) > request.max_layers:
                raise ArtifactValidationError("PSD 图层数超过上限")
            by_name = {
                str(layer.name): layer for layer in layers if getattr(layer, "visible", True)
            }
            if len(by_name) != len([layer for layer in layers if getattr(layer, "visible", True)]):
                raise ArtifactValidationError("PSD 可见图层名称重复")
            codec = ImageCodec()
            outputs: list[tuple[str, Path]] = []
            for layer_name, logical_path in request.layer_mapping:
                layer = by_name.get(layer_name)
                if layer is None:
                    raise ArtifactValidationError(f"PSD 缺少可见图层: {layer_name}")
                output_path = request.output_root / logical_path
                resolved = output_path.resolve(strict=False)
                root = request.output_root.resolve(strict=False)
                if resolved == root or root not in resolved.parents:
                    raise ArtifactValidationError("PSD 输出越出 output_root")
                composite = layer.composite()
                rgba = composite.convert("RGBA")
                from art.image_codec import RasterImage

                codec.write_png(RasterImage(rgba.width, rgba.height, rgba.tobytes()), output_path)
                outputs.append((logical_path, output_path))
            return PsdExportResult(tuple(outputs))
        except (ArtifactValidationError, ToolExecutionError):
            raise
        except Exception as exc:
            raise ToolExecutionError("PSD 解析或图层导出失败") from exc


__all__ = ["PsdExportRequest", "PsdExportResult", "PsdExporter"]
