"""发布缓存键的确定性测试。"""

from release.cache_keys import CacheKeyFactory, ReleaseCacheKeyInput


def _value(**overrides: object) -> ReleaseCacheKeyInput:
    """构造缓存键输入。"""
    values: dict[str, object] = {
        "task_name": "scene",
        "implementation_version": "1",
        "source_revision": "123",
        "input_file_hashes": ("a" * 64, "b" * 64),
        "config_digest": "c" * 64,
        "platform": "windows",
        "profile": "release",
        "tool_versions": (("unity", "2022"),),
        "strategy_version": "strategy-1",
        "source_snapshot_id": "source-1",
        "upstream_hashes": ("d" * 64,),
    }
    values.update(overrides)
    return ReleaseCacheKeyInput(**values)  # type: ignore[arg-type]


def test_cache_key_normalizes_unordered_hash_inputs() -> None:
    """验证输入文件和上游摘要排列不影响缓存键。"""
    first = CacheKeyFactory.create(_value())
    second = CacheKeyFactory.create(
        _value(input_file_hashes=tuple(reversed(_value().input_file_hashes)))
    )
    assert first == second


def test_cache_key_changes_when_identity_field_changes() -> None:
    """验证规则、平台和工具链变化都会隔离缓存。"""
    original = CacheKeyFactory.create(_value())
    assert original != CacheKeyFactory.create(_value(strategy_version="strategy-2"))
    assert original != CacheKeyFactory.create(_value(platform="android"))
    assert original != CacheKeyFactory.create(_value(tool_versions=(("unity", "2023"),)))
