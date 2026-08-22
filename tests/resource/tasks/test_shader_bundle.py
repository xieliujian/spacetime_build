"""Shader bundle 任务的显式 variant 输入和 Unity 输出契约测试。

本模块使用可控 builder 和内存对象存储验证资源任务边界；测试不启动真实 Unity，
也不假设供应商 Shader Bundle 的具体二进制格式。
"""

from pathlib import Path

import pytest

from core.artifacts import ArtifactKind, ArtifactMetadata, BlobRef, LogicalArtifact
from core.platforms import BuildPlatform
from core.tasks import ArtifactCollection, BuildContext
from ports.storage import PutObjectRequest, StoredObject
from release.entries import ResourceVariant
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput
from resource.task_service import ResourceBuildService
from resource.tasks.shader_bundle import (
    ShaderBundleBuildOutput,
    ShaderBundleBuildRequest,
    ShaderBundleResourceTask,
)
from resource.unity_operations import UnityProjectRole


class _Store:
    """记录 shader bundle Blob 写入的内存对象存储替身。"""

    def __init__(self) -> None:
        """初始化写入记录。"""
        self.requests: list[PutObjectRequest] = []

    def put(self, request: PutObjectRequest) -> StoredObject:
        """保存请求并返回与请求一致的对象引用。"""
        self.requests.append(request)
        return StoredObject(request.key, request.sha256, len(request.content))


def _input() -> ResourceBuildInput:
    """构造固定 Windows 主资源输入。"""
    return ResourceBuildInput(
        "source-1", "shader-bundle-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-2", None
    )


def _context() -> BuildContext:
    """构造确定性资源构建上下文。"""
    return BuildContext("a" * 64, "r100", "b" * 64, None, 1)


def _variant_artifact(
    logical_path: str = "depend/shader_variant/variants.bin",
    sha256: str = "1" * 64,
    source_task: str = "shader_variant",
) -> LogicalArtifact:
    """构造一份已登记的 Shader variant 逻辑产物。"""
    return LogicalArtifact(
        logical_path=logical_path,
        kind=ArtifactKind.FILE,
        blob=BlobRef(f"blobs/{sha256}", sha256, 13),
        dependencies=(),
        subpackage_ids=frozenset(),
        metadata=ArtifactMetadata(
            source_task=source_task,
            source_revision="r100",
            toolchain_digest="b" * 64,
            attributes=(),
        ),
    )


class _Builder:
    """规划并生成固定 Shader Bundle 输出的 builder 替身。"""

    def __init__(self, output_root: Path) -> None:
        """保存输出根并初始化请求记录。"""
        self.output_root = output_root
        self.plan_requests: list[ShaderBundleBuildRequest] = []
        self.build_requests: list[ShaderBundleBuildRequest] = []

    def plan(self, request: ShaderBundleBuildRequest) -> tuple[str, ...]:
        """返回固定共享 Shader Bundle 输出。"""
        self.plan_requests.append(request)
        return (
            "depend/shader_common.assetbundle",
            "depend/shader_mobile.assetbundle",
        )

    def build(self, request: ShaderBundleBuildRequest) -> tuple[ShaderBundleBuildOutput, ...]:
        """写入固定字节并返回精确输出。"""
        self.build_requests.append(request)
        request.output_root.mkdir(parents=True, exist_ok=True)
        common = request.output_root / "common.assetbundle"
        mobile = request.output_root / "mobile.assetbundle"
        common.write_bytes(b"shader-common")
        mobile.write_bytes(b"shader-mobile")
        return (
            ShaderBundleBuildOutput("depend/shader_common.assetbundle", common),
            ShaderBundleBuildOutput("depend/shader_mobile.assetbundle", mobile),
        )


def _task(tmp_path: Path, store: _Store, builder: object | None = None) -> ShaderBundleResourceTask:
    """构造带或不带 builder 的 Shader Bundle 任务。"""
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    kwargs: dict[str, object] = {}
    if builder is not None:
        kwargs["builder"] = builder
        kwargs["output_root"] = tmp_path / "output"
    return ShaderBundleResourceTask(_input(), source, BlobCommitter(store), **kwargs)


def test_shader_bundle_requires_explicit_variant_input(tmp_path: Path) -> None:
    """验证 bundle 不会在没有显式 variant 产物时启动 builder。"""
    store = _Store()
    builder = _Builder(tmp_path / "output")
    task = _task(tmp_path, store, builder)

    with pytest.raises(ValueError, match="variant"):
        ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))
    assert builder.plan_requests == []
    assert store.requests == []


