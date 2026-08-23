"""美术辅助任务的不可变公共模型。

本模块只表达操作身份、规则、诊断和 CAS 产物结果。它不读取图像、PSD 或 Unity
工程，不创建输出目录，也不接受 callable、命令行或绝对输出路径，确保所有外部
副作用都停留在专用处理器和端口边界。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.artifacts import BlobRef, LogicalArtifact


class ArtOperation(str, Enum):
    """支持的美术辅助操作名称。"""

    RESIZE = "resize"
    CROP = "crop"
    PAD = "pad"
    EXTRACT_ALPHA = "extract_alpha"
    SPLIT = "split"
    ATLAS_PLAN = "atlas_plan"
    ATLAS_BUILD = "atlas_build"
    PSD_EXPORT = "psd_export"
    SCENE_BAKE = "scene_bake"
    ASSET_CHECK = "asset_check"


class ArtOutputOwnership(str, Enum):
    """美术任务输出的所有权边界。"""

    WORKSPACE = "workspace"
    CAS = "cas"


class ArtDiagnosticSeverity(str, Enum):
    """美术诊断严重级别。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def _validate_identity(value: object, field_name: str) -> str:
    """校验任务身份为不含路径和控制字符的稳定字符串。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    if any(character in value for character in ("/", "\\", "\r", "\n", "\t")):
        raise ValueError(f"{field_name} 不得包含路径分隔符或控制字符")
    return value


def _normalize_options(options: object) -> tuple[tuple[str, str], ...]:
    """校验并按 UTF-8 字节序规范化键值选项。"""
    if not isinstance(options, tuple):
        raise TypeError("options 必须是 tuple[tuple[str, str], ...]")
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in options:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("options 每项必须是 (key, value)")
        key, value = item
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("options key 必须非空且唯一")
        if not isinstance(value, str) or any(ord(character) < 0x20 for character in value):
            raise ValueError("options value 必须是不含控制字符的字符串")
        if any(character in key for character in ("/", "\\")):
            raise ValueError("options key 不得包含路径分隔符")
        seen.add(key)
        normalized.append((key, value))
    return tuple(
        sorted(normalized, key=lambda pair: (pair[0].encode("utf-8"), pair[1].encode("utf-8")))
    )


@dataclass(frozen=True, slots=True)
class ArtTaskRequest:
    """描述一次固定输入快照上的美术操作请求。

    参数：
        task_id: 不含路径的任务标识。
        operation: 固定的 ``ArtOperation``。
        input_snapshot_id: 输入快照身份。
        rule_version: 规则版本身份。
        options: 类型化为字符串对的严格选项，不包含命令或绝对输出路径。

    返回：
        无；实例冻结后可作为任务缓存身份输入。

    异常：
        类型、路径语义、重复选项或控制字符非法时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        构造过程只做内存校验，不访问文件系统或外部工具。
    """

    task_id: str
    operation: ArtOperation
    input_snapshot_id: str
    rule_version: str
    options: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """校验身份、操作和选项不变量。"""
        _validate_identity(self.task_id, "task_id")
        if not isinstance(self.operation, ArtOperation):
            raise TypeError("operation 必须是 ArtOperation")
        _validate_identity(self.input_snapshot_id, "input_snapshot_id")
        _validate_identity(self.rule_version, "rule_version")
        object.__setattr__(self, "options", _normalize_options(self.options))


@dataclass(frozen=True, slots=True)
class ArtRule:
    """保存一组带资源上限的美术规则。"""

    rule_id: str
    version: str
    options: tuple[tuple[str, str], ...]
    max_pixels: int = 16_777_216
    max_outputs: int = 10_000
    output_ownership: ArtOutputOwnership = ArtOutputOwnership.WORKSPACE

    def __post_init__(self) -> None:
        """校验规则身份、资源上限和选项。"""
        _validate_identity(self.rule_id, "rule_id")
        _validate_identity(self.version, "version")
        object.__setattr__(self, "options", _normalize_options(self.options))
        for value, field_name in (
            (self.max_pixels, "max_pixels"),
            (self.max_outputs, "max_outputs"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} 必须是正整数")
        if not isinstance(self.output_ownership, ArtOutputOwnership):
            raise TypeError("output_ownership 必须是 ArtOutputOwnership")


@dataclass(frozen=True, slots=True)
class ArtDiagnostic:
    """保存一条可排序且不含秘密的美术诊断。"""

    severity: ArtDiagnosticSeverity
    code: str
    logical_path: str
    message: str

    def __post_init__(self) -> None:
        """校验诊断字段并拒绝控制字符。"""
        if not isinstance(self.severity, ArtDiagnosticSeverity):
            raise TypeError("severity 必须是 ArtDiagnosticSeverity")
        _validate_identity(self.code, "code")
        if not isinstance(self.logical_path, str) or "\\" in self.logical_path:
            raise ValueError("logical_path 必须是 slash 路径")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message 必须是非空字符串")
        if any(ord(character) < 0x20 and character not in "\t" for character in self.message):
            raise ValueError("message 不得包含控制字符")


@dataclass(frozen=True, slots=True)
class ArtTaskResult:
    """保存美术任务生成的 CAS 产物、报告引用和诊断集合。

    参数：
        artifacts: 逻辑产物元组；按逻辑路径排序且不得重复。
        report: 已提交 CAS 的确定性 JSON 报告 Blob。
        diagnostics: 结构化诊断；按严重级别、路径和 code 排序。

    返回：
        无；结果本身不包含本地工作区路径。

    异常：
        产物、报告或诊断类型不符合约束时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        结果是冻结的审计值对象，不执行 CAS 写入。
    """

    artifacts: tuple[LogicalArtifact, ...]
    report: BlobRef
    diagnostics: tuple[ArtDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """校验并规范化产物和诊断顺序。"""
        if not isinstance(self.artifacts, tuple):
            raise TypeError("artifacts 必须是 tuple[LogicalArtifact, ...]")
        if not all(isinstance(item, LogicalArtifact) for item in self.artifacts):
            raise TypeError("artifacts 必须全部是 LogicalArtifact")
        artifact_paths = tuple(item.logical_path for item in self.artifacts)
        if len(set(artifact_paths)) != len(artifact_paths):
            raise ValueError("artifacts 逻辑路径不得重复")
        if artifact_paths != tuple(sorted(artifact_paths, key=lambda item: item.encode("utf-8"))):
            raise ValueError("artifacts 必须按逻辑路径 UTF-8 字节序排列")
        if not isinstance(self.report, BlobRef):
            raise TypeError("report 必须是 BlobRef")
        if not isinstance(self.diagnostics, tuple) or not all(
            isinstance(item, ArtDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("diagnostics 必须是 tuple[ArtDiagnostic, ...]")
        severity_order = {
            ArtDiagnosticSeverity.ERROR: 0,
            ArtDiagnosticSeverity.WARNING: 1,
            ArtDiagnosticSeverity.INFO: 2,
        }
        normalized = tuple(
            sorted(
                self.diagnostics,
                key=lambda item: (
                    severity_order[item.severity],
                    item.logical_path.encode("utf-8"),
                    item.code.encode("utf-8"),
                    item.message.encode("utf-8"),
                ),
            )
        )
        object.__setattr__(self, "diagnostics", normalized)


__all__ = [
    "ArtDiagnostic",
    "ArtDiagnosticSeverity",
    "ArtOperation",
    "ArtOutputOwnership",
    "ArtRule",
    "ArtTaskRequest",
    "ArtTaskResult",
]
