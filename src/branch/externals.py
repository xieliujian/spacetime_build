"""svn:externals 的结构化解析、确定性渲染和纯重写计划。

本模块把每一行 external 分成结构化条目或不可变原始 trivia，支持旧/新语法、
operative/peg revision、仓库相对 URL、空行、注释和带引号的本地路径。重写器只
消费文档和前缀规则，使用最长前缀、目标 allowlist、重复路径和循环检查生成报告；
它不导入、不调用 ``SourceProvider``，也不执行任何 SVN 写操作。
"""

from __future__ import annotations

import re
import shlex
import unicodedata
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable
from collections.abc import Sequence
from urllib.parse import urlsplit

from branch.model import BranchValidationError


class ExternalParseError(BranchValidationError):
    """svn:externals 文本不能解析为安全结构化条目时抛出的异常。"""


class ExternalRewriteError(BranchValidationError):
    """external 重写无法形成允许闭包或确定性计划时抛出的异常。"""


class ExternalSyntax(str, Enum):
    """记录 SVN external 使用的新式或旧式字段顺序。"""

    NEW = "new"
    OLD = "old"


_REVISION_PATTERN = re.compile(r"[1-9][0-9]*\Z")
_URL_SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


def _split_line_ending(value: str) -> tuple[str, str]:
    """分离一行的正文和原始换行符。"""
    if value.endswith("\r\n"):
        return value[:-2], "\r\n"
    if value.endswith(("\r", "\n")):
        return value[:-1], value[-1]
    return value, ""


def _validate_revision(value: object, field_name: str) -> int:
    """校验 external revision 是固定正整数而非 HEAD。"""
    if isinstance(value, str) and value.upper() == "HEAD":
        raise ExternalParseError(f"{field_name} 不得使用 HEAD")
    if isinstance(value, str) and _REVISION_PATTERN.fullmatch(value) is not None:
        return int(value)
    if type(value) is not int or _REVISION_PATTERN.fullmatch(str(value)) is None:
        raise ExternalParseError(f"{field_name} 必须是正整数")
    return value


def _validate_local_path(value: object, field_name: str) -> str:
    """校验 external local path 为安全相对路径。"""
    if not isinstance(value, str) or not value.strip():
        raise ExternalParseError(f"{field_name} 必须是非空字符串")
    if "\\" in value or value.startswith("/") or _DRIVE_PATTERN.match(value):
        raise ExternalParseError(f"{field_name} 必须是安全相对路径")
    if any(segment in {"", ".", ".."} for segment in value.split("/")):
        raise ExternalParseError(f"{field_name} 不得路径逃逸或包含空段")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ExternalParseError(f"{field_name} 不得包含控制字符")
    return value


def _is_url_token(value: str) -> bool:
    """判断 token 是否符合 SVN external URL 的明显边界。"""
    return (
        value.startswith(("^/", "../", "./", "/", "//"))
        or _URL_SCHEME_PATTERN.match(value) is not None
    )


def _split_peg_revision(value: str) -> tuple[str, int | None]:
    """从 URL 尾部提取可选 peg revision。"""
    match = re.fullmatch(r"(.+)@([0-9]+|HEAD|head)", value)
    if match is None:
        return value, None
    raw_revision = match.group(2)
    revision = _validate_revision(raw_revision, "peg revision")
    return match.group(1), revision


def _parse_revision_options(tokens: Sequence[str]) -> tuple[list[str], int | None]:
    """提取 ``-rN``/``-r N`` 选项并拒绝未知 option。"""
    remaining: list[str] = []
    operative_revision: int | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-r":
            if index + 1 >= len(tokens):
                raise ExternalParseError("缺少 -r 的 revision")
            raw_revision = tokens[index + 1]
            operative_revision = _validate_revision(raw_revision, "operative revision")
            index += 2
            continue
        if token.startswith("-r"):
            operative_revision = _validate_revision(token[2:], "operative revision")
            index += 1
            continue
        if token.startswith("-"):
            raise ExternalParseError(f"不支持的 external option: {token}")
        remaining.append(token)
        index += 1
    return remaining, operative_revision


