"""shader variant 资源任务。"""

from pathlib import Path

from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.tasks.file_task import FileResourceTask


class ShaderVariantResourceTask(FileResourceTask):
    """生成 shader 变体清单的确定性文件产物。"""

    def __init__(
        self, resource_input: ResourceBuildInput, source_root: Path, blob_committer: BlobCommitter
    ) -> None:
        """初始化 shader variant 文件任务。"""
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.SHADER_VARIANT,
            name="shader_variant",
            output_prefix="depend/shader_variant",
        )
