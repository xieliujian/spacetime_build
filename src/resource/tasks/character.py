"""character 资源任务。"""

from pathlib import Path

from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.tasks.file_task import FileResourceTask


class CharacterResourceTask(FileResourceTask):
    """生成 character/ 下的角色资源产物。"""

    def __init__(
        self, resource_input: ResourceBuildInput, source_root: Path, blob_committer: BlobCommitter
    ) -> None:
        """初始化 character 文件任务。"""
        super().__init__(
            resource_input,
            source_root,
            blob_committer,
            kind=ResourceKind.CHARACTER,
            name="character",
            output_prefix="character",
        )
