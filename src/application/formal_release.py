"""正式版本构建、协议生成、上传、验证和入口激活的端到端用例。

``FormalReleaseUseCase`` 接收已经成功的 ``BuildManifest`` 和显式产物字节，使用
``TransferObjectBuilder`` 生成旧客户端兼容传输对象，再由 ``ReleaseAssembler``、
``ProtocolOutputBuilder``、``UploadPlanFactory`` 和真实发布端口完成上传→验证→版本
状态固化→CAS 激活→确认。它不从目录猜测资源、不解析秘密、不调用 shell；缺少真实
外部端口时由组合根返回配置错误或由上层以 ``PENDING`` 记录。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from application.model import RunState
from core.artifacts import BlobRef
from core.build_records import BuildManifest
from release.assembly import ReleaseAssembler, ReleaseAssemblyItem, ReleaseAssemblyResult
from release.entries import ResourceVariant
from release.protocol_outputs import ProtocolOutputBuilder
from release.transfer import TransferObjectBuilder
from release.upload_plan import UploadItem, UploadPhase, UploadPlan, UploadPlanFactory
from release.version_layout import build_version_entry
from release.versioning import VersionAllocator, VersionReservation, VersionStream
from compatibility.line_endings import LineEnding


class FormalReleaseError(ValueError):
    """表示正式版本组装或发布输入不完整。"""


class FormalUploader(Protocol):
    """正式发布上传端口。"""

    def upload(self, plan: UploadPlan) -> object:
        """上传普通不可变对象。"""
        ...


class FormalVerifier(Protocol):
    """正式发布远端验证端口。"""

    def verify(self, bundle: object, plan: UploadPlan) -> object:
        """验证普通对象并返回绑定 Bundle 的凭证。"""
        ...


class FormalActivator(Protocol):
    """正式版本入口 CAS 激活端口。"""

    def activate(self, plan: UploadPlan, verification: object) -> object:
        """执行一次版本入口 CAS。"""
        ...


@dataclass(frozen=True, slots=True)
class FormalReleaseRequest:
    """从资源 BuildManifest 进入正式发布所需的显式请求。"""

    build_manifest: BuildManifest
    artifact_contents: Mapping[str, bytes]
    variant: ResourceVariant
    platform: str
    stream: VersionStream
    build_id: str
    request_id: str
    major: int
    minor: int
    version_entry_key: str
    file_list_base_url: str
    expected_generation: int
    expected_entry_digest: str
    line_ending: LineEnding = LineEnding.LF
    is_trunk: bool = True

    def __post_init__(self) -> None:
        """在任何外部副作用前校验正式发布请求的身份和内容边界。"""
        if not isinstance(self.build_manifest, BuildManifest):
            raise FormalReleaseError("build_manifest 必须是 BuildManifest")
        if not isinstance(self.artifact_contents, Mapping):
            raise FormalReleaseError("artifact_contents 必须是 Mapping")
        if any(
            not isinstance(path, str) or not isinstance(content, bytes)
            for path, content in self.artifact_contents.items()
        ):
            raise FormalReleaseError("artifact_contents 必须是 str 到 bytes 的 Mapping")
        if not isinstance(self.variant, ResourceVariant):
            raise FormalReleaseError("variant 必须是 ResourceVariant")
        if (
            not isinstance(self.platform, str)
            or not self.platform
            or any(character in self.platform for character in "\r\n")
        ):
            raise FormalReleaseError("platform 必须是非空且不含换行的字符串")
        if not isinstance(self.stream, VersionStream):
            raise FormalReleaseError("stream 必须是 VersionStream")
        if self.stream.version_entry_key != self.version_entry_key:
            raise FormalReleaseError("version_entry_key 必须与 stream 的入口 key 一致")
        for name, value in (("build_id", self.build_id), ("request_id", self.request_id)):
            if (
                not isinstance(value, str)
                or not value
                or any(character in value for character in "\r\n")
            ):
                raise FormalReleaseError(f"{name} 必须是非空且不含换行的字符串")
        if not isinstance(self.major, int) or isinstance(self.major, bool) or self.major < 0:
            raise FormalReleaseError("major 必须是非负整数")
        if not isinstance(self.minor, int) or isinstance(self.minor, bool) or self.minor < 0:
            raise FormalReleaseError("minor 必须是非负整数")
        if not isinstance(self.version_entry_key, str) or not self.version_entry_key:
            raise FormalReleaseError("version_entry_key 必须是非空字符串")
        if not isinstance(self.file_list_base_url, str) or not self.file_list_base_url:
            raise FormalReleaseError("file_list_base_url 必须是非空字符串")
        if (
            not isinstance(self.expected_generation, int)
            or isinstance(self.expected_generation, bool)
            or self.expected_generation < 0
        ):
            raise FormalReleaseError("expected_generation 必须是非负整数")
        if (
            not isinstance(self.expected_entry_digest, str)
            or len(self.expected_entry_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.expected_entry_digest)
        ):
            raise FormalReleaseError("expected_entry_digest 必须是小写 SHA256")
        if not isinstance(self.line_ending, LineEnding) or not isinstance(self.is_trunk, bool):
            raise FormalReleaseError("line_ending 或 is_trunk 类型非法")


@dataclass(frozen=True, slots=True)
class FormalReleaseResult:
    """正式版本用例的阶段结果和最终状态。"""

    state: RunState
    reservation: VersionReservation | None
    assembly: ReleaseAssemblyResult | None
    plan: UploadPlan | None
    upload_report: object | None
    verification: object | None
    activation: object | None
    error: str | None


class FormalReleaseUseCase:
    """把资源清单推进到可审计的正式发布终态。"""

    def __init__(
        self,
        version_allocator: VersionAllocator,
        uploader: FormalUploader,
        verifier: FormalVerifier,
        activator: FormalActivator,
    ) -> None:
        """绑定版本状态、上传、远端验证和 CAS 端口。"""
        if not all(
            callable(getattr(version_allocator, name, None))
            for name in ("allocate", "mark_ready", "prepare_activation", "confirm")
        ):
            raise TypeError("version_allocator 必须提供 allocate/mark_ready 等方法")
        for name, value, method in (
            ("uploader", uploader, "upload"),
            ("verifier", verifier, "verify"),
            ("activator", activator, "activate"),
        ):
            if not callable(getattr(value, method, None)):
                raise TypeError(f"{name} 未提供 {method} 方法")
        self._version_allocator = version_allocator
        self._uploader = uploader
        self._verifier = verifier
        self._activator = activator

    def run(self, request: FormalReleaseRequest) -> FormalReleaseResult:
        """执行版本分配、Bundle 组装、上传、验证、CAS 和 confirm。"""
        if not isinstance(request, FormalReleaseRequest):
            raise FormalReleaseError("request 必须是 FormalReleaseRequest")
        reservation: VersionReservation | None = None
        assembly: ReleaseAssemblyResult | None = None
        plan: UploadPlan | None = None
        uploaded: object | None = None
        verification: object | None = None
        try:
            reservation = self._version_allocator.allocate(
                request.stream,
                build_id=request.build_id,
                request_id=request.request_id,
                major=request.major,
                minor=request.minor,
            )
            assembly, transfer_items = self._assemble(request, reservation)
            version_entry = build_version_entry(
                request.version_entry_key,
                request.file_list_base_url,
                reservation.file_list_no,
            )
            protocol_outputs = ProtocolOutputBuilder.build(
                (assembly.manifest,), request.line_ending
            )
            protocol_items = tuple(
                UploadItem(
                    f"{reservation.file_list_no}/{output.key}",
                    output.blob,
                    output.content,
                    UploadPhase.PROTOCOL,
                )
                for output in protocol_outputs
            )
            version_blob = _blob_for_content(version_entry.content)
            plan = UploadPlanFactory.create(
                assembly.bundle.bundle_id,
                transfer_items,
                protocol_items,
                UploadItem(
                    version_entry.key,
                    version_blob,
                    version_entry.content,
                    UploadPhase.VERSION_ENTRY,
                ),
                request.expected_generation,
            )
            uploaded = self._uploader.upload(plan)
            verification = self._verifier.verify(assembly.bundle, plan)
            verification_digest = getattr(verification, "verified_objects_digest", None)
            if not isinstance(verification_digest, str):
                raise FormalReleaseError("远端验证凭证缺少 verified_objects_digest")
            reservation = self._version_allocator.mark_ready(
                reservation.reservation_id,
                bundle_id=assembly.bundle.bundle_id,
                upload_plan_id=plan.plan_id,
                verification_digest=verification_digest,
            )
            reservation = self._version_allocator.prepare_activation(
                reservation.reservation_id,
                expected_generation=request.expected_generation,
                expected_entry_digest=request.expected_entry_digest,
                replacement_entry=version_entry.content,
            )
            activation = self._activator.activate(plan, verification)
            generation = getattr(activation, "generation", None)
            digest = getattr(activation, "sha256", None)
            if not isinstance(generation, int) or not isinstance(digest, str):
                raise FormalReleaseError("CAS 激活回执缺少 generation 或 sha256")
            reservation = self._version_allocator.confirm(
                reservation.reservation_id,
                observed_generation=generation,
                observed_entry_digest=digest,
            )
            return FormalReleaseResult(
                RunState.SUCCEEDED,
                reservation,
                assembly,
                plan,
                uploaded,
                verification,
                activation,
                None,
            )
        except Exception as exc:
            return FormalReleaseResult(
                RunState.FAILED,
                reservation,
                assembly,
                plan,
                uploaded,
                verification,
                None,
                str(exc),
            )

    def build(self, request: FormalReleaseRequest) -> FormalReleaseResult:
        """正式命令 handler 使用的语义别名。"""
        return self.run(request)

    @staticmethod
    def _assemble(
        request: FormalReleaseRequest,
        reservation: VersionReservation,
    ) -> tuple[ReleaseAssemblyResult, tuple[UploadItem, ...]]:
        """把 BuildManifest 产物转换为发布快照和资源上传项。"""
        if not isinstance(request.artifact_contents, Mapping):
            raise FormalReleaseError("artifact_contents 必须是逻辑路径到 bytes 的 Mapping")
        assembly_items: list[ReleaseAssemblyItem] = []
        upload_items: list[UploadItem] = []
        for artifact in request.build_manifest.payload.artifacts:
            content = request.artifact_contents.get(artifact.logical_path)
            if not isinstance(content, bytes):
                raise FormalReleaseError(f"缺少产物字节: {artifact.logical_path}")
            transfer = TransferObjectBuilder.build(
                artifact.logical_path,
                content,
                platform=request.platform,
                is_trunk=request.is_trunk,
            )
            transfer_blob = _blob_for_content(transfer.content)
            assembly_items.append(
                ReleaseAssemblyItem(
                    artifact=artifact,
                    source_md5=transfer.source_md5,
                    transfer_blob=transfer_blob,
                    original_size=transfer.original_size,
                    transfer_size=transfer.transfer_size,
                )
            )
            upload_items.append(
                UploadItem(
                    f"{reservation.file_list_no}/{artifact.logical_path}",
                    transfer_blob,
                    transfer.content,
                    UploadPhase.RESOURCE,
                )
            )
        if not assembly_items:
            raise FormalReleaseError("正式发布 BuildManifest 不得为空")
        assembly = ReleaseAssembler.assemble(
            request.variant,
            reservation.file_list_no,
            (request.build_manifest.manifest_id,),
            tuple(assembly_items),
        )
        return assembly, tuple(upload_items)


def _blob_for_content(content: bytes) -> BlobRef:
    """为已知内容计算持久内容寻址引用。"""
    digest = hashlib.sha256(content).hexdigest()
    return BlobRef(f"blobs/{digest}", digest, len(content))


__all__ = [
    "FormalReleaseError",
    "FormalReleaseRequest",
    "FormalReleaseResult",
    "FormalReleaseUseCase",
]
