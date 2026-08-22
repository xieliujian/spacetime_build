"""scene 资源任务的显式输入、Unity 操作和依赖输出契约测试。

本模块使用可控 builder 与内存对象存储验证场景任务边界；测试不启动真实 Unity，
也不把低清变体或源工程反写行为伪装成本期能力。
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
from resource.tasks.scene import (
    SceneBuildOutput,
    SceneBuildRequest,
    SceneBuilder,
    SceneResourceTask,
)
from resource.unity_operations import UnityProjectRole


class _Store:
    """记录场景产物 Blob 写入的内存对象存储替身。"""

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
        "source-1", "scene-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-2", None
    )


def _context() -> BuildContext:
    """构造确定性资源构建上下文。"""
    return BuildContext("a" * 64, "r100", "b" * 64, None, 1)


def _shader_artifact(
    logical_path: str = "depend/shader_common.assetbundle",
    sha256: str = "1" * 64,
    source_task: str = "shader_bundle",
) -> LogicalArtifact:
    """构造一份已登记的显式 Shader 上游产物。"""
    return LogicalArtifact(
        logical_path=logical_path,
        kind=ArtifactKind.ASSET_BUNDLE,
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
    """规划并生成固定场景 Bundle 与索引的 builder 替身。"""

    def __init__(self, output_root: Path) -> None:
        """保存输出根并初始化请求记录。"""
        self.output_root = output_root
        self.plan_requests: list[SceneBuildRequest] = []
        self.build_requests: list[SceneBuildRequest] = []

    def plan(self, request: SceneBuildRequest) -> tuple[str, ...]:
        """返回固定场景输出集合。"""
        self.plan_requests.append(request)
        return ("scene/main.assetbundle", "scene/scene_index.json")

    def build(self, request: SceneBuildRequest) -> tuple[SceneBuildOutput, ...]:
        """写入固定字节并返回带 Unity 依赖的场景输出。"""
        self.build_requests.append(request)
        request.output_root.mkdir(parents=True, exist_ok=True)
        bundle = request.output_root / "main.assetbundle"
        index = request.output_root / "scene_index.json"
        bundle.write_bytes(b"scene-main")
        index.write_bytes(b"scene-index")
        return (
            SceneBuildOutput(
                "scene/main.assetbundle",
                bundle,
                ("depend/shader_common.assetbundle", "depend/shader_common.assetbundle"),
            ),
            SceneBuildOutput("scene/scene_index.json", index, ()),
        )


def _task(tmp_path: Path, store: _Store, builder: SceneBuilder | None = None) -> SceneResourceTask:
    """构造带或不带 builder 的 scene 任务。"""
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    if builder is None:
        return SceneResourceTask(_input(), source, BlobCommitter(store))
    return SceneResourceTask(
        _input(),
        source,
        BlobCommitter(store),
        builder=builder,
        output_root=tmp_path / "output",
    )


def test_scene_requires_explicit_shader_input_before_starting_builder(tmp_path: Path) -> None:
    """验证场景 builder 缺少显式 Shader 上游时不会启动。"""
    store = _Store()
    builder = _Builder(tmp_path / "output")
    task = _task(tmp_path, store, builder)

    with pytest.raises(ValueError, match="shader"):
        ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))
    assert builder.plan_requests == []
    assert store.requests == []


def test_scene_plans_build_scene_and_binds_shader_identity(tmp_path: Path) -> None:
    """验证场景操作、资源工程角色和显式 Shader Blob 摘要均被固定。"""
    store = _Store()
    builder = _Builder(tmp_path / "output")
    task = _task(tmp_path, store, builder)
    shader = _shader_artifact()
    inputs = ArtifactCollection.from_artifacts((shader,))

    plan = task.plan_with_inputs(_context(), inputs)

    assert plan.spec.dependencies == ()
    assert plan.spec.outputs == frozenset({"scene/main.assetbundle", "scene/scene_index.json"})
    request = builder.plan_requests[0]
    assert request.operation.name == "build_scene"
    assert request.operation.project_role is UnityProjectRole.RESOURCE
    assert request.operation.arguments == (("platform", "windows"),)
    assert request.shader_inputs == (shader,)

    changed = _shader_artifact(sha256="2" * 64)
    changed_plan = task.plan_with_inputs(_context(), ArtifactCollection.from_artifacts((changed,)))
    assert changed_plan.resolved_input_digest != plan.resolved_input_digest


def test_scene_build_commits_exact_outputs_and_preserves_dependencies(tmp_path: Path) -> None:
    """验证场景输出先完整校验，再提交 CAS，并保留依赖原始顺序和重复项。"""
    store = _Store()
    builder = _Builder(tmp_path / "output")
    task = _task(tmp_path, store, builder)

    result = ResourceBuildService().build(
        task,
        _context(),
        ArtifactCollection.from_artifacts((_shader_artifact(),)),
    )

    assert tuple(item.logical_path for item in result.result.outputs) == (
        "scene/main.assetbundle",
        "scene/scene_index.json",
    )
    assert result.result.outputs[0].dependencies == (
        "depend/shader_common.assetbundle",
        "depend/shader_common.assetbundle",
    )
    assert len(store.requests) == 2
    assert len(builder.build_requests) == 1


@pytest.mark.parametrize("mode", ("drift", "missing", "outside", "wrong_prefix"))
def test_scene_rejects_invalid_outputs_before_cas_write(tmp_path: Path, mode: str) -> None:
    """验证输出漂移、缺失、越界和错误逻辑前缀不会写入 CAS。"""

    class _InvalidBuilder(_Builder):
        """按测试模式生成非法场景输出的替身。"""

        def build(self, request: SceneBuildRequest) -> tuple[SceneBuildOutput, ...]:
            """返回指定的非法输出集合。"""
            request.output_root.mkdir(parents=True, exist_ok=True)
            valid = request.output_root / "main.assetbundle"
            valid.write_bytes(b"scene-main")
            if mode == "drift":
                return (SceneBuildOutput("scene/main.assetbundle", valid, ()),)
            if mode == "missing":
                return (
                    SceneBuildOutput("scene/main.assetbundle", request.output_root / "missing", ()),
                    SceneBuildOutput(
                        "scene/scene_index.json", request.output_root / "index.json", ()
                    ),
                )
            if mode == "outside":
                return (
                    SceneBuildOutput(
                        "scene/main.assetbundle", request.output_root.parent / "escape", ()
                    ),
                    SceneBuildOutput("scene/scene_index.json", valid, ()),
                )
            return (
                SceneBuildOutput("ui/main.assetbundle", valid, ()),
                SceneBuildOutput("scene/scene_index.json", valid, ()),
            )

    store = _Store()
    task = _task(tmp_path, store, _InvalidBuilder(tmp_path / "output"))

    with pytest.raises((FileNotFoundError, ValueError), match="输出|文件|规划"):
        ResourceBuildService().build(
            task,
            _context(),
            ArtifactCollection.from_artifacts((_shader_artifact(),)),
        )
    assert store.requests == []


def test_scene_compatibility_mode_keeps_file_task_contract(tmp_path: Path) -> None:
    """验证未注入 builder 时仍保留固定目录到 CAS 的兼容模式。"""
    store = _Store()
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "legacy.assetbundle").write_bytes(b"legacy")
    task = SceneResourceTask(_input(), source, BlobCommitter(store))

    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))

    assert result.result.outputs[0].logical_path == "scene/legacy.assetbundle"
    assert len(store.requests) == 1


def test_scene_rejects_non_shader_source_and_platform_override(tmp_path: Path) -> None:
    """验证场景输入来源和平台参数不能被调用方伪造。"""
    store = _Store()
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="platform"):
        SceneResourceTask(
            _input(),
            source,
            BlobCommitter(store),
            builder=_Builder(tmp_path / "output"),
            output_root=tmp_path / "output",
            operation_arguments=(("platform", "android"),),
        )

    task = _task(tmp_path, store, _Builder(tmp_path / "output-foreign"))
    with pytest.raises(ValueError, match="shader_bundle"):
        task.plan_with_inputs(
            _context(),
            ArtifactCollection.from_artifacts((_shader_artifact(source_task="scene"),)),
        )