def _quote_token(value: str) -> str:
    """按 SVN external 可读语法确定性引用带空白的 token。"""
    if not value or any(
        character.isspace() or character in {'"', "'", "\\"} for character in value
    ):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


@dataclass(frozen=True, slots=True, repr=False)
class ExternalEntry:
    """一个 svn:externals 的结构化 external 条目。

    参数：
        url: external URL，不包含 peg revision；
        local_path: 工作副本中的安全相对目标路径；
        operative_revision: 可选 ``-r`` 固定 revision；
        peg_revision: 可选 URL ``@REV`` peg revision；
        syntax: 原文本字段顺序；
        original_line: 解析时保留的完整原始行，供未修改项无损渲染；
        line_number: 诊断用一基行号。

    返回：
        无；条目不可变，修改通过 ``with_url`` 返回新条目。

    异常：
        URL、路径或 revision 无效时抛 ``ExternalParseError``。

    约束与副作用：
        未修改条目优先返回原始文本；repr 不显示原始行，避免把任意属性值带入日志。
    """

    url: str
    local_path: str
    operative_revision: int | None = None
    peg_revision: int | None = None
    syntax: ExternalSyntax = ExternalSyntax.NEW
    original_line: str = field(default="", repr=False)
    line_number: int = field(default=0, repr=False)
    modified: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        """校验 URL、local path、revision 和语法枚举。"""
        if not isinstance(self.url, str) or not self.url.strip():
            raise ExternalParseError("external url 不得为空")
        if _URL_SCHEME_PATTERN.match(self.url) and urlsplit(self.url).username is not None:
            raise ExternalParseError("external url 不得包含 URL 用户信息")
        if any(character.isspace() for character in self.url):
            raise ExternalParseError("external url 不得包含空白")
        _validate_local_path(self.local_path, "external local_path")
        if self.operative_revision is not None:
            _validate_revision(self.operative_revision, "operative revision")
        if self.peg_revision is not None:
            _validate_revision(self.peg_revision, "peg revision")
        if not isinstance(self.syntax, ExternalSyntax):
            raise ExternalParseError("external syntax 必须是 ExternalSyntax")
        if not isinstance(self.original_line, str):
            raise ExternalParseError("original_line 必须是 str")
        if not isinstance(self.line_number, int) or self.line_number < 0:
            raise ExternalParseError("line_number 必须是非负整数")

    def with_url(self, url: str) -> ExternalEntry:
        """返回只替换 URL 的新 external 条目。

        参数：
            url: 不含 peg revision 的新 URL。

        返回：
            保留原顺序、路径、revision 和换行的已修改条目。

        异常：
            URL 不安全时抛 ``ExternalParseError``。

        约束与副作用：
            不修改当前对象；渲染时只重建这一行。
        """
        return replace(self, url=url, modified=True)

    def render(self) -> str:
        """渲染该条目，未修改时逐字节保留原始行。"""
        if not self.modified and self.original_line:
            return self.original_line
        _, ending = _split_line_ending(self.original_line)
        revision_tokens = (
            [] if self.operative_revision is None else [f"-r{self.operative_revision}"]
        )
        url = self.url if self.peg_revision is None else f"{self.url}@{self.peg_revision}"
        if self.syntax is ExternalSyntax.OLD:
            tokens = [_quote_token(self.local_path), *revision_tokens, _quote_token(url)]
        else:
            tokens = [*revision_tokens, _quote_token(url), _quote_token(self.local_path)]
        return " ".join(tokens) + ending

    def __repr__(self) -> str:
        """返回不包含原始行内容的结构化表示。"""
        return (
            "ExternalEntry("
            f"url={self.url!r}, local_path={self.local_path!r}, "
            f"operative_revision={self.operative_revision!r}, peg_revision={self.peg_revision!r}, "
            f"syntax={self.syntax!r})"
        )


External = ExternalEntry


