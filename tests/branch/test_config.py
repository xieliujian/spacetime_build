"""验证 branch TOML schema、映射规范化和最长前缀解析。"""

from __future__ import annotations

from pathlib import Path

import pytest

from branch.config import (
    BranchConfig,
    BranchConfigError,
    MappingRule,
    load_branch_config,
    normalize_branch_config,
    parse_branch_config,
)


def _valid_data() -> dict[str, object]:
    """构造一个包含 project/resource/custom 映射的合法配置字典。"""
    return {
        "schema_version": 1,
        "source": {
            "project": "^/repo/trunk/project",
            "resource": "^/repo/trunk/resource",
            "custom": {"tools": "^/repo/trunk/tools"},
        },
        "target": {
            "project": "^/repo/branches/project",
            "resource": "^/repo/branches/resource",
            "custom": {"tools": "^/repo/branches/tools"},
        },
        "externals": {
            "allowlist": ["^/repo/branches", "https://svn.example.test/repo/branches"],
            "unmatched_policy": "preserve",
        },
    }


def test_parse_branch_config_normalizes_mapping_order_and_paths() -> None:
    """验证不同输入插入顺序产生相同的不可变规范配置。"""
    data = _valid_data()
    reversed_data = _valid_data()
    reversed_data["source"] = {
        "custom": {"tools": "^/repo/trunk/tools/"},
        "resource": "^/repo/trunk/resource/",
        "project": "^/repo/trunk/project/",
    }
    reversed_data["target"] = {
        "custom": {"tools": "^/repo/branches/tools/"},
        "resource": "^/repo/branches/resource/",
        "project": "^/repo/branches/project/",
    }

    first = parse_branch_config(data)
    second = parse_branch_config(reversed_data)

    assert isinstance(first, BranchConfig)
    assert first == second
    assert tuple(rule.name for rule in first.mappings) == ("custom.tools", "project", "resource")
    assert first.mappings[0].source_prefix == "^/repo/trunk/tools"


def test_branch_config_resolves_longest_prefix_deterministically() -> None:
    """验证更长的唯一前缀优先于父前缀，且结果与输入顺序无关。"""
    data = _valid_data()
    data["source"] = {"project": "^/repo/trunk", "resource": "^/repo/trunk/resource"}
    data["target"] = {"project": "^/repo/branches", "resource": "^/repo/branches/resource"}
    config = parse_branch_config(data)

    rule = config.resolve("^/repo/trunk/resource/file.asset")

    assert rule is not None
    assert rule.name == "resource"
    assert rule.target_prefix == "^/repo/branches/resource"


def test_branch_config_loads_tomllib_file_without_external_side_effects(tmp_path: Path) -> None:
    """验证文件加载使用 TOML 解析并返回与字典解析相同的配置。"""
    path = tmp_path / "branch.toml"
    path.write_text(
        """schema_version = 1

[source]
project = "^/repo/trunk/project"
resource = "^/repo/trunk/resource"

[source.custom]
tools = "^/repo/trunk/tools"

[target]
project = "^/repo/branches/project"
resource = "^/repo/branches/resource"

[target.custom]
tools = "^/repo/branches/tools"

[externals]
allowlist = ["^/repo/branches", "https://svn.example.test/repo/branches"]
unmatched_policy = "preserve"
""",
        encoding="utf-8",
    )

    assert load_branch_config(path) == parse_branch_config(_valid_data())


@pytest.mark.parametrize(
    "invalid_data",
    [
        {**_valid_data(), "unknown": True},
        {**_valid_data(), "source": {"project": "../escape"}},
        {**_valid_data(), "externals": {"allowlist": ["../escape"]}},
        {**_valid_data(), "schema_version": 2},
    ],
)
def test_branch_config_rejects_unknown_schema_and_path_escape(
    invalid_data: dict[str, object],
) -> None:
    """验证未知字段、非法版本和所有配置路径逃逸都会失败。"""
    with pytest.raises(BranchConfigError):
        parse_branch_config(invalid_data)


def test_branch_config_rejects_duplicate_and_ambiguous_mappings() -> None:
    """验证重复 source 前缀和同长度不同目标的歧义被拒绝。"""
    data = _valid_data()
    data["mappings"] = [
        {"name": "one", "source": "^/repo/trunk", "target": "^/repo/branches/one"},
        {"name": "two", "source": "^/repo/trunk", "target": "^/repo/branches/two"},
    ]
    data.pop("source")
    data.pop("target")

    with pytest.raises(BranchConfigError, match="歧义|重复"):
        parse_branch_config(data)


def test_mapping_rule_rejects_duplicate_names_and_exposes_source_target_aliases() -> None:
    """验证单条规则也执行局部校验并提供 Source/Target 语义别名。"""
    rule = MappingRule(
        name="project",
        source_prefix="^/repo/trunk/project",
        target_prefix="^/repo/branches/project",
    )

    assert rule.source == rule.source_prefix
    assert rule.target == rule.target_prefix
    with pytest.raises(BranchConfigError):
        MappingRule(name="", source_prefix="a", target_prefix="b")


def test_normalize_branch_config_accepts_only_mapping_data() -> None:
    """验证公开规范化入口不会隐式接受字符串或任意对象。"""
    with pytest.raises(BranchConfigError):
        normalize_branch_config("schema_version = 1")
