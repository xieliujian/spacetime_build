"""发布缓存身份的确定性模型与 SHA256 工厂。

缓存键覆盖任务实现、固定源码 revision、输入内容、解析配置、平台、Profile、工具链、
策略、快照和显式上游身份。无序摘要集合在编码前按 UTF-8 排序，缓存键不使用 build_id、
本地路径或运行时间，避免跨节点和重试产生错误命中。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from core.manifest_codec import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class ReleaseCacheKeyInput:
    """发布任务缓存键的完整输入身份。"""

    task_name: str
    implementation_version: str
    source_revision: str
    input_file_hashes: tuple[str, ...]
    config_digest: str
    platform: str
    profile: str
    tool_versions: tuple[tuple[str, str], ...]
    strategy_version: str
    source_snapshot_id: str
    upstream_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        """校验字符串身份、摘要格式和稳定集合字段。"""
        for name in (
            "task_name",
            "implementation_version",
            "source_revision",
            "config_digest",
            "platform",
            "profile",
            "strategy_version",
            "source_snapshot_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or any(c in value for c in "\r\n"):
                raise ValueError(f"{name} 必须是非空无换行字符串")
        _validate_digests(self.input_file_hashes, "input_file_hashes")
        _validate_digests(self.upstream_hashes, "upstream_hashes")
        _validate_digest(self.config_digest, "config_digest")
        if not isinstance(self.tool_versions, tuple):
            raise TypeError("tool_versions 必须是 tuple[tuple[str, str], ...]")
        for name, version in self.tool_versions:
            if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
                raise ValueError("tool_versions 每项必须是非空字符串对")
        object.__setattr__(self, "input_file_hashes", _sorted_unique(self.input_file_hashes))
        object.__setattr__(self, "upstream_hashes", _sorted_unique(self.upstream_hashes))
        object.__setattr__(
            self,
            "tool_versions",
            tuple(sorted(self.tool_versions, key=lambda pair: pair[0].encode("utf-8"))),
        )


class CacheKeyFactory:
    """从完整缓存输入计算内容寻址缓存键。"""

    @staticmethod
    def create(value: ReleaseCacheKeyInput) -> str:
        """返回不含运行态字段的规范 SHA256 缓存键。"""
        if not isinstance(value, ReleaseCacheKeyInput):
            raise TypeError("value 必须是 ReleaseCacheKeyInput")
        payload = {
            "config_digest": value.config_digest,
            "implementation_version": value.implementation_version,
            "input_file_hashes": list(value.input_file_hashes),
            "platform": value.platform,
            "profile": value.profile,
            "source_revision": value.source_revision,
            "source_snapshot_id": value.source_snapshot_id,
            "strategy_version": value.strategy_version,
            "task_name": value.task_name,
            "tool_versions": [list(pair) for pair in value.tool_versions],
            "upstream_hashes": list(value.upstream_hashes),
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _validate_digest(value: str, field_name: str) -> None:
    """校验单个小写 SHA256 摘要。"""
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{field_name} 必须是 64 位小写 SHA256")


def _validate_digests(values: tuple[str, ...], field_name: str) -> None:
    """校验摘要元组。"""
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} 必须是 tuple[str, ...]")
    for value in values:
        _validate_digest(value, field_name)


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    """按 UTF-8 排序并去除重复摘要。"""
    return tuple(sorted(set(values), key=lambda value: value.encode("utf-8")))
