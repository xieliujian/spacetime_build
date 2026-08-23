"""验证美术辅助任务的不可变模型和输出边界。"""

from __future__ import annotations

import hashlib

import pytest

from art.model import (
    ArtDiagnostic,
    ArtDiagnosticSeverity,
    ArtOperation,
    ArtOutputOwnership,
    ArtRule,
    ArtTaskRequest,
    ArtTaskResult,
)
from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact


def _artifact(path: str = "ui/icon.png") -> LogicalArtifact:
    """创建测试用逻辑产物。"""
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return LogicalArtifact(
        logical_path=path,
        kind=ArtifactKind.FILE,
        blob=BlobRef(f"blobs/{digest}", digest, 1),
        dependencies=(),
        subpackage_ids=frozenset(),
        metadata=ArtifactMetadata("art", "r1", "tool1", ()),
    )


def test_request_normalizes_options_and_rejects_path_like_identity() -> None:
    """请求选项应确定性排序，任务身份不得携带路径语义。"""
    request = ArtTaskRequest(
        "atlas-ui",
        ArtOperation.ATLAS_PLAN,
        "snapshot-1",
        "rules-1",
        (("z", "1"), ("a", "2")),
    )

    assert request.options == (("a", "2"), ("z", "1"))
    with pytest.raises(ValueError):
        ArtTaskRequest("../unsafe", ArtOperation.CROP, "snapshot-1", "rules-1", ())


def test_rule_and_result_are_immutable_and_diagnostics_are_stable() -> None:
    """规则、诊断和结果应只保存结构化、可排序的公开值。"""
    rule = ArtRule(
        "image-default",
        "1",
        (("resampler", "nearest"),),
        max_pixels=1024,
        max_outputs=4,
    )
    diagnostic = ArtDiagnostic(
        ArtDiagnosticSeverity.WARNING,
        "TRIM_EMPTY",
        "ui/icon.png",
        "alpha 边界为空",
    )
    report_digest = hashlib.sha256(b"report").hexdigest()
    result = ArtTaskResult(
        artifacts=(_artifact(),),
        report=BlobRef(f"blobs/{report_digest}", report_digest, 6),
        diagnostics=(diagnostic,),
    )

    assert rule.output_ownership is ArtOutputOwnership.WORKSPACE
    assert result.diagnostics == (diagnostic,)
    with pytest.raises((AttributeError, TypeError)):
        result.diagnostics = ()  # type: ignore[misc]
