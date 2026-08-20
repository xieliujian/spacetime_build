"""验证 svn:externals 的结构化解析、语义保留和规范渲染。"""

from __future__ import annotations

import pytest

from branch.externals import (
    ExternalParseError,
    ExternalSyntax,
    parse_externals,
    render_externals,
)


def test_parse_and_render_preserve_comments_blank_lines_and_unchanged_entries() -> None:
    """验证空行、注释、行顺序、引号路径和原始换行均可无损往返。"""
    text = (
        "# common assets\r\n"
        "\r\n"
        '-r 17 ^/repo/trunk/lib@19 "third party/lib"\r\n'
        '"legacy path" -r3 ../shared/lib@4\r\n'
    )

    document = parse_externals(text)

    assert len(document.entries) == 2
    assert document.entries[0].operative_revision == 17
    assert document.entries[0].peg_revision == 19
    assert document.entries[0].local_path == "third party/lib"
    assert document.entries[0].syntax is ExternalSyntax.NEW
    assert document.entries[1].syntax is ExternalSyntax.OLD
    assert document.entries[1].url == "../shared/lib"
    assert document.entries[1].operative_revision == 3
    assert document.entries[1].peg_revision == 4
    assert render_externals(document) == text


def test_relative_urls_and_operative_only_revision_are_structured() -> None:
    """验证仓库相对 URL 和没有 peg revision 的 external 不被当作普通文本。"""
    document = parse_externals("^/repo/common shared\n../../vendor vendor\n")

    assert [entry.url for entry in document.entries] == ["^/repo/common", "../../vendor"]
    assert all(entry.operative_revision is None for entry in document.entries)
    assert all(entry.peg_revision is None for entry in document.entries)


@pytest.mark.parametrize(
    "text",
    [
        "-rHEAD ^/repo/trunk/lib lib\n",
        "^/repo/trunk/lib\n",
        "^/repo/trunk/lib local extra\n",
        '"unterminated ^/repo/trunk/lib\n',
        "^/repo/trunk/lib ../escape\n",
        "-r 0 ^/repo/trunk/lib lib\n",
    ],
)
def test_parse_externals_rejects_illegal_lines_and_unfixed_revision(text: str) -> None:
    """验证缺字段、路径逃逸、非法 revision 和引号错误带有行级异常。"""
    with pytest.raises(ExternalParseError, match="第 1 行"):
        parse_externals(text)


def test_changed_external_renders_deterministically_with_quoted_path() -> None:
    """验证显式修改后只规范化目标 external，未修改行仍使用原始表示。"""
    document = parse_externals('# keep\n^/repo/trunk/lib "third party/lib"\n')
    changed = document.replace_url(0, "^/repo/branches/lib")

    assert render_externals(changed) == '# keep\n^/repo/branches/lib "third party/lib"\n'
    assert render_externals(document) == '# keep\n^/repo/trunk/lib "third party/lib"\n'
