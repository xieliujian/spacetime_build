"""十二类资源任务注册和输出所有权测试。"""

from pathlib import Path

from core.platforms import BuildPlatform
from ports.storage import PutObjectRequest, StoredObject
from release.entries import ResourceVariant
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.tasks.audio import AudioResourceTask
from resource.tasks.character import CharacterResourceTask
from resource.tasks.config import ConfigResourceTask
from resource.tasks.map import MapResourceTask
from resource.tasks.particle import ParticleResourceTask
from resource.tasks.scene import SceneResourceTask
from resource.tasks.shader_bundle import ShaderBundleResourceTask
from resource.tasks.shader_variant import ShaderVariantResourceTask
from resource.tasks.texture import TextureResourceTask
from resource.tasks.ui import UiResourceTask
from resource.tasks.video import VideoResourceTask


class _Store:
    """提供最小对象存储回执。"""

    def put(self, request: PutObjectRequest) -> StoredObject:
        """返回请求对应的存储对象。"""
        return StoredObject(request.key, request.sha256, len(request.content))


def test_all_approved_resource_task_classes_have_unique_kind_and_name(tmp_path: Path) -> None:
    """验证批准的任务集合有唯一 kind、任务名和输出根。"""
    resource_input = ResourceBuildInput(
        "source-1", "resource-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-1", None
    )
    factories = (
        (ConfigResourceTask, "config", ResourceKind.CONFIG),
        (ShaderVariantResourceTask, "shader_variant", ResourceKind.SHADER_VARIANT),
        (ShaderBundleResourceTask, "shader_bundle", ResourceKind.SHADER_BUNDLE),
        (SceneResourceTask, "scene", ResourceKind.SCENE),
        (MapResourceTask, "map", ResourceKind.MAP),
        (CharacterResourceTask, "character", ResourceKind.CHARACTER),
        (TextureResourceTask, "texture", ResourceKind.TEXTURE),
        (UiResourceTask, "ui", ResourceKind.UI),
        (ParticleResourceTask, "particle", ResourceKind.PARTICLE),
        (AudioResourceTask, "audio", ResourceKind.AUDIO),
        (VideoResourceTask, "video", ResourceKind.VIDEO),
    )
    values: list[str] = []
    for task_class, name, kind in factories:
        root = tmp_path / name
        root.mkdir()
        (root / "file.bin").write_bytes(name.encode())
        task = task_class(resource_input, root, BlobCommitter(_Store()))
        assert task.name == name
        assert task.resource_kind is kind
        values.append(task.name)
    assert len(values) == len(set(values)) == 11
