"""美术任务的确定性 JSON 报告和 CAS 提交辅助。"""

from __future__ import annotations

from dataclasses import dataclass

from art.model import ArtDiagnostic
from core.artifacts import BlobRef
from core.manifest_codec import canonical_json_bytes
from resource.blob_committer import BlobCommitter


@dataclass(frozen=True, slots=True)
class ArtReport:
    """保存请求身份、工具版本和诊断摘要。"""

    task_id: str
    input_snapshot_id: str
    rule_version: str
    tool_versions: tuple[tuple[str, str], ...]
    diagnostics: tuple[ArtDiagnostic, ...]

    def to_payload(self) -> dict[str, object]:
        """返回不含本地路径的规范报告 payload。"""
        return {
            "diagnostics": [
                {
                    "code": item.code,
                    "logical_path": item.logical_path,
                    "message": item.message,
                    "severity": item.severity.value,
                }
                for item in self.diagnostics
            ],
            "input_snapshot_id": self.input_snapshot_id,
            "rule_version": self.rule_version,
            "task_id": self.task_id,
            "tool_versions": [list(item) for item in self.tool_versions],
        }

    def to_json_bytes(self) -> bytes:
        """返回确定性 UTF-8 JSON 字节。"""
        return canonical_json_bytes(self.to_payload())

    def commit(self, blob_committer: BlobCommitter) -> BlobRef:
        """将报告字节提交 CAS 并返回不含本地路径的 BlobRef。"""
        if not isinstance(blob_committer, BlobCommitter):
            raise TypeError("blob_committer 必须是 BlobCommitter")
        return blob_committer.commit_bytes(self.to_json_bytes())


__all__ = ["ArtReport"]
