"""分支构建的只读仓库快照、externals 摘要和前置条件验证。

本模块定义 branch 自己需要的最小只读 ``SourceProvider`` 协议。提供器只能通过
``inspect`` 返回已经固定 revision 的结构化节点快照，validator 不接受、保存或调用
任何 SVN copy、delete、propset 或其他写入口。快照中的 externals 使用内容摘要绑定，
使后续计划能够检测源属性漂移而不依赖不透明的原始命令输出。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast
from urllib.parse import urlsplit

from branch.externals import ExternalParseError, parse_externals
from branch.model import BranchSource, BranchTarget, BranchValidationError


class BranchPreconditionError(BranchValidationError):
    """只读仓库快照不满足分支计划前置条件时抛出的异常。

    职责：
        区分快照身份、节点存在性、节点类型和 revision 冲突，供 planner 在不访问
        SVN 写端口的情况下拒绝不安全输入。

    参数：
        继承 ``BranchValidationError`` 的标准异常参数。

    返回：
        无；本类只作为异常类型使用。

    异常：
        自身即异常，不在构造时访问外部系统。

    约束与副作用：
        异常消息只包含公开的字段或 URL 摘要，不包含 externals 原始值和秘密。
    """


class RepositoryNodeType(str, Enum):
    """仓库节点的有限类型集合。"""

    DIRECTORY = "directory"
    FILE = "file"
    MISSING = "missing"


NodeKind = RepositoryNodeType
"""``RepositoryNodeType`` 的兼容性别名。"""


_REVISION_PATTERN = re.compile(r"[1-9][0-9]*\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE_PATTERN = re.compile(r"[A-Za-z]:")


def _validate_text(value: object, field_name: str) -> str:
    """校验快照文本非空且不含空白或控制字符。"""
    if not isinstance(value, str) or not value.strip():
        raise BranchPreconditionError(f"{field_name} 必须是非空字符串")
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise BranchPreconditionError(f"{field_name} 不得包含空白或控制字符")
    return value


def _validate_url(value: object, field_name: str) -> str:
    """校验仓库 URL 不含用户信息、fragment 或路径逃逸。"""
    url = _validate_text(value, field_name)
    parsed = urlsplit(url)
    if parsed.username is not None or parsed.password is not None:
        raise BranchPreconditionError(f"{field_name} 不得包含 URL 用户信息")
    if parsed.fragment:
        raise BranchPreconditionError(f"{field_name} 不得包含 URL fragment")
    path = parsed.path if parsed.scheme else url
    if any(segment == ".." for segment in path.replace("\\", "/").split("/")):
        raise BranchPreconditionError(f"{field_name} 不得路径逃逸")
    return url.rstrip("/") if url != "/" else url


def _validate_relative_path(value: object, field_name: str) -> str:
    """校验 externals 属性路径是客户端逻辑相对路径。"""
    path = _validate_text(value, field_name)
    if (
        path.startswith("/")
        or "\\" in path
        or _DRIVE_PATTERN.match(path) is not None
        or any(segment in {"", ".", ".."} for segment in path.split("/"))
    ):
        raise BranchPreconditionError(f"{field_name} 必须是安全相对路径")
    return path


def _validate_revision(value: object, field_name: str) -> int:
    """校验 snapshot revision 是不含 HEAD 的正整数。"""
    if type(value) is not int or _REVISION_PATTERN.fullmatch(str(value)) is None:
        raise BranchPreconditionError(f"{field_name} 必须是正整数固定 revision")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class ExternalPropertySummary:
    """保存一个 ``svn:externals`` 属性的路径、值和 SHA-256 摘要。

    参数：
        path: 属性所在的仓库逻辑相对路径。
        value: 已由只读提供器读取的 externals 文本。
        sha256: 可选的提供器摘要；省略时由 value 计算，提供时必须匹配。

    返回：
        无；构造后摘要和文本不可变。

    异常：
        路径、属性文本、external 语法或摘要不合法时抛 ``BranchPreconditionError``。

    约束与副作用：
        只在内存中解析和摘要文本；repr 不显示 externals 原文，避免意外泄漏凭据。
    """

    path: str
    value: str
    sha256: str | None = None

    def __post_init__(self) -> None:
        """校验路径、解析属性语法并绑定内容摘要。"""
        _validate_relative_path(self.path, "ExternalPropertySummary.path")
        if not isinstance(self.value, str):
            raise BranchPreconditionError("ExternalPropertySummary.value 必须是字符串")
        try:
            parse_externals(self.value)
        except ExternalParseError as exc:
            raise BranchPreconditionError("svn:externals 属性无法解析") from exc
        digest = hashlib.sha256(self.value.encode("utf-8")).hexdigest()
        if self.sha256 is not None and _SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise BranchPreconditionError("ExternalPropertySummary.sha256 必须是小写 SHA-256")
        if self.sha256 is not None and self.sha256 != digest:
            raise BranchPreconditionError("externals 属性摘要与内容不一致")
        object.__setattr__(self, "sha256", digest)

    @property
    def digest(self) -> str:
        """返回 externals 内容的 SHA-256 摘要。"""
        return cast(str, self.sha256)

    @property
    def summary(self) -> str:
        """返回摘要别名，便于审计代码按 summary 语义读取。"""
        return self.digest

    def __repr__(self) -> str:
        """返回不展开 externals 原文的安全表示。"""
        return f"ExternalPropertySummary(path={self.path!r}, sha256={self.digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class RepositoryNodeSnapshot:
    """表示一次只读查询得到的仓库节点状态。

    参数：
        url: 本次查询对应的规范仓库 URL。
        exists: 节点在查询 revision 是否存在。
        node_type: 节点类型；不存在节点必须使用 ``MISSING``。
        externals: 节点上的结构化 externals 摘要集合。

    返回：
        无；字段和 externals 集合均不可变。

    异常：
        URL、存在性和节点类型组合不一致时抛 ``BranchPreconditionError``。

    约束与副作用：
        只表示查询结果，不提供写入、删除或刷新节点的方法。
    """

    url: str
    exists: bool
    node_type: RepositoryNodeType
    externals: tuple[ExternalPropertySummary, ...] = ()

    def __post_init__(self) -> None:
        """校验节点状态组合并规范 externals 顺序。"""
        _validate_url(self.url, "RepositoryNodeSnapshot.url")
        if type(self.exists) is not bool:
            raise BranchPreconditionError("RepositoryNodeSnapshot.exists 必须是 bool")
        if not isinstance(self.node_type, RepositoryNodeType):
            raise BranchPreconditionError("RepositoryNodeSnapshot.node_type 类型无效")
        if self.exists and self.node_type is RepositoryNodeType.MISSING:
            raise BranchPreconditionError("存在节点不能使用 MISSING 类型")
        if not self.exists and self.node_type is not RepositoryNodeType.MISSING:
            raise BranchPreconditionError("不存在节点必须使用 MISSING 类型")
        externals = tuple(self.externals)
        if not all(isinstance(item, ExternalPropertySummary) for item in externals):
            raise BranchPreconditionError("RepositoryNodeSnapshot.externals 类型无效")
        if len({item.path for item in externals}) != len(externals):
            raise BranchPreconditionError("externals 属性路径重复")
        object.__setattr__(
            self,
            "externals",
            tuple(sorted(externals, key=lambda item: item.path.encode("utf-8"))),
        )

    def __repr__(self) -> str:
        """返回节点公开字段和摘要数量，不展开 externals 原文。"""
        return (
            "RepositoryNodeSnapshot("
            f"url={self.url!r}, exists={self.exists!r}, node_type={self.node_type!r}, "
            f"external_count={len(self.externals)!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RepositorySnapshot:
    """表示一个仓库 URL 在固定 revision 下的只读快照。

    参数：
        repository_uuid: 服务器返回的仓库 UUID。
        revision: 服务器返回的固定正整数 revision。
        node: 查询 URL 对应的节点结果。
        externals: 可选的快照级 externals 摘要，供只读假实现直接提供属性摘要。

    返回：
        无；构造后不能刷新或改变外部状态。

    异常：
        仓库身份、revision 或 externals 类型不合法时抛 ``BranchPreconditionError``。

    约束与副作用：
        这是输入 DTO，不执行 I/O；``external_properties`` 返回规范化的只读合并视图。
    """

    repository_uuid: str
    revision: int
    node: RepositoryNodeSnapshot
    externals: tuple[ExternalPropertySummary, ...] = ()

    def __post_init__(self) -> None:
        """校验仓库身份、revision 和快照级属性集合。"""
        _validate_text(self.repository_uuid, "RepositorySnapshot.repository_uuid")
        _validate_revision(self.revision, "RepositorySnapshot.revision")
        if not isinstance(self.node, RepositoryNodeSnapshot):
            raise BranchPreconditionError("RepositorySnapshot.node 类型无效")
        externals = tuple(self.externals)
        if not all(isinstance(item, ExternalPropertySummary) for item in externals):
            raise BranchPreconditionError("RepositorySnapshot.externals 类型无效")
        object.__setattr__(
            self,
            "externals",
            tuple(sorted(externals, key=lambda item: item.path.encode("utf-8"))),
        )

    @property
    def external_properties(self) -> tuple[ExternalPropertySummary, ...]:
        """返回节点级和快照级 externals 摘要的去重合并结果。"""
        by_path = {item.path: item for item in self.node.externals}
        by_path.update({item.path: item for item in self.externals})
        return tuple(
            by_path[path] for path in sorted(by_path, key=lambda item: item.encode("utf-8"))
        )

    def __repr__(self) -> str:
        """返回不展开属性原文的安全快照表示。"""
        return (
            "RepositorySnapshot("
            f"repository_uuid={self.repository_uuid!r}, revision={self.revision!r}, "
            f"node={self.node!r}, external_count={len(self.external_properties)!r})"
        )


class ReadOnlySourceProvider(Protocol):
    """只允许读取固定 revision 或当前目标状态的源码提供器协议。"""

    def inspect(self, url: str, revision: int | None) -> RepositorySnapshot:
        """读取 URL 的节点和属性摘要，不执行 SVN 写操作。"""
        ...


SourceProvider = ReadOnlySourceProvider
"""``ReadOnlySourceProvider`` 的 branch 兼容性别名。"""


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedBranchSnapshot:
    """保存已通过源/目标前置条件验证的成对只读快照。

    参数：
        source: 源 URL 的固定 revision 快照。
        target: 目标 URL 的当前固定 revision 快照。

    返回：
        无；planner 只应消费 validator 返回的此类型。

    异常：
        快照类型不正确或源/目标仓库身份不一致时抛 ``BranchPreconditionError``。

    约束与副作用：
        对象不含 provider 引用，也不能触发重新查询；创建 plan 后原快照保持不变。
    """

    source: RepositorySnapshot
    target: RepositorySnapshot

    def __post_init__(self) -> None:
        """校验成对快照的最小结构不变量。"""
        if not isinstance(self.source, RepositorySnapshot):
            raise BranchPreconditionError("ValidatedBranchSnapshot.source 类型无效")
        if not isinstance(self.target, RepositorySnapshot):
            raise BranchPreconditionError("ValidatedBranchSnapshot.target 类型无效")
        if self.source.repository_uuid != self.target.repository_uuid:
            raise BranchPreconditionError("源和目标必须属于同一仓库")
        if self.source.node.exists is not True:
            raise BranchPreconditionError("源节点必须存在")
        if self.source.node.node_type is not RepositoryNodeType.DIRECTORY:
            raise BranchPreconditionError("源节点必须是目录")
        if self.target.node.exists:
            raise BranchPreconditionError("目标节点已存在")

    @property
    def source_revision(self) -> int:
        """返回源快照的固定 revision。"""
        return self.source.revision

    @property
    def expected_repository_revision(self) -> int:
        """返回计划应锁定的目标查询 revision。"""
        return self.target.revision

    @property
    def repository_uuid(self) -> str:
        """返回已验证的仓库 UUID。"""
        return self.source.repository_uuid

    def __repr__(self) -> str:
        """返回源/目标 revision 和 UUID 摘要，不展开属性文本。"""
        return (
            "ValidatedBranchSnapshot("
            f"repository_uuid={self.repository_uuid!r}, source_revision={self.source_revision!r}, "
            f"expected_repository_revision={self.expected_repository_revision!r})"
        )


BranchRepositorySnapshot = ValidatedBranchSnapshot
"""``ValidatedBranchSnapshot`` 的语义兼容性别名。"""


class BranchPreconditionValidator:
    """使用显式只读提供器验证 branch 复制前置条件。"""

    def __init__(self, provider: ReadOnlySourceProvider) -> None:
        """保存只读 provider，不保存任何写端口或凭据。

        参数：
            provider: 实现 ``inspect`` 的显式只读源码提供器。

        返回：
            无；validator 不主动查询，只有 ``validate`` 被调用时才读取。

        异常：
            provider 没有可调用 ``inspect`` 方法时抛 ``TypeError``。

        约束与副作用：
            构造不执行 I/O；本类不会探测或调用 provider 上的写方法。
        """
        if not callable(getattr(provider, "inspect", None)):
            raise TypeError("provider 必须实现只读 inspect(url, revision) 方法")
        self._provider = provider

    def validate(
        self,
        source: BranchSource,
        target: BranchTarget,
    ) -> ValidatedBranchSnapshot:
        """读取并验证源/目标快照，返回 planner 可消费的不可变结果。

        参数：
            source: 已声明固定 revision 和仓库 UUID 的源引用。
            target: 已声明仓库 UUID、但必须尚不存在的目标引用。

        返回：
            ``ValidatedBranchSnapshot``，包含源固定 revision 和目标期望 revision。

        异常：
            引用类型错误、跨仓库、revision 漂移、源非目录或目标已存在时抛
            ``BranchPreconditionError``。

        约束与副作用：
            只调用 provider.inspect 两次；不会调用写操作、删除目标或修改快照。
        """
        if not isinstance(source, BranchSource):
            raise BranchPreconditionError("source 必须是 BranchSource")
        if not isinstance(target, BranchTarget):
            raise BranchPreconditionError("target 必须是 BranchTarget")
        if source.repository_uuid != target.repository_uuid:
            raise BranchPreconditionError("源和目标必须属于同一仓库")

        source_snapshot = self._inspect(source.url, source.revision, "源")
        target_snapshot = self._inspect(target.url, None, "目标")
        if source_snapshot.repository_uuid != source.repository_uuid:
            raise BranchPreconditionError("源快照 repository UUID 不匹配")
        if target_snapshot.repository_uuid != target.repository_uuid:
            raise BranchPreconditionError("目标快照 repository UUID 不匹配")
        if source_snapshot.revision != source.revision:
            raise BranchPreconditionError("源快照 revision 与固定 revision 不一致")
        if source_snapshot.node.url != source.url:
            raise BranchPreconditionError("源快照 URL 与请求 URL 不一致")
        if target_snapshot.node.url != target.url:
            raise BranchPreconditionError("目标快照 URL 与请求 URL 不一致")
        if not source_snapshot.node.exists:
            raise BranchPreconditionError("源节点不存在")
        if source_snapshot.node.node_type is not RepositoryNodeType.DIRECTORY:
            raise BranchPreconditionError("源节点必须是目录")
        if target_snapshot.node.exists:
            raise BranchPreconditionError("目标节点已存在")
        if target_snapshot.node.node_type is not RepositoryNodeType.MISSING:
            raise BranchPreconditionError("不存在目标节点必须是 MISSING 类型")
        return ValidatedBranchSnapshot(source_snapshot, target_snapshot)

    def validate_preconditions(
        self,
        source: BranchSource,
        target: BranchTarget,
    ) -> ValidatedBranchSnapshot:
        """提供 ``validate`` 的语义别名，保持调用方命名清晰。"""
        return self.validate(source, target)

    def _inspect(
        self,
        url: str,
        revision: int | None,
        subject: str,
    ) -> RepositorySnapshot:
        """执行一次只读 inspect 并检查返回 DTO 类型。"""
        result = self._provider.inspect(url, revision)
        if not isinstance(result, RepositorySnapshot):
            raise BranchPreconditionError(f"{subject} inspect 必须返回 RepositorySnapshot")
        return result


__all__ = [
    "BranchPreconditionError",
    "BranchPreconditionValidator",
    "BranchRepositorySnapshot",
    "ExternalPropertySummary",
    "NodeKind",
    "ReadOnlySourceProvider",
    "RepositoryNodeSnapshot",
    "RepositoryNodeType",
    "RepositorySnapshot",
    "SourceProvider",
    "ValidatedBranchSnapshot",
]
