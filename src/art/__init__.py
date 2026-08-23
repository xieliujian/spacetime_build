"""美术辅助能力的纯 Python 任务组件。"""

from art.image_codec import ImageCodec, MetadataPolicy, RasterImage, pillow_version
from art.atlas import AtlasBuildResult, AtlasBuilder, AtlasPlan, AtlasPlanner, AtlasRule
from art.asset_checks import (
    AssetCheckResult,
    AssetCheckRule,
    AssetChecker,
    AssetFixAction,
    AssetFixPlan,
)
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
from art.model import (
    ArtDiagnostic,
    ArtDiagnosticSeverity,
    ArtOperation,
    ArtOutputOwnership,
    ArtRule,
    ArtTaskRequest,
    ArtTaskResult,
)
from art.psd import PsdExportRequest, PsdExportResult, PsdExporter
from art.report import ArtReport
from art.scene_bake import SceneBakeRequest, SceneBakeResult, SceneBakeTask
from art.task import ArtOperationExecutor, ArtTask

__all__ = [
    "AlphaPolicy",
    "AtlasBuildResult",
    "AtlasBuilder",
    "AtlasPlan",
    "AtlasPlanner",
    "AtlasRule",
    "ArtDiagnostic",
    "ArtDiagnosticSeverity",
    "ArtOperation",
    "ArtOutputOwnership",
    "ArtRule",
    "ArtTaskRequest",
    "ArtTaskResult",
    "ArtOperationExecutor",
    "ArtReport",
    "ArtTask",
    "AssetCheckResult",
    "AssetCheckRule",
    "AssetChecker",
    "AssetFixAction",
    "AssetFixPlan",
    "ImageCodec",
    "MetadataPolicy",
    "PsdExportRequest",
    "PsdExportResult",
    "PsdExporter",
    "RasterImage",
    "SpriteFrame",
    "crop",
    "extract_alpha",
    "pad",
    "pillow_version",
    "resize_nearest",
    "split",
    "SceneBakeRequest",
    "SceneBakeResult",
    "SceneBakeTask",
    "trim_transparent",
]
