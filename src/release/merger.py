"""协议无关 ReleaseSnapshot 的纯函数式合并。

``SnapshotMerger`` 只合并已经通过领域校验的快照，不扫描目录、不读取 Blob、不执行
资源任务。incoming 条目按逻辑路径替换基线条目，removed_paths 显式删除；最终快照
按 UTF-8 逻辑路径排序并重新经过 ``ReleaseSnapshot.create`` 校验。
"""

from __future__ import annotations

from collections.abc import Iterable

from core.errors import PublishError
from release.snapshots import ReleaseSnapshot, ReleaseSnapshotEntry


class SnapshotMerger:
    """将增量快照显式合并到同变体基线。"""

    @staticmethod
    def merge(
        baseline: ReleaseSnapshot | None,
        incoming: ReleaseSnapshot,
        *,
        removed_paths: Iterable[str] = (),
    ) -> ReleaseSnapshot:
        """合并基线、替换/新增 incoming 条目并删除明确路径。

        参数：
            baseline: 可选同变体完整基线。
            incoming: 本次已验证的新增或替换快照。
            removed_paths: 调用方明确声明的删除逻辑路径。

        返回：
            重新通过完整交叉引用校验、路径稳定排序的 ``ReleaseSnapshot``。

        异常：
            类型、变体、删除路径不存在或删除/替换重叠时抛出 ``ValueError`` /
            ``PublishError``。

        约束与副作用：
            纯内存函数；不猜测删除、不修改输入快照、不执行外部副作用。
        """
        if not isinstance(incoming, ReleaseSnapshot):
            raise TypeError("incoming 必须是 ReleaseSnapshot")
        if baseline is not None and not isinstance(baseline, ReleaseSnapshot):
            raise TypeError("baseline 必须是 ReleaseSnapshot 或 None")
        if baseline is not None and baseline.variant is not incoming.variant:
            raise ValueError("baseline 与 incoming 必须使用同一 ResourceVariant")
        removed = tuple(removed_paths)
        if any(not isinstance(path, str) or not path for path in removed):
            raise ValueError("removed_paths 必须是非空字符串元组")
        if len(set(removed)) != len(removed):
            raise ValueError("removed_paths 不得重复")
        incoming_paths = {item.release_entry.logical_path for item in incoming.entries}
        if incoming_paths.intersection(removed):
            raise ValueError("同一路径不得同时出现在 incoming 和 removed_paths")
        merged: dict[str, ReleaseSnapshotEntry] = {}
        if baseline is not None:
            merged.update((item.release_entry.logical_path, item) for item in baseline.entries)
        for path in removed:
            if path not in merged:
                raise ValueError(f"removed_paths 不存在于 baseline: {path!r}")
            del merged[path]
        merged.update((item.release_entry.logical_path, item) for item in incoming.entries)
        ordered = tuple(
            merged[path] for path in sorted(merged, key=lambda value: value.encode("utf-8"))
        )
        if not ordered:
            raise PublishError("合并后的 ReleaseSnapshot 不得为空")
        return ReleaseSnapshot.create(incoming.variant, ordered)