@dataclass(frozen=True, slots=True, repr=False)
class ExternalLine:
    """保存 external 文档中的一行 trivia 或结构化条目。"""

    raw: str
    entry: ExternalEntry | None
    line_number: int

    def render(self) -> str:
        """返回原始 trivia 或条目的当前渲染结果。"""
        return self.entry.render() if self.entry is not None else self.raw

    def __repr__(self) -> str:
        """返回不直接展示 raw 的安全行表示。"""
        return f"ExternalLine(line_number={self.line_number!r}, entry={self.entry!r})"


@dataclass(frozen=True, slots=True, repr=False)
class ExternalDocument:
    """由不可变行序列组成的 svn:externals 文档。

    参数：
        lines: 按原始顺序保存的 trivia 和 external 行。

    返回：
        无；通过 ``entries`` 查看结构化条目，通过 ``replace_url`` 生成新文档。

    异常：
        行类型错误时抛 ``ExternalParseError``。

    约束与副作用：
        文档没有写文件能力；渲染结果只由内存内容决定。
    """

    lines: tuple[ExternalLine, ...]

    def __post_init__(self) -> None:
        """校验并规范行序列。"""
        lines = tuple(self.lines)
        if not all(isinstance(line, ExternalLine) for line in lines):
            raise ExternalParseError("ExternalDocument.lines 必须只包含 ExternalLine")
        object.__setattr__(self, "lines", lines)

    @property
    def entries(self) -> tuple[ExternalEntry, ...]:
        """返回按文档顺序过滤得到的 external 条目。"""
        return tuple(line.entry for line in self.lines if line.entry is not None)

    def replace_url(self, entry_index: int, url: str) -> ExternalDocument:
        """按 external 条目序号只替换一个 URL。

        参数：
            entry_index: ``entries`` 视角的零基索引。
            url: 不含 peg revision 的新 URL。

        返回：
            保留所有 trivia 和行顺序的新文档。

        异常：
            索引越界或新 URL 非法时抛 ``ExternalParseError``。

        约束与副作用：
            不修改当前文档；其他条目保持原始表示。
        """
        if entry_index < 0:
            raise ExternalParseError("external entry index 不得为负数")
        current = 0
        replaced = False
        new_lines: list[ExternalLine] = []
        for line in self.lines:
            if line.entry is None:
                new_lines.append(line)
                continue
            if current == entry_index:
                new_lines.append(replace(line, entry=line.entry.with_url(url)))
                replaced = True
            else:
                new_lines.append(line)
            current += 1
        if not replaced:
            raise ExternalParseError(f"external entry index 不存在: {entry_index}")
        return ExternalDocument(tuple(new_lines))

    def render(self) -> str:
        """按原始行顺序渲染完整 externals 文本。"""
        return "".join(line.render() for line in self.lines)

    def __iter__(self) -> Iterable[ExternalLine]:
        """按文档顺序迭代所有 trivia 和 external 行。"""
        return iter(self.lines)

    def __repr__(self) -> str:
        """返回不展开原始文本的文档表示。"""
        return f"ExternalDocument(lines={self.lines!r})"


