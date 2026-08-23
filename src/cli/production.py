"""CLI 的显式 production composition 和基础命令 handler。

本模块只接收 ``ProductionApplicationServices``，不从环境变量猜测 URL、可执行文件
或秘密。版本命令调用 application 的真实状态服务；外部探针从命令参数构造类型化
请求，缺少参数时返回 PENDING，避免把开发替身误当作生产能力。
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from application.composition import ProductionApplicationServices
from application.formal_release import FormalReleaseRequest
from cli.commands import CommandServices
from configuration.model import SecretRef
from core.errors import ConfigurationError
from core.manifest_codec import read_build_manifest
from integrations.probes import (
    JsonProbeEvidenceWriter,
    probe_jenkins,
    probe_object_store,
    probe_secret,
    probe_svn,
    probe_unity,
)
from ports.ci import CiJobRequest
from ports.source import SourceRef
from ports.unity import UnityBatchRequest
from release.versioning import version_stream
from release.entries import ResourceVariant
from compatibility.line_endings import LineEnding


def _required_text(args: Namespace, name: str) -> str:
    """读取 production handler 所需的非空命令参数。"""
    value = getattr(args, name, None)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"缺少参数 --{name.replace('_', '-')}")
    return value


def _version_stream(args: Namespace):
    """从 CLI 参数创建唯一版本流身份。"""
    return version_stream(
        _required_text(args, "destination_id"),
        _required_text(args, "object_store_namespace"),
        _required_text(args, "version_entry_key"),
    )


def _major_minor(args: Namespace) -> tuple[int, int]:
    """读取并验证显示版本的非负整数主次版本。"""
    major = getattr(args, "major", None)
    minor = getattr(args, "minor", None)
    if isinstance(major, bool) or not isinstance(major, int) or major < 0:
        raise ConfigurationError("--major 必须是非负整数")
    if isinstance(minor, bool) or not isinstance(minor, int) or minor < 0:
        raise ConfigurationError("--minor 必须是非负整数")
    return major, minor


def _version_preview(services: ProductionApplicationServices, args: Namespace) -> object:
    """执行正式 FileListNo 预览，不写版本状态。"""
    major, minor = _major_minor(args)
    return services.allocator.preview(_version_stream(args), major=major, minor=minor)


def _version_allocate(services: ProductionApplicationServices, args: Namespace) -> object:
    """幂等预留正式 FileListNo。"""
    major, minor = _major_minor(args)
    build_id = _required_text(args, "build_id")
    request_id = _required_text(args, "run_id")
    return services.allocator.allocate(
        _version_stream(args),
        build_id=build_id,
        request_id=request_id,
        major=major,
        minor=minor,
    )


def _required_path(args: Namespace, name: str) -> Path:
    """读取正式构建所需的绝对普通路径参数。"""
    value = _required_text(args, name)
    path = Path(value)
    if not path.is_absolute():
        raise ConfigurationError(f"--{name.replace('_', '-')} 必须是绝对路径")
    return path


def _formal_release(services: ProductionApplicationServices, args: Namespace) -> object:
    """从已验证 BuildManifest 和隔离产物根执行完整正式版本事务。"""
    manifest_path = _required_path(args, "manifest_path")
    artifact_root = _required_path(args, "artifact_root")
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        raise ConfigurationError("--artifact-root 必须是普通目录")
    manifest = read_build_manifest(manifest_path)
    contents: dict[str, bytes] = {}
    resolved_root = artifact_root.resolve()
    for artifact in manifest.payload.artifacts:
        path = (artifact_root / artifact.logical_path).resolve(strict=False)
        if resolved_root not in path.parents or path.is_symlink() or not path.is_file():
            raise ConfigurationError(f"正式版本缺少隔离产物: {artifact.logical_path}")
        contents[artifact.logical_path] = path.read_bytes()
    major, minor = _major_minor(args)
    variant = ResourceVariant(_required_text(args, "variant"))
    entry_key = _required_text(args, "version_entry_key")
    return services.formal_release.build(
        FormalReleaseRequest(
            build_manifest=manifest,
            artifact_contents=contents,
            variant=variant,
            platform=_required_text(args, "platform"),
            stream=_version_stream(args),
            build_id=_required_text(args, "build_id"),
            request_id=_required_text(args, "run_id"),
            major=major,
            minor=minor,
            version_entry_key=entry_key,
            file_list_base_url=_required_text(args, "file_list_base_url"),
            expected_generation=_required_int(args, "expected_generation"),
            expected_entry_digest=_required_text(args, "expected_entry_digest"),
            line_ending=LineEnding.LF,
        )
    )


def _required_int(args: Namespace, name: str) -> int:
    """读取正式事务所需的非负或正整数参数。"""
    value = getattr(args, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f"缺少或非法参数 --{name.replace('_', '-')}")
    return value


def _external_probe(services: ProductionApplicationServices, args: Namespace) -> object:
    """根据显式参数执行 SVN、Unity、Jenkins、Secrets 和 CDN 探针。"""
    svn_url = getattr(args, "svn_url", None)
    svn_revision = getattr(args, "svn_revision", None) or "HEAD"
    svn_destination = getattr(args, "probe_destination", None)
    source = SourceRef("svn", svn_url, svn_revision) if isinstance(svn_url, str) else None
    destination = Path(svn_destination) if isinstance(svn_destination, str) else None

    unity_request = None
    executable = getattr(args, "unity_executable", None)
    project = getattr(args, "unity_project", None)
    method = getattr(args, "unity_method", None)
    log_path = getattr(args, "unity_log", None)
    if all(isinstance(value, str) for value in (executable, project, method, log_path)):
        unity_request = UnityBatchRequest(
            Path(executable),
            Path(project),
            method,
            (),
            Path(log_path),
            300.0,
        )

    jenkins_job = getattr(args, "jenkins_job", None)
    jenkins_request = (
        CiJobRequest(jenkins_job, (), _required_text(args, "run_id"))
        if isinstance(jenkins_job, str)
        else None
    )
    secret_value = getattr(args, "secret_ref", None)
    secret_reference = SecretRef(secret_value) if isinstance(secret_value, str) else None
    evidence = services.probes.run(
        (
            lambda: probe_svn(services.source_provider, source, destination),
            lambda: probe_unity(services.unity_runner, unity_request),
            lambda: probe_jenkins(services.ci_client, jenkins_request),
            lambda: probe_secret(services.secret_provider, secret_reference),
            lambda: probe_object_store(
                services.object_store,
                getattr(args, "object_store_probe_key", None),
            ),
        ),
        writer=(
            JsonProbeEvidenceWriter(Path(args.evidence_path))
            if isinstance(getattr(args, "evidence_path", None), str)
            else None
        ),
    )
    return evidence


def create_production_command_services(
    services: ProductionApplicationServices,
) -> CommandServices:
    """为一次 production application composition 创建 CLI handler 注册表。"""
    if not isinstance(services, ProductionApplicationServices):
        raise TypeError("services 必须是 ProductionApplicationServices")
    return CommandServices(
        release_build=lambda args: _formal_release(services, args),
        release_version_preview=lambda args: _version_preview(services, args),
        release_version_allocate=lambda args: _version_allocate(services, args),
        external_probe=lambda args: _external_probe(services, args),
    )


__all__ = ["create_production_command_services"]
