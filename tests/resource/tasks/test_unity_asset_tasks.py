"""七类 Unity 资源任务的统一 builder 契约与任务专属输入测试。

测试使用可控 builder 和内存对象存储，不启动真实 Unity；真实工具执行由后续
Unity batch 适配器探针负责。这里重点验证显式输入、操作身份、输出所有权、依赖
保序和 CAS 提交边界。
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
from resource.tasks.audio import AudioResourceTask
from resource.tasks.character import CharacterResourceTask
from resource.tasks.map import MapResourceTask
from resource.tasks.particle import ParticleResourceTask
from resource.tasks.texture import TextureResourceTask
from resource.tasks.ui import UiResourceTask
from resource.tasks.unity_asset import UnityAssetBuildOutput, UnityAssetBuildRequest
from resource.tasks.video import VideoResourceTask
from resource.unity_operations import UnityProjectRole


class _Store:
    """记录资源 Blob 写入的内存对象存储替身。"""

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
        "source-1", "resource-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-3", None
    )


def _context() -> BuildContext:
    """构造确定性资源构建上下文。"""
    return BuildContext("a" * 64, "r100", "b" * 64, None, 1)


def _artifact(
    logical_path: str,
    source_task: str,
    sha256: str = "1" * 64,
) -> LogicalArtifact:
    """构造一份带来源任务身份的显式输入产物。"""
    return LogicalArtifact(
        logical_path=logical_path,
        kind=ArtifactKind.FILE,
        blob=BlobRef(f"blobs/{sha256}", sha256, 9),
        dependencies=(),
        subpackage_ids=frozenset(),
        metadata=ArtifactMetadata(
            source_task=source_task,
            source_revision="r100",
            toolchain_digest="b" * 64,
            attributes=(),
        ),
    )


def _config() -> LogicalArtifact:
    """构造配置任务产物。"""
    return _artifact("config/runtime.bin", "config")


def _shader() -> LogicalArtifact:
    """构造 Shader Bundle 产物。"""
    return _artifact("depend/shader_common.assetbundle", "shader_bundle")


def _atlas() -> LogicalArtifact:
    """构造图集任务产物。"""
    return _artifact("ui/atlas/common.bin", "atlas")


class _Builder:
    """生成固定 Bundle 和索引的通用 builder 替身。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.plan_requests: list[UnityAssetBuildRequest] = []
        self.build_requests: list[UnityAssetBuildRequest] = []

    def plan(self, request: UnityAssetBuildRequest) -> tuple[str, ...]:
        """根据操作名返回固定任务输出。"""
        self.plan_requests.append(request)
        prefix = request.operation.name.removeprefix("build_")
        return (f"{prefix}/index.json", f"{prefix}/main.assetbundle")

    def build(self, request: UnityAssetBuildRequest) -> tuple[UnityAssetBuildOutput, ...]:
        """在隔离输出根写入固定输出并保留示例依赖。"""
        self.build_requests.append(request)
        request.output_root.mkdir(parents=True, exist_ok=True)
        prefix = request.operation.name.removeprefix("build_")
        bundle = request.output_root / "main.assetbundle"
        index = request.output_root / "index.json"
        bundle.write_bytes(prefix.encode("ascii") + b"-bundle")
        index.write_bytes(prefix.encode("ascii") + b"-index")
        dependencies = (
            ("depend/shader_common.assetbundle",) if prefix in {"character", "particle"} else ()
        )
        return (
            UnityAssetBuildOutput(f"{prefix}/index.json", index),
            UnityAssetBuildOutput(f"{prefix}/main.assetbundle", bundle, dependencies),
        )


def _task(task_type: type, tmp_path: Path, store: _Store, builder: _Builder) -> object:
    """构造带 builder 的资源任务。"""
    source = tmp_path / task_type.__name__.lower()
    source.mkdir()
    return task_type(
        _input(),
        source,
        BlobCommitter(store),
        builder=builder,
        output_root=tmp_path / "output",
    )


def test_map_requires_config_and_build_map_operation(tmp_path: Path) -> None:
    """验证地图任务只消费配置输入并生成 build_map 操作。"""
    builder = _Builder()
    task = _task(MapResourceTask, tmp_path, _Store(), builder)

    plan = task.plan_with_inputs(_context(), ArtifactCollection.from_artifacts((_config(),)))

    assert plan.spec.outputs == frozenset({"map/main.assetbundle", "map/index.json"})
    request = builder.plan_requests[0]
    assert request.operation.name == "build_map"
    assert request.operation.project_role is UnityProjectRole.RESOURCE
    assert request.explicit_inputs == (_config(),)

    with pytest.raises(ValueError, match="config"):
        task.plan_with_inputs(_context(), ArtifactCollection.from_artifacts((_shader(),)))


def test_character_requires_config_and_shader_inputs(tmp_path: Path) -> None:
    """验证角色任务必须同时绑定配置和 Shader 上游。"""
    builder = _Builder()
    task = _task(CharacterResourceTask, tmp_path, _Store(), builder)
    inputs = ArtifactCollection.from_artifacts((_config(), _shader()))

    task.plan_with_inputs(_context(), inputs)

    request = builder.plan_requests[0]
    assert request.operation.name == "build_character"
    assert tuple(item.metadata.source_task for item in request.explicit_inputs) == (
        "config",
        "shader_bundle",
    )

    with pytest.raises(ValueError, match="config"):
        task.plan_with_inputs(_context(), ArtifactCollection.from_artifacts((_shader(),)))