def test_shader_bundle_plans_build_shader_and_binds_variant_identity(tmp_path: Path) -> None:
    """验证操作身份、显式输入请求和 variant Blob 摘要都被固定。"""
    store = _Store()
    builder = _Builder(tmp_path / "output")
    task = _task(tmp_path, store, builder)
    variant = _variant_artifact()
    inputs = ArtifactCollection.from_artifacts((variant,))

    plan = task.plan_with_inputs(_context(), inputs)

    assert plan.spec.dependencies == ()
    assert plan.spec.outputs == frozenset(
        {
            "depend/shader_common.assetbundle",
            "depend/shader_mobile.assetbundle",
        }
    )
    request = builder.plan_requests[0]
    assert request.operation.name == "build_shader_bundle"
    assert request.operation.project_role is UnityProjectRole.SHADER
    assert request.operation.arguments == (("platform", "windows"),)
    assert request.variant_inputs == (variant,)

    changed = _variant_artifact(sha256="2" * 64)
    changed_plan = task.plan_with_inputs(_context(), ArtifactCollection.from_artifacts((changed,)))
    assert changed_plan.resolved_input_digest != plan.resolved_input_digest

    changed_snapshot = ResourceBuildInput(
        "source-2",
        "shader-bundle-2",
        BuildPlatform.WINDOWS,
        ResourceVariant.MAIN,
        "rules-2",
        None,
    )
    changed_task = ShaderBundleResourceTask(
        changed_snapshot,
        tmp_path / "source",
        BlobCommitter(store),
        builder=builder,
        output_root=tmp_path / "output-2",
    )
    snapshot_plan = changed_task.plan_with_inputs(
        _context(), ArtifactCollection.from_artifacts((variant,))
    )
    assert snapshot_plan.resolved_input_digest != plan.resolved_input_digest


def test_shader_bundle_builds_with_variant_input_and_commits_exact_outputs(tmp_path: Path) -> None:
    """验证显式 variant 进入 build 请求并在完整校验后提交 CAS。"""
    store = _Store()
    builder = _Builder(tmp_path / "output")
    task = _task(tmp_path, store, builder)
    variant = _variant_artifact()

    result = ResourceBuildService().build(
        task, _context(), ArtifactCollection.from_artifacts((variant,))
    )

    assert tuple(item.logical_path for item in result.result.outputs) == (
        "depend/shader_common.assetbundle",
        "depend/shader_mobile.assetbundle",
    )
    assert len(store.requests) == 2
    assert len(builder.build_requests) == 1
    assert builder.build_requests[0].variant_inputs == (variant,)


@pytest.mark.parametrize("mode", ("drift", "missing", "outside", "variant_prefix"))
def test_shader_bundle_rejects_invalid_outputs_before_cas_write(tmp_path: Path, mode: str) -> None:
    """验证输出漂移、缺失、越界和覆盖 variant 前缀均不会写入 CAS。"""

    class _InvalidBuilder(_Builder):
        """按测试模式生成非法输出的替身。"""

        def build(self, request: ShaderBundleBuildRequest) -> tuple[ShaderBundleBuildOutput, ...]:
            """返回指定的非法输出集合。"""
            request.output_root.mkdir(parents=True, exist_ok=True)
            valid = request.output_root / "common.assetbundle"
            valid.write_bytes(b"shader-common")
            if mode == "drift":
                return (ShaderBundleBuildOutput("depend/shader_common.assetbundle", valid),)
            if mode == "missing":
                return (
                    ShaderBundleBuildOutput(
                        "depend/shader_common.assetbundle", request.output_root / "missing"
                    ),
                    ShaderBundleBuildOutput(
                        "depend/shader_mobile.assetbundle",
                        request.output_root / "mobile.assetbundle",
                    ),
                )
            if mode == "outside":
                return (
                    ShaderBundleBuildOutput(
                        "depend/shader_common.assetbundle", request.output_root.parent / "escape"
                    ),
                    ShaderBundleBuildOutput("depend/shader_mobile.assetbundle", valid),
                )
            return (
                ShaderBundleBuildOutput("depend/shader_variant/variants.bin", valid),
                ShaderBundleBuildOutput("depend/shader_mobile.assetbundle", valid),
            )

    store = _Store()
    task = _task(tmp_path, store, _InvalidBuilder(tmp_path / "output"))

    with pytest.raises((FileNotFoundError, ValueError), match="输出|文件"):
        ResourceBuildService().build(
            task,
            _context(),
            ArtifactCollection.from_artifacts((_variant_artifact(),)),
        )
    assert store.requests == []


def test_shader_bundle_compatibility_mode_keeps_file_task_contract(tmp_path: Path) -> None:
    """验证未注入 builder 时仍保留固定目录到 CAS 的兼容模式。"""
    store = _Store()
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "legacy.assetbundle").write_bytes(b"legacy")
    task = ShaderBundleResourceTask(_input(), source, BlobCommitter(store))

    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))

    assert result.result.outputs[0].logical_path == "depend/shader_bundle/legacy.assetbundle"
    assert len(store.requests) == 1


def test_shader_bundle_rejects_non_variant_source_and_platform_override(tmp_path: Path) -> None:
    """验证输入来源和平台参数不能被调用方伪造或覆盖。"""
    store = _Store()
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="platform"):
        ShaderBundleResourceTask(
            _input(),
            source,
            BlobCommitter(store),
            builder=_Builder(tmp_path / "output"),
            output_root=tmp_path / "output",
            operation_arguments=(("platform", "android"),),
        )

    task = _task(tmp_path, store, _Builder(tmp_path / "output-foreign"))
    with pytest.raises(ValueError, match="shader_variant"):
        task.plan_with_inputs(
            _context(),
            ArtifactCollection.from_artifacts((_variant_artifact(source_task="scene"),)),
        )
