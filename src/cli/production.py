"""CLI 的显式 production composition 和基础命令 handler。

本模块只接收 ``ProductionApplicationServices``，不从环境变量猜测 URL、可执行文件
或秘密。版本命令调用 application 的真实状态服务；外部探针从命令参数构造类型化
请求，缺少参数时返回 PENDING，避免把开发替身误当作生产能力。
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import cast

from application.composition import ProductionApplicationServices
from application.build_resources import BuildResourcesUseCase
from application.formal_release import FormalReleaseRequest
from application.model import ApplicationRequest, RunState
from application.operations import RunOperations
from application.package_client import PackageClientUseCase
from application.publish_release import PublishReleaseUseCase
from application.records import RunRecord
from cli.commands import CommandServices
from configuration.model import SecretRef
from core.errors import ConfigurationError
from core.manifest_codec import read_build_manifest, write_build_manifest
from core.platforms import BuildPlatform
from core.tasks import BuildContext
from integrations.probes import (
    JsonProbeEvidenceWriter,
    probe_jenkins,
    probe_object_store,
    probe_secret,
    probe_svn,
    probe_unity,
)
from compatibility.legacy_acceptance import LegacyDualRunService
from ports.ci import CiJobRequest
from ports.source import SourceRef
from ports.unity import UnityBatchRequest
from package.model import PackageRequest
from release.activation_codec import read_verified_release_bundle
from release.activator import ReleaseActivator
from release.bundle_codec import read_release_bundle
from release.upload_plan_codec import read_upload_plan
from release.uploader import ReleaseObjectUploader
from release.versioning import version_stream
from release.entries import ResourceVariant
from compatibility.line_endings import LineEnding
from application.composition import ResourceTaskProvider, RunResumeAction


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


def _required_service(services: ProductionApplicationServices, name: str) -> object:
    """读取显式装配的可选业务服务，缺失时返回稳定配置错误。"""
    value = getattr(services, name, None)
    if value is None:
        raise ConfigurationError(f"production 服务未装配: {name}")
    return value


def _resource_build(
    services: ProductionApplicationServices,
    args: Namespace,
    *,
    force_dry_run: bool = False,
) -> object:
    """由统一 composition provider 创建任务并调用资源构建用例。"""
    use_case = cast(BuildResourcesUseCase, _required_service(services, "resource_build"))
    provider = cast(ResourceTaskProvider, _required_service(services, "resource_task_provider"))
    platform_text = _required_text(args, "platform")
    try:
        platform = BuildPlatform(platform_text)
    except ValueError as exc:
        raise ConfigurationError(f"不支持的构建平台: {platform_text}") from exc
    revision = _required_text(args, "revision")
    request = ApplicationRequest(
        _required_text(args, "run_id"),
        _required_text(args, "profile"),
        revision,
        platform,
        force_dry_run or bool(getattr(args, "dry_run", False)),
    )
    context = BuildContext(
        _required_text(args, "request_digest"),
        revision,
        _required_text(args, "toolchain_digest"),
        getattr(args, "baseline_id", None),
        _required_int_or_default(args, "schema_version", 1),
    )
    tasks = provider(args, request, context)
    result = cast(object, use_case.run(request, context, tasks))
    manifest = getattr(result, "manifest", None)
    manifest_output = getattr(args, "manifest_output", None)
    if manifest is not None and isinstance(manifest_output, str):
        write_build_manifest(manifest, _required_absolute_path_value(manifest_output, "manifest_output"))
    return result


def _resource_plan(services: ProductionApplicationServices, args: Namespace) -> object:
    """执行只规划不写产物的资源 plan 命令。"""
    return _resource_build(services, args, force_dry_run=True)


def _required_int_or_default(args: Namespace, name: str, default: int) -> int:
    """读取可选的非负整数配置，缺失时使用固定默认值。"""
    value = getattr(args, name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f"--{name.replace('_', '-')} 必须是非负整数")
    return value


def _required_absolute_path_value(value: str, name: str) -> Path:
    """校验字符串是绝对路径，并拒绝空值。"""
    path = Path(value)
    if not path.is_absolute():
        raise ConfigurationError(f"--{name.replace('_', '-')} 必须是绝对路径")
    return path


def _release_upload(services: ProductionApplicationServices, args: Namespace) -> object:
    """读取受信 UploadPlan 并执行一次不可变对象上传。"""
    plan = read_upload_plan(_required_path(args, "upload_plan_path"))
    uploader = cast(ReleaseObjectUploader, _required_service(services, "uploader"))
    return uploader.upload(plan)


def _release_activate(services: ProductionApplicationServices, args: Namespace) -> object:
    """读取 UploadPlan 和验证凭证并执行一次入口 CAS 激活。"""
    plan = read_upload_plan(_required_path(args, "upload_plan_path"))
    verification = read_verified_release_bundle(_required_path(args, "verification_path"))
    activator = cast(ReleaseActivator, _required_service(services, "activator"))
    return activator.activate(plan, verification)


def _release_publish(services: ProductionApplicationServices, args: Namespace) -> object:
    """读取 Bundle/UploadPlan 并调用阶段化发布用例。"""
    bundle = read_release_bundle(_required_path(args, "bundle_path"))
    plan = read_upload_plan(_required_path(args, "upload_plan_path"))
    publish = cast(PublishReleaseUseCase, _required_service(services, "publish_release"))
    return publish.run(bundle, plan)


def _release_rollback(services: ProductionApplicationServices, args: Namespace) -> object:
    """使用已验证历史计划执行一次不重建资源的回滚激活。"""
    current_bundle_id = _required_text(args, "current_bundle_id")
    target_bundle_id = _required_text(args, "target_bundle_id")
    plan = read_upload_plan(_required_path(args, "upload_plan_path"))
    verification = read_verified_release_bundle(_required_path(args, "verification_path"))
    operations = cast(RunOperations, _required_service(services, "run_operations"))
    activator = cast(ReleaseActivator, _required_service(services, "activator"))
    return operations.rollback(
        current_bundle_id,
        target_bundle_id,
        plan,
        verification,
        activator,
    )


def _package_build(services: ProductionApplicationServices, args: Namespace) -> object:
    """读取验证凭证，构造 PackageRequest 并分派到统一包体用例。"""
    package_client = cast(PackageClientUseCase, _required_service(services, "package_client"))
    try:
        platform = BuildPlatform(_required_text(args, "platform"))
    except ValueError as exc:
        raise ConfigurationError("--platform 不是受支持的包体平台") from exc
    source_revision = getattr(args, "source_revision", None) or getattr(args, "revision", None)
    if not isinstance(source_revision, str) or not source_revision:
        raise ConfigurationError("缺少参数 --source-revision 或 --revision")
    request = PackageRequest(
        platform,
        source_revision,
        _required_text(args, "release_bundle_id"),
        _required_text(args, "unity_version"),
        _required_text(args, "application_id"),
        _required_text(args, "version_name"),
        _required_int(args, "version_code"),
        _required_text(args, "package_profile"),
    )
    verification = read_verified_release_bundle(_required_path(args, "verification_path"))
    return package_client.run(request, verification)


def _compatibility_dual_run(
    services: ProductionApplicationServices,
    args: Namespace,
) -> object:
    """将 CLI 隔离路径交给旧系统双跑服务。"""
    line_ending_text = getattr(args, "line_ending", "lf")
    line_ending = LineEnding.CRLF if line_ending_text == "crlf" else LineEnding.LF
    acceptance = cast(LegacyDualRunService, _required_service(services, "legacy_acceptance"))
    return acceptance.run(
        isolation_root=_required_absolute_path_value(
            _required_text(args, "isolation_root"), "isolation_root"
        ),
        source_snapshot=_required_absolute_path_value(
            _required_text(args, "source_snapshot"), "source_snapshot"
        ),
        historical_output=_required_absolute_path_value(
            _required_text(args, "historical_output"), "historical_output"
        ),
        candidate_output=_required_absolute_path_value(
            _required_text(args, "candidate_output"), "candidate_output"
        ),
        runner=services.legacy_runner,
        line_ending=line_ending,
    )


def _run_status(services: ProductionApplicationServices, args: Namespace) -> object:
    """从持久运行记录仓库读取当前状态。"""
    run_id = _required_text(args, "run_id")
    record, _ = services.run_record_repository.read_current(run_id)
    return record


def _run_cancel(services: ProductionApplicationServices, args: Namespace) -> object:
    """以持久 CAS 记录实现幂等取消请求。"""
    run_id = _required_text(args, "run_id")
    record, generation = services.run_record_repository.read_current(run_id)
    operation = services.run_operations.cancel(run_id, record.state)
    if operation.idempotent:
        return operation
    next_record = RunRecord(
        record.run_id,
        RunState.CANCEL_REQUESTED,
        record.request_digest,
        record.artifact_ids,
        record.error,
    )
    services.run_record_repository.write(
        next_record,
        expected_generation=generation,
        previous_state=record.state,
    )
    return operation


def _run_resume(services: ProductionApplicationServices, args: Namespace) -> object:
    """调用一次显式恢复 action，并将返回的 RunRecord 以 CAS 持久化。"""
    action = cast(RunResumeAction, _required_service(services, "run_resume_action"))
    run_id = _required_text(args, "run_id")
    record, generation = services.run_record_repository.read_current(run_id)
    operation = services.run_operations.resume(
        run_id,
        record.state,
        lambda: action(record, args),
    )
    if not isinstance(operation.value, RunRecord):
        raise ConfigurationError("run_resume_action 必须返回 RunRecord")
    receipt = services.run_record_repository.write(
        operation.value,
        expected_generation=generation,
        previous_state=record.state,
    )
    return {"operation": operation, "receipt": receipt}


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
    if (
        isinstance(executable, str)
        and isinstance(project, str)
        and isinstance(method, str)
        and isinstance(log_path, str)
    ):
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
        plan=lambda args: _resource_plan(services, args),
        resource_build=lambda args: _resource_build(services, args),
        release_publish=lambda args: _release_publish(services, args),
        release_build=lambda args: _formal_release(services, args),
        release_version_preview=lambda args: _version_preview(services, args),
        release_version_allocate=lambda args: _version_allocate(services, args),
        release_upload=lambda args: _release_upload(services, args),
        release_activate=lambda args: _release_activate(services, args),
        release_rollback=lambda args: _release_rollback(services, args),
        external_probe=lambda args: _external_probe(services, args),
        compatibility_dual_run=lambda args: _compatibility_dual_run(services, args),
        package_build=lambda args: _package_build(services, args),
        run_status=lambda args: _run_status(services, args),
        run_cancel=lambda args: _run_cancel(services, args),
        run_resume=lambda args: _run_resume(services, args),
    )


__all__ = ["create_production_command_services"]
