"""shader bundle 资源任务。"""

from pathlib import Path

from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.tasks.file_task import FileResourceTask


class ShaderBundleResourceTask(FileResourceTask):
    """生成共享 shader bundle 产物并拥有独占输出前缀。"""

    def __init__(
        self, resource_input: ResourceBuildInput, source_root: Path, blob_committer: BlobCommitter
    ) -> None:
        """初始化 shader bundle 文件任务。"""
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.SHADER_BUNDLE,
            name="shader_bundle",
            output_prefix="depend/shader_bundle",
        )