def test_particle_requires_shader_input_and_preserves_output_dependencies(tmp_path: Path) -> None:
    """验证粒子任务绑定 Shader 输入，并保留 builder 返回的依赖。"""
    store = _Store()
    builder = _Builder()
    task = _task(ParticleResourceTask, tmp_path, store, builder)

    result = ResourceBuildService().build(
        task,
        _context(),
        ArtifactCollection.from_artifacts((_shader(),)),
    )

    assert result.result.outputs[1].dependencies == ("depend/shader_common.assetbundle",)
    assert len(store.requests) == 2


def test_ui_requires_atlas_input_and_uses_ui_operation(tmp_path: Path) -> None:
    """验证 UI 任务使用图集显式输入和 UI 工程操作。"""
    builder = _Builder()
    task = _task(UiResourceTask, tmp_path, _Store(), builder)

    task.plan_with_inputs(_context(), ArtifactCollection.from_artifacts((_atlas(),)))

    request = builder.plan_requests[0]
    assert request.operation.name == "build_ui"
    assert request.explicit_inputs == (_atlas(),)


@pytest.mark.parametrize(
    ("task_type", "operation"),
    [
        (TextureResourceTask, "build_texture"),
        (AudioResourceTask, "build_audio"),
        (VideoResourceTask, "build_video"),
    ],
)
def test_texture_audio_video_have_typed_operations(
    tmp_path: Path, task_type: type, operation: str
) -> None:
    """验证贴图、音频和视频任务均接入类型化 Unity 操作。"""
    builder = _Builder()
    task = _task(task_type, tmp_path, _Store(), builder)

    task.plan_with_inputs(_context(), ArtifactCollection.from_artifacts(()))

    request = builder.plan_requests[0]
    assert request.operation.name == operation
    assert request.operation.project_role is UnityProjectRole.RESOURCE
    assert request.explicit_inputs == ()


def test_invalid_unity_asset_output_is_rejected_before_cas_write(tmp_path: Path) -> None:
    """验证 builder 输出越界或逻辑路径漂移时不会提交任何 Blob。"""

    class _InvalidBuilder(_Builder):
        """返回越出任务输出前缀的非法产物。"""

        def build(self, request: UnityAssetBuildRequest) -> tuple[UnityAssetBuildOutput, ...]:
            """故意返回错误逻辑路径。"""
            request.output_root.mkdir(parents=True, exist_ok=True)
            output = request.output_root / "main.assetbundle"
            output.write_bytes(b"invalid")
            return (
                UnityAssetBuildOutput("escape/main.assetbundle", output),
                UnityAssetBuildOutput("map/index.json", output),
            )

    store = _Store()
    builder = _InvalidBuilder()
    task = _task(MapResourceTask, tmp_path, store, builder)

    with pytest.raises(ValueError, match="输出"):
        ResourceBuildService().build(
            task,
            _context(),
            ArtifactCollection.from_artifacts((_config(),)),
        )
    assert store.requests == []


@pytest.mark.parametrize(
    "task_type",
    [
        MapResourceTask,
        CharacterResourceTask,
        TextureResourceTask,
        UiResourceTask,
        ParticleResourceTask,
        AudioResourceTask,
        VideoResourceTask,
    ],
)
def test_tasks_keep_file_compatibility_without_builder(tmp_path: Path, task_type: type) -> None:
    """验证未注入 builder 时七类任务仍保持旧文件任务兼容模式。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "legacy.bin").write_bytes(b"legacy")
    store = _Store()
    task = task_type(_input(), source, BlobCommitter(store))

    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))

    assert result.result.outputs[0].logical_path.endswith("/legacy.bin")
    assert len(store.requests) == 1


def test_audio_rejects_case_insensitive_duplicate_bank_names(tmp_path: Path) -> None:
    """验证音频 bank 名单在大小写不敏感平台上不能产生歧义。"""
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="bank"):
        AudioResourceTask(
            _input(),
            source,
            BlobCommitter(_Store()),
            bank_names=("Main", "main"),
        )


def test_video_rejects_case_insensitive_duplicate_source_names(tmp_path: Path) -> None:
    """验证视频源文件名重复时在构造阶段失败。"""
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="video"):
        VideoResourceTask(
            _input(),
            source,
            BlobCommitter(_Store()),
            source_names=("Intro.mp4", "intro.mp4"),
        )


def test_audio_request_contains_typed_bank_settings(tmp_path: Path) -> None:
    """验证音频名单进入确定性 builder 请求，而不是隐藏在文件扫描中。"""
    builder = _Builder()
    source = tmp_path / "source"
    source.mkdir()
    task = AudioResourceTask(
        _input(),
        source,
        BlobCommitter(_Store()),
        builder=builder,
        output_root=tmp_path / "output",
        bank_names=("Main", "voice"),
    )

    task.plan_with_inputs(_context(), ArtifactCollection.from_artifacts(()))

    assert ("bank_names", "Main,voice") in builder.plan_requests[0].settings


def test_texture_request_has_spine_and_language_settings(tmp_path: Path) -> None:
    """验证贴图任务将 Spine 版本和多语言名单纳入 builder 请求。"""
    builder = _Builder()
    source = tmp_path / "source"
    source.mkdir()
    task = TextureResourceTask(
        _input(),
        source,
        BlobCommitter(_Store()),
        builder=builder,
        output_root=tmp_path / "output",
        spine_version="4.2",
        languages=("en", "zh"),
    )

    task.plan_with_inputs(_context(), ArtifactCollection.from_artifacts(()))

    assert ("spine_version", "4.2") in builder.plan_requests[0].settings
    assert ("languages", "en,zh") in builder.plan_requests[0].settings