def _parse_external_line(body: str, line_number: int, original_line: str) -> ExternalEntry:
    """解析一条非空非注释 external 行。"""
    lexer = shlex.shlex(body, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise ExternalParseError(f"第 {line_number} 行引号不闭合") from exc
    if not tokens:
        raise ExternalParseError(f"第 {line_number} 行缺少 external 内容")
    try:
        remaining, operative_revision = _parse_revision_options(tokens)
    except ExternalParseError as exc:
        raise ExternalParseError(f"第 {line_number} 行: {exc}") from exc
    url_indices = [index for index, token in enumerate(remaining) if _is_url_token(token)]
    if len(url_indices) != 1 or len(remaining) != 2:
        raise ExternalParseError(f"第 {line_number} 行字段数量或 URL 不合法")
    url_index = url_indices[0]
    local_index = 1 - url_index
    url_token = remaining[url_index]
    local_path = remaining[local_index]
    url, peg_revision = _split_peg_revision(url_token)
    try:
        _validate_local_path(local_path, f"第 {line_number} 行 local path")
    except ExternalParseError as exc:
        raise ExternalParseError(str(exc)) from exc
    syntax = ExternalSyntax.NEW if url_index == 0 else ExternalSyntax.OLD
    try:
        return ExternalEntry(
            url=url,
            local_path=local_path,
            operative_revision=operative_revision,
            peg_revision=peg_revision,
            syntax=syntax,
            original_line=original_line,
            line_number=line_number,
        )
    except ExternalParseError as exc:
        raise ExternalParseError(f"第 {line_number} 行: {exc}") from exc


def parse_externals(text: str) -> ExternalDocument:
    """把 svn:externals 属性文本解析为保留原始行的结构化文档。

    参数：
        text: SVN 属性原始文本，支持 LF、CRLF、空行和注释。

    返回：
        ``ExternalDocument``，其中 external 行为 ``ExternalEntry``，其他行按原文保存。

    异常：
        输入非字符串、行语法错误、HEAD、非法 revision 或路径逃逸时抛
        ``ExternalParseError``，消息包含一基行号。

    约束与副作用：
        纯文本解析；不调用 SVN、不解析 URL 远端状态、不物化工作副本。
    """
    if not isinstance(text, str):
        raise ExternalParseError("externals text 必须是字符串")
    if "\x00" in text:
        raise ExternalParseError("externals text 不得包含 NUL")
    lines: list[ExternalLine] = []
    for line_number, raw in enumerate(text.splitlines(keepends=True), start=1):
        body, _ = _split_line_ending(raw)
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(ExternalLine(raw=raw, entry=None, line_number=line_number))
            continue
        lines.append(
            ExternalLine(
                raw=raw,
                entry=_parse_external_line(body, line_number, raw),
                line_number=line_number,
            )
        )
    if text and not lines:
        lines.append(ExternalLine(raw=text, entry=None, line_number=1))
    return ExternalDocument(tuple(lines))


def render_externals(document: ExternalDocument) -> str:
    """渲染结构化 externals 文档并保留未修改行。

    参数：
        document: ``parse_externals`` 返回的不可变文档。

    返回：
        确定性的属性文本字符串。

    异常：
        输入不是 ``ExternalDocument`` 时抛 ``ExternalParseError``。

    约束与副作用：
        只生成内存字符串，不写工作副本或调用 SVN。
    """
    if not isinstance(document, ExternalDocument):
        raise ExternalParseError("document 必须是 ExternalDocument")
    return document.render()


def _normalize_rule_prefix(value: object, field_name: str) -> str:
    """为纯重写规则执行前缀安全规范化。"""
    if not isinstance(value, str) or not value.strip():
        raise ExternalRewriteError(f"{field_name} 必须是非空字符串")
    if "\\" in value or any(segment in {".", ".."} for segment in value.split("/")):
        raise ExternalRewriteError(f"{field_name} 不得路径逃逸")
    if _URL_SCHEME_PATTERN.match(value) is not None and urlsplit(value).username is not None:
        raise ExternalRewriteError(f"{field_name} 不得包含 URL 用户信息")
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ExternalRewriteError(f"{field_name} 不得包含空白或控制字符")
    return value.rstrip("/")


def _prefix_matches(prefix: str, value: str) -> bool:
    """判断重写规则是否命中完整 URL 前缀边界。"""
    return value == prefix or value.startswith(prefix + "/")


@dataclass(frozen=True, slots=True)
class ExternalRewriteRule:
    """一条不依赖 SourceProvider 的 external URL 重写规则。

    参数：
        name: 稳定规则名称。
        source_prefix: external URL 源前缀。
        target_prefix: external URL 目标前缀。

    返回：
        无；构造后规则不可变。

    异常：
        名称或前缀非法时抛 ``ExternalRewriteError``。

    约束与副作用：
        规则本身不访问外部系统；跨规则重复和循环在 rewrite 计划阶段检查。
    """

    name: str
    source_prefix: str
    target_prefix: str

    def __post_init__(self) -> None:
        """校验规则名称与前缀。"""
        if not isinstance(self.name, str) or not self.name.strip():
            raise ExternalRewriteError("ExternalRewriteRule.name 不得为空")
        _normalize_rule_prefix(self.source_prefix, "source_prefix")
        _normalize_rule_prefix(self.target_prefix, "target_prefix")

    @property
    def source(self) -> str:
        """返回 source 前缀别名。"""
        return self.source_prefix

    @property
    def target(self) -> str:
        """返回 target 前缀别名。"""
        return self.target_prefix


@dataclass(frozen=True, slots=True)
class ExternalRewriteChange:
    """记录一条 external URL 的纯文本重写差异。"""

    local_path: str
    old_url: str
    new_url: str
    rule_name: str


@dataclass(frozen=True, slots=True)
class ExternalClosureReport:
    """报告 external 重写后的闭包和未匹配项。"""

    rewritten_local_paths: tuple[str, ...]
    unmatched_local_paths: tuple[str, ...]
    target_urls: tuple[str, ...]
    closed: bool
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class ExternalRewriteResult:
    """保存重写文档、差异和闭包报告的不可变结果。"""

    document: ExternalDocument
    changes: tuple[ExternalRewriteChange, ...]
    report: ExternalClosureReport

    @property
    def rendered(self) -> str:
        """返回重写后确定性 externals 文本。"""
        return self.document.render()

    @property
    def text(self) -> str:
        """返回 rendered 的文本兼容性别名。"""
        return self.rendered

    def __repr__(self) -> str:
        """返回不展开原始属性文本的结果摘要。"""
        return f"ExternalRewriteResult(changes={self.changes!r}, report={self.report!r})"


ExternalRewritePlan = ExternalRewriteResult


def _find_rule(rules: tuple[ExternalRewriteRule, ...], value: str) -> ExternalRewriteRule | None:
    """按 source 前缀字节长度选择唯一最长规则。"""
    matches = [rule for rule in rules if _prefix_matches(rule.source_prefix, value)]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item.source_prefix.encode("utf-8")))


