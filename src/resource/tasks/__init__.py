"""正式版本第一期十二类资源任务。

每个任务只拥有自己的逻辑输出前缀，任务间没有隐式依赖。具体工具实现可以在
后续通过端口替换文件任务的 ``build`` 步骤，但任务身份和产物契约保持不变。
"""

from resource.tasks.audio import AudioResourceTask
from resource.tasks.character import CharacterResourceTask
from resource.tasks.config import ConfigResourceTask
from resource.tasks.lua import LuaResourceTask
from resource.tasks.map import MapResourceTask
from resource.tasks.particle import ParticleResourceTask
from resource.tasks.scene import SceneResourceTask
from resource.tasks.shader_bundle import ShaderBundleResourceTask
from resource.tasks.shader_variant import ShaderVariantResourceTask
from resource.tasks.texture import TextureResourceTask
from resource.tasks.ui import UiResourceTask
from resource.tasks.video import VideoResourceTask

__all__ = [
    "AudioResourceTask",
    "CharacterResourceTask",
    "ConfigResourceTask",
    "LuaResourceTask",
    "MapResourceTask",
    "ParticleResourceTask",
    "SceneResourceTask",
    "ShaderBundleResourceTask",
    "ShaderVariantResourceTask",
    "TextureResourceTask",
    "UiResourceTask",
    "VideoResourceTask",
]
