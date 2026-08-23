"""验证 CLI production composition 能调用版本服务和外部探针套件。"""

from __future__ import annotations

from pathlib import Path

from application.composition import ProductionApplicationServices
from cli.commands import CommandDispatcher
from cli.parser import build_parser
from cli.production import create_production_command_services
from integrations.factory import IntegrationFactory


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