def _validate_rule_collection(rules: tuple[ExternalRewriteRule, ...]) -> None:
    """拒绝重复 source 前缀和 source-target 有向循环。"""
    if not rules:
        raise ExternalRewriteError("重写规则不得为空")
    seen: set[str] = set()
    for rule in rules:
        if rule.source_prefix in seen:
            raise ExternalRewriteError(f"重写规则存在重复 source 前缀: {rule.source_prefix}")
        seen.add(rule.source_prefix)

    edges: dict[str, str] = {}
    for rule in rules:
        candidates = [
            candidate
            for candidate in rules
            if _prefix_matches(candidate.source_prefix, rule.target_prefix)
        ]
        if candidates:
            next_rule = max(candidates, key=lambda item: len(item.source_prefix.encode("utf-8")))
            edges[rule.source_prefix] = next_rule.source_prefix

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        """深度优先检查单个规则节点的循环。"""
        if node in visiting:
            raise ExternalRewriteError("重写规则存在循环映射")
        if node in visited:
            return
        visiting.add(node)
        next_node = edges.get(node)
        if next_node is not None:
            visit(next_node)
        visiting.remove(node)
        visited.add(node)

    for source_prefix in sorted(edges, key=lambda item: item.encode("utf-8")):
        visit(source_prefix)


def _validate_local_path_uniqueness(document: ExternalDocument) -> None:
    """检查所有 external local path 不重复。"""
    seen: set[str] = set()
    for entry in document.entries:
        if entry.local_path in seen:
            raise ExternalRewriteError(f"external local path 重复: {entry.local_path}")
        seen.add(entry.local_path)


def _validate_allowlist(url: str, allowlist: tuple[str, ...]) -> bool:
    """判断重写目标是否命中至少一个仓库 allowlist 前缀。"""
    if not allowlist:
        return True
    return any(_prefix_matches(prefix, url) for prefix in allowlist)


