"""验证 CLI production composition 能调用版本服务和外部探针套件。"""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import hashlib

import pytest

from application.composition import ProductionApplicationServices
from application.build_resources import BuildResourcesUseCase
from application.model import RunState
from application.records import RunRecord
from cli.commands import CommandDispatcher
from cli.parser import build_parser
from cli.production import create_production_command_services
from core.errors import ConfigurationError
from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from core.tasks import BuildContext, TaskPlan, TaskResult, TaskSpec
from integrations.factory import IntegrationFactory
from release.activation import bind_verified_release_bundle
from release.upload_plan import UploadItem, UploadPhase, UploadPlanFactory
from release.upload_plan_codec import write_upload_plan
from release.activation_codec import write_verified_release_bundle


class _Runner:
    """不启动真实进程的 production composition 测试替身。"""

    def run(self, request: object, cancellation: object = None) -> object:
        """拒绝意外的外部执行。"""
        raise AssertionError("测试不应启动外部进程")


def _services(tmp_path: Path) -> ProductionApplicationServices:
    """创建本地对象存储和显式 application composition。"""
    integrations = IntegrationFactory.local(_Runner(), tmp_path / "objects")
    return ProductionApplicationServices.create(integrations, tmp_path / "versions")


def test_production_composition_wires_version_preview_and_allocate(tmp_path: Path) -> None:
    """CLI 版本命令应进入同一个 VersionAllocator。"""
    services = create_production_command_services(_services(tmp_path))
    preview_args = build_parser().parse_args(
        [
            "release",
            "version",
            "preview",
            "--destination-id",
            "prod",
            "--object-store-namespace",
            "cdn",
            "--version-entry-key",
            "windows/entry.json",
            "--major",
            "1",
            "--minor",
            "2",
        ]
    )
    preview = CommandDispatcher(services).dispatch(preview_args)
    assert preview.file_list_no == 1

    allocate_args = build_parser().parse_args(
        [
            "release",
            "version",
            "allocate",
            "--destination-id",
            "prod",
            "--object-store-namespace",
            "cdn",
            "--version-entry-key",
            "windows/entry.json",
            "--major",
            "1",
            "--minor",
            "2",
            "--build-id",
            "build-1",
            "--run-id",
            "run-1",
        ]
    )
    allocated = CommandDispatcher(services).dispatch(allocate_args)
    assert allocated.file_list_no == 1
    assert allocated.status.value == "reserved"


def test_production_external_probe_without_explicit_inputs_is_pending(tmp_path: Path) -> None:
    """没有真实 URL、工具和秘密引用时，探针应返回可审计 PENDING。"""
    services = create_production_command_services(_services(tmp_path))
    args = build_parser().parse_args(["external", "probe"])

    evidence = CommandDispatcher(services).dispatch(args)

    assert len(evidence) == 5
    assert {item.status.value for item in evidence} == {"pending"}


def test_production_release_upload_handler_uses_strict_upload_plan_codec(tmp_path: Path) -> None:
    """Given 上传计划文件，When 执行 release upload，Then 调用注入的上传器。"""
    content = b"resource"
    entry = b"entry"
    content_digest = hashlib.sha256(content).hexdigest()
    entry_digest = hashlib.sha256(entry).hexdigest()
    plan = UploadPlanFactory.create(
        "a" * 64,
        (
            UploadItem(
                "1/data.bin",
                BlobRef("blobs/" + content_digest, content_digest, len(content)),
                content,
                UploadPhase.RESOURCE,
            ),
        ),
        (),
        UploadItem(
            "version/entry.json",
            BlobRef("blobs/" + entry_digest, entry_digest, len(entry)),
            entry,
            UploadPhase.VERSION_ENTRY,
        ),
        0,
    )
    plan_path = tmp_path / "upload-plan.json"
    write_upload_plan(plan, plan_path)
    calls: list[str] = []

    class _Uploader:
        """记录 standalone upload handler 的调用。"""

        def upload(self, received: object, *, cancellation: object = None) -> object:
            """保存计划身份并返回测试回执。"""
            del cancellation
            calls.append(received.plan_id)
            return {"uploaded": True}

    services = replace(_services(tmp_path), uploader=_Uploader())
    result = CommandDispatcher(create_production_command_services(services)).dispatch(
        build_parser().parse_args(["release", "upload", "--upload-plan-path", str(plan_path)])
    )

    assert result == {"uploaded": True}
    assert calls == [plan.plan_id]


