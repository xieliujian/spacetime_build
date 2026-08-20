"""验证 svn:externals 前缀重写、闭包报告与确定性约束。"""

from __future__ import annotations

import pytest

from branch.externals import (
    ExternalRewriteError,
    ExternalRewriteRule,
    parse_externals,
    rewrite_externals,
)


def _rules() -> tuple[ExternalRewriteRule, ...]:
    """构造一组有父子前缀关系的重写规则。"""
    return (
        ExternalRewriteRule(
            name="project",
            source_prefix="^/repo/trunk",
            target_prefix="^/repo/branches",
        ),
        ExternalRewriteRule(
            name="resource",
            source_prefix="^/repo/trunk/resource",
            target_prefix="^/repo/branches/resource-v2",
        ),
    )


def test_rewrite_uses_longest_prefix_and_reports_unmatched_closure() -> None:
    """验证子前缀优先、未匹配项按策略保留并进入闭包报告。"""
    document = parse_externals("^/repo/trunk/resource/a resource/a\n^/other/repo/tool tool\n")

    result = rewrite_externals(
        document,
        _rules(),
        allowed_repositories=("^/repo/branches",),
        unmatched_policy="preserve",
    )

    assert result.document.entries[0].url == "^/repo/branches/resource-v2/a"
    assert result.document.entries[1].url == "^/other/repo/tool"
    assert result.report.unmatched_local_paths == ("tool",)
    assert result.report.closed is False


def test_rewrite_rejects_unmatched_or_disallowed_targets() -> None:
    """验证 fail 策略和目标仓库 allowlist 不能被静默绕过。"""
    document = parse_externals("^/other/repo/tool tool\n")
    with pytest.raises(ExternalRewriteError, match="未匹配"):
        rewrite_externals(document, _rules(), unmatched_policy="fail")

    document = parse_externals("^/repo/trunk/lib lib\n")
    with pytest.raises(ExternalRewriteError, match="allowlist"):
        rewrite_externals(
            document,
            _rules(),
            allowed_repositories=("^/different/repo",),
        )


def test_rewrite_rejects_duplicate_local_paths_and_mapping_cycles() -> None:
    """验证 external local path 重复和规则有向环都显式失败。"""
    duplicate = parse_externals("^/repo/trunk/a shared\n^/repo/trunk/b shared\n")
    with pytest.raises(ExternalRewriteError, match="重复"):
        rewrite_externals(duplicate, _rules())

    cycle = (
        ExternalRewriteRule("a", "^/repo/a", "^/repo/b"),
        ExternalRewriteRule("b", "^/repo/b", "^/repo/a"),
    )
    with pytest.raises(ExternalRewriteError, match="循环"):
        rewrite_externals(parse_externals("^/repo/a lib\n"), cycle)


def test_rewrite_is_independent_of_rule_input_order() -> None:
    """验证规则排列顺序不影响输出文本和结构化报告。"""
    document = parse_externals("^/repo/trunk/resource/a resource/a\n")
    first = rewrite_externals(document, _rules())
    second = rewrite_externals(document, tuple(reversed(_rules())))

    assert first.document == second.document
    assert first.report == second.report