def rewrite_externals(
    document: ExternalDocument | str,
    rules: Sequence[ExternalRewriteRule],
    *,
    allowed_repositories: Sequence[str] = (),
    unmatched_policy: str = "preserve",
    allowlist: Sequence[str] | None = None,
) -> ExternalRewriteResult:
    """根据前缀规则生成纯 external 重写计划和闭包报告。

    参数：
        document: 已解析文档，或待解析的原始 externals 文本。
        rules: Source/Target 规则；输入排列不影响最长前缀选择。
        allowed_repositories: 可选目标仓库前缀 allowlist。
        unmatched_policy: ``preserve`` 保留未匹配项，``fail`` 直接失败。
        allowlist: ``allowed_repositories`` 的配置字段兼容别名；不能与其同时指定。

    返回：
        ``ExternalRewriteResult``，包含重写文档、差异和确定性闭包报告。

    异常：
        规则空集、重复/循环、未匹配 fail、目标不在 allowlist 或 local path 重复
        时抛 ``ExternalRewriteError``。

    约束与副作用：
        只进行内存计算；不调用 ``SourceProvider``、不探测远端 URL、不运行 svn 写操作。
    """
    parsed = parse_externals(document) if isinstance(document, str) else document
    if not isinstance(parsed, ExternalDocument):
        raise ExternalRewriteError("document 必须是 ExternalDocument 或 str")
    normalized_rules = tuple(rules)
    if not all(isinstance(rule, ExternalRewriteRule) for rule in normalized_rules):
        raise ExternalRewriteError("rules 必须只包含 ExternalRewriteRule")
    _validate_rule_collection(normalized_rules)
    _validate_local_path_uniqueness(parsed)
    if unmatched_policy not in {"preserve", "fail"}:
        raise ExternalRewriteError("unmatched_policy 只能是 preserve 或 fail")
    if allowlist is not None:
        if allowed_repositories:
            raise ExternalRewriteError("allowed_repositories 与 allowlist 不能同时指定")
        allowed_repositories = allowlist
    normalized_allowlist = tuple(
        sorted(
            {
                _normalize_rule_prefix(value, "allowed_repositories")
                for value in allowed_repositories
            },
            key=lambda item: item.encode("utf-8"),
        )
    )

    rewritten = parsed
    changes: list[ExternalRewriteChange] = []
    rewritten_paths: list[str] = []
    unmatched_paths: list[str] = []
    target_urls: set[str] = set()
    entry_index = 0
    for entry in parsed.entries:
        rule = _find_rule(normalized_rules, entry.url)
        if rule is None:
            unmatched_paths.append(entry.local_path)
            if unmatched_policy == "fail":
                raise ExternalRewriteError(f"external 未匹配任何 source 前缀: {entry.url}")
            entry_index += 1
            continue
        suffix = entry.url[len(rule.source_prefix) :]
        new_url = rule.target_prefix + suffix
        if not _validate_allowlist(new_url, normalized_allowlist):
            raise ExternalRewriteError(f"重写目标不在 allowlist: {new_url}")
        target_urls.add(new_url)
        rewritten_paths.append(entry.local_path)
        if new_url != entry.url:
            changes.append(
                ExternalRewriteChange(
                    local_path=entry.local_path,
                    old_url=entry.url,
                    new_url=new_url,
                    rule_name=rule.name,
                )
            )
            rewritten = rewritten.replace_url(entry_index, new_url)
        entry_index += 1

    report = ExternalClosureReport(
        rewritten_local_paths=tuple(sorted(rewritten_paths, key=lambda item: item.encode("utf-8"))),
        unmatched_local_paths=tuple(sorted(unmatched_paths, key=lambda item: item.encode("utf-8"))),
        target_urls=tuple(sorted(target_urls, key=lambda item: item.encode("utf-8"))),
        closed=not unmatched_paths,
    )
    return ExternalRewriteResult(document=rewritten, changes=tuple(changes), report=report)


def rewrite_external_plan(
    document: ExternalDocument | str,
    rules: Sequence[ExternalRewriteRule],
    *,
    allowed_repositories: Sequence[str] = (),
    unmatched_policy: str = "preserve",
    allowlist: Sequence[str] | None = None,
) -> ExternalRewriteResult:
    """提供 ``rewrite_externals`` 的计划语义别名。"""
    return rewrite_externals(
        document,
        rules,
        allowed_repositories=allowed_repositories,
        unmatched_policy=unmatched_policy,
        allowlist=allowlist,
    )