def test_production_run_status_and_cancel_use_persistent_record_repository(tmp_path: Path) -> None:
    """Given 对象存储中的运行记录，When status/cancel，Then 读取并追加状态记录。"""
    services = _services(tmp_path)
    original = RunRecord("run-1", RunState.RUNNING, "request-digest", (), None)
    services.run_record_repository.write(original, expected_generation=0)
    command_services = create_production_command_services(services)

    status = CommandDispatcher(command_services).dispatch(
        build_parser().parse_args(["run", "status", "--run-id", "run-1"])
    )
    cancelled = CommandDispatcher(command_services).dispatch(
        build_parser().parse_args(["run", "cancel", "--run-id", "run-1"])
    )
    latest, _ = services.run_record_repository.read_current("run-1")

    assert status.state is RunState.RUNNING
    assert cancelled.state is RunState.CANCEL_REQUESTED
    assert latest.state is RunState.CANCEL_REQUESTED


def test_production_unconfigured_business_command_fails_as_configuration_error(
    tmp_path: Path,
) -> None:
    """Given 未注入资源服务，When 执行 resource build，Then 明确返回配置错误。"""
    services = create_production_command_services(_services(tmp_path))
    args = build_parser().parse_args(
        ["resource", "build", "--platform", "android", "--revision", "123", "--profile", "release"]
    )

    with pytest.raises(ConfigurationError, match="resource"):
        CommandDispatcher(services).dispatch(args)


def test_production_resource_build_uses_injected_task_provider_and_writes_manifest(
    tmp_path: Path,
) -> None:
    """Given 资源 task provider，When 执行 resource build，Then 生成确定性清单文件。"""

    class _Task:
        """构造不访问外部工具的最小资源任务。"""

        name = "config"

        def plan(self, context: BuildContext) -> TaskPlan:
            """返回一个无依赖、单输出的任务计划。"""
            del context
            return TaskPlan(
                TaskSpec("config", (), frozenset({"config.bin"}), "test-1", ()),
                "input-digest",
                "config-digest",
            )

        def execute(self, context: BuildContext, inputs: object) -> TaskResult:
            """返回固定的内存产物。"""
            del context, inputs
            return TaskResult(
                (
                    LogicalArtifact(
                        "config.bin",
                        ArtifactKind.FILE,
                        BlobRef("blobs/" + "3" * 64, "3" * 64, 1),
                        (),
                        frozenset(),
                        ArtifactMetadata("config", "123", "toolchain", ()),
                    ),
                )
            )

    output = tmp_path / "manifest.json"

    def provider(args: object, request: object, context: BuildContext) -> dict[str, _Task]:
        """返回显式注册的单任务映射。"""
        del args, request, context
        return {"config": _Task()}

    services = replace(
        _services(tmp_path),
        resource_build=BuildResourcesUseCase(),
        resource_task_provider=provider,
    )
    args = build_parser().parse_args(
        [
            "resource",
            "build",
            "--platform",
            "android",
            "--revision",
            "123",
            "--profile",
            "release",
            "--run-id",
            "run-resource",
            "--request-digest",
            "request",
            "--toolchain-digest",
            "toolchain",
            "--manifest-output",
            str(output),
        ]
    )

    result = CommandDispatcher(create_production_command_services(services)).dispatch(args)

    assert result.state is RunState.SUCCEEDED
    assert result.manifest is not None
    assert output.is_file()


def test_production_package_build_reads_verification_credential(tmp_path: Path) -> None:
    """Given 已验证 Bundle 文件，When 执行 package build，Then 调用注入的包体用例。"""
    verification = bind_verified_release_bundle(
        bundle_id="a" * 64,
        required_objects_digest="b" * 64,
        verified_objects_digest="b" * 64,
    )
    verification_path = tmp_path / "verification.json"
    write_verified_release_bundle(verification, verification_path)
    calls: list[str] = []

    class _PackageClient:
        """记录 package request 的测试 application 用例。"""

        def run(self, request: object, received: object, *, contents: object = None) -> object:
            """保存请求和验证凭证身份。"""
            del contents
            calls.append(f"{request.request_id}:{received.bundle_id}")
            return {"state": "succeeded"}

    services = replace(_services(tmp_path), package_client=_PackageClient())
    args = build_parser().parse_args(
        [
            "package",
            "build",
            "--platform",
            "android",
            "--revision",
            "123",
            "--source-revision",
            "123",
            "--release-bundle-id",
            "a" * 64,
            "--verification-path",
            str(verification_path),
            "--unity-version",
            "2022.3.62f2",
            "--application-id",
            "com.example.game",
            "--version-name",
            "1.0.0",
            "--version-code",
            "1",
            "--package-profile",
            "release",
        ]
    )

    result = CommandDispatcher(create_production_command_services(services)).dispatch(args)

    assert result == {"state": "succeeded"}
    assert len(calls) == 1
