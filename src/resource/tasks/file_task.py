"""确定性文件型资源任务的共享实现。

本模块把已经进入隔离输入快照的文件逐个提交到内容寻址存储，并转换为现有
``LogicalArtifact``。它不模拟 Unity 产物：Unity 任务应在替换实现中通过端口完成，
而输出契约、路径所有权和 Blob 校验仍复用本模块。导入本模块不执行 I/O。
"""

from __future__ import annotations

from pathlib import Path

from core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    LogicalArtifact,
)
from core.tasks import ArtifactCollection, BuildContext, TaskResult
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput, ResourceKind
from resource.task_base import ResourceBuildTask


class FileResourceTask(ResourceBuildTask):
    """将一个固定输入目录映射为单一逻辑输出前缀的资源任务。

    职责：
        确定性枚举普通文件、检查大小写敏感的逻辑路径冲突、提交 Blob，并创建
        无分包依赖的 ``LogicalArtifact``。

    参数：
        resource_input: 固定资源输入身份。
        source_root: 已隔离的普通输入目录。
        blob_committer: 内容寻址 Blob 提交器。
        kind/name/output_prefix: 任务种类、名称和逻辑输出前缀。
        artifact_kind: 产物类型，默认为普通文件。
        implementation_version: 任务实现版本。

    返回：
        ``discover_outputs`` 返回精确路径，``build`` 返回已提交产物。

    异常：
        缺少文件、符号链接、逻辑路径冲突或 Blob 提交失败时抛出业务异常。

    约束与副作用：
        仅读取 source_root 并通过 BlobCommitter 写入 CAS，不提交 SVN、不触发 Jenkins。
    """

    def __init__(
        self,
        resource_input: ResourceBuildInput,
        source_root: Path,
        blob_committer: BlobCommitter,
        *,
        kind: ResourceKind,
        name: str,
        output_prefix: str,
        artifact_kind: ArtifactKind = ArtifactKind.FILE,
        implementation_version: str = "1",
    ) -> None:
        """初始化文件型任务并锁定输出所有权。

        参数：
            resource_input: 固定资源输入。
            source_root: 输入目录。
            blob_committer: CAS 提交器。
            kind/name/output_prefix: 任务身份和输出前缀。
            artifact_kind: 产物种类。
            implementation_version: 实现版本。

        返回：
            ``None``。

        异常：
            输入路径、提交器或输出前缀非法时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            只保存任务配置，不读取文件和提交对象。
        """
        if not isinstance(blob_committer, BlobCommitter):
            raise TypeError("blob_committer 必须是 BlobCommitter")
        if not isinstance(source_root, Path) or not source_root.is_absolute():
            raise ValueError("source_root 必须是绝对 Path")
        if not source_root.is_dir() or source_root.is_symlink():
            raise ValueError("source_root 必须是普通目录")
        if not isinstance(artifact_kind, ArtifactKind):
            raise TypeError("artifact_kind 必须是 ArtifactKind")
        if not output_prefix or "\\" in output_prefix or output_prefix.startswith("/"):
            raise ValueError("output_prefix 必须是相对逻辑路径")
        super().__init__(
            resource_input=resource_input,
            kind=kind,
            name=name,
            implementation_version=implementation_version,
            source_root=source_root,
        )
        self._blob_committer = blob_committer
        self._output_prefix = output_prefix.rstrip("/")
        self._artifact_kind = artifact_kind

    def _files(self, source_root: Path) -> tuple[tuple[str, Path], ...]:
        """枚举输入目录内的普通文件和相对路径。

        参数：
            source_root: 已校验的输入目录。

        返回：
            按 UTF-8 相对路径排序的 ``(相对路径, 绝对路径)`` 元组。

        异常：
            符号链接、无文件或根目录变化时抛出 ``ValueError`` / ``FileNotFoundError``。

        约束与副作用：
            只读目录树；不跟随符号链接，避免输入树逃逸。
        """
        if not source_root.is_dir() or source_root.is_symlink():
            raise FileNotFoundError(source_root)
        items: list[tuple[str, Path]] = []
        for path in source_root.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"资源输入不得包含符号链接: {path}")
            if path.is_file():
                relative = path.relative_to(source_root).as_posix()
                items.append((relative, path))
        if not items:
            raise FileNotFoundError(f"资源输入目录没有文件: {source_root}")
        items.sort(key=lambda item: item[0].encode("utf-8"))
        return tuple(items)

    def discover_outputs(self, source_root: Path) -> tuple[str, ...]:
        """发现目录内所有文件对应的精确逻辑输出。

        参数：
            source_root: 固定资源输入目录。

        返回：
            ``output_prefix`` 下按 UTF-8 排序的逻辑路径元组。

        异常：
            输入目录缺失、为空或含符号链接时抛出 ``FileNotFoundError`` / ``ValueError``。

        约束与副作用：
            只读发现，不创建输出。
        """
        return tuple(
            f"{self._output_prefix}/{relative}" for relative, _ in self._files(source_root)
        )

    def build(self, context: BuildContext, inputs: ArtifactCollection) -> TaskResult:
        """提交输入文件并返回严格对应的逻辑产物集合。

        参数：
            context: 共享构建上下文。
            inputs: 显式输入产物；文件任务当前不隐式消费它们。

        返回：
            与 ``discover_outputs`` 一一对应且已拥有持久 Blob 的 ``TaskResult``。

        异常：
            文件变化、对象存储失败或产物模型拒绝时透传对应业务异常。

        约束与副作用：
            每个文件先提交 CAS 再创建 ``LogicalArtifact``；失败不会返回部分结果。
        """
        del inputs
        source_root = self.source_root
        if source_root is None:
            raise ValueError("文件任务缺少 source_root")
        artifacts: list[LogicalArtifact] = []
        for relative, path in self._files(source_root):
            blob = self._blob_committer.commit(path, allowed_root=source_root)
            logical_path = f"{self._output_prefix}/{relative}"
            artifacts.append(
                LogicalArtifact(
                    logical_path=logical_path,
                    kind=self._artifact_kind,
                    blob=blob,
                    dependencies=(),
                    subpackage_ids=frozenset(),
                    metadata=ArtifactMetadata(
                        source_task=self.name,
                        source_revision=context.revision,
                        toolchain_digest=context.toolchain_digest,
                        attributes=(
                            ("platform", self.resource_input.platform.value),
                            ("variant", self.resource_input.variant.value),
                            ("rule_version", self.resource_input.rule_version),
                        ),
                    ),
                )
            )
        return TaskResult(tuple(artifacts))
