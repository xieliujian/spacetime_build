"""分支构建第一批使用的不可变领域模型。

本模块只表达固定源码引用、目标引用、复制操作、属性变更和受控结果状态。
模型在构造边界拒绝 HEAD、空引用、控制字符和路径逃逸，不访问 SVN、文件系统
或秘密服务；所有序列字段在边界规范为 tuple，保证计划与诊断可以确定性比较。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, cast
from urllib.parse import urlsplit


class BranchValidationError(ValueError):
    """分支领域值违反安全或状态不变量时抛出的异常。

    职责：
        统一表示 branch 模型、映射规则和结果状态的局部校验失败，供调用方在
        不依赖具体实现细节的情况下捕获。

    参数：
        继承 ``ValueError`` 的标准异常参数。

    返回：
        无；本类只作为异常类型使用。

    异常：
        自身即异常，不在构造阶段访问外部资源。

    约束与副作用：
        异常消息不得包含秘密值；构造和抛出不产生 I/O 或 SVN 副作用。
    """


_REVISION_PATTERN = re.compile(r"[1-9][0-9]*\Z")
_DRIVE_PATTERN = re.compile(r"[A-Za-z]:")


def _validate_text(value: object, field_name: str) -> str:
    """校验引用文本为非空且不含空白或控制字符。

    参数：
        value: 待校验的运行时值。
        field_name: 错误消息中的字段名。

    返回：
        原始字符串；不裁剪其内容，避免改变 SVN 引用语义。

    异常：
        类型错误、空文本、空白文本或控制字符会抛 ``BranchValidationError``。

    约束与副作用：
        只做内存校验，不访问 URL、路径或秘密提供器。
    """
    if not isinstance(value, str):
        raise BranchValidationError(f"{field_name} 必须是字符串")
    if not value.strip():
        raise BranchValidationError(f"{field_name} 不得为空")
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise BranchValidationError(f"{field_name} 不得包含空白或控制字符")
    return value


def _validate_url(value: object, field_name: str) -> str:
    """校验 SVN 引用 URL，并拒绝可能承载秘密的用户信息。

    参数：
        value: 待校验的 URL 或 SVN 仓库相对引用。
        field_name: 错误消息中的字段名。

    返回：
        原始合法 URL。

    异常：
        空引用、用户信息、URL 片段或路径逃逸会抛 ``BranchValidationError``。

    约束与副作用：
        不解析 DNS、不发起网络请求；URL 不包含密码等秘密，模型 repr 可安全使用。
    """
    url = _validate_text(value, field_name)
    if "@" in url and urlsplit(url).username is not None:
        raise BranchValidationError(f"{field_name} 不得包含 URL 用户信息")
    if "#" in url:
        raise BranchValidationError(f"{field_name} 不得包含 URL fragment")
    path = urlsplit(url).path if "://" in url else url
    if any(segment == ".." for segment in path.replace("\\", "/").split("/")):
        raise BranchValidationError(f"{field_name} 不得路径逃逸")
    return url.rstrip("/") if url != "/" else url


def _validate_revision(value: object, field_name: str = "revision") -> int:
    """校验 revision 是严格的正整数，拒绝 HEAD 和布尔值。

    参数：
        value: 待校验 revision，可以是任意运行时值。
        field_name: 错误消息中的字段名。

    返回：
        原始正整数。

    异常：
        ``HEAD``、字符串数字、零、负数、布尔值及其他类型会抛异常。

    约束与副作用：
        只做固定 revision 校验，不向 SVN 查询当前 HEAD。
    """
    if isinstance(value, str) and value.upper() == "HEAD":
        raise BranchValidationError(f"{field_name} 不得使用 HEAD")
    if type(value) is not int or value <= 0:
        raise BranchValidationError(f"{field_name} 必须是正整数固定 revision")
    if _REVISION_PATTERN.fullmatch(str(value)) is None:
        raise BranchValidationError(f"{field_name} 必须是正整数固定 revision")
    return value


def _validate_relative_path(value: object, field_name: str) -> str:
    """校验分支逻辑路径是安全的 slash 分隔相对路径。

    参数：
        value: 待校验的逻辑路径。
        field_name: 错误消息中的字段名。

    返回：
        原始 slash 分隔相对路径。

    异常：
        绝对路径、反斜杠、空段、点段、盘符路径或路径逃逸会抛异常。

    约束与副作用：
        不调用 ``Path.resolve``，避免把客户端逻辑路径绑定到本机文件系统。
    """
    path = _validate_text(value, field_name)
    if "\\" in path or path.startswith("/") or _DRIVE_PATTERN.match(path):
        raise BranchValidationError(f"{field_name} 必须是安全相对路径")
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise BranchValidationError(f"{field_name} 不得路径逃逸或包含空段")
    return path


@dataclass(frozen=True, slots=True, repr=False)
class BranchSource:
    """描述已经固定 revision 的 SVN 源引用。

    参数：
        url: 不含用户信息和 fragment 的源码 URL。
        repository_uuid: 非空仓库身份标识。
        revision: 严格正整数；``HEAD`` 必须在适配器层解析后才能进入模型。

    返回：
        无；构造后字段不可变。

    异常：
        任一引用字段为空、含控制字符或 revision 未固定时抛异常。

    约束与副作用：
        不连接 SVN；自定义 repr 只显示安全 URL 与仓库身份摘要，不携带秘密。
    """

    url: str
    repository_uuid: str
    revision: int

    def __post_init__(self) -> None:
        """校验源 URL、仓库 UUID 和固定 revision。"""
        _validate_url(self.url, "BranchSource.url")
        _validate_text(self.repository_uuid, "BranchSource.repository_uuid")
        _validate_revision(self.revision, "BranchSource.revision")

    def __repr__(self) -> str:
        """返回不包含 URL 用户信息的稳定调试表示。"""
        return (
            "BranchSource("
            f"url={self.url!r}, repository_uuid={self.repository_uuid!r}, "
            f"revision={self.revision!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class BranchTarget:
    """描述 SVN 分支复制的目标引用。

    参数：
        url: 不含用户信息和 fragment 的目标 URL。
        repository_uuid: 非空仓库身份标识。

    返回：
        无；构造后字段不可变。

    异常：
        URL、仓库 UUID 无效时抛 ``BranchValidationError``。

    约束与副作用：
        不检查目标是否已存在；存在性属于后续只读 precondition 检查。
    """

    url: str
    repository_uuid: str

    def __post_init__(self) -> None:
        """校验目标 URL 和仓库 UUID。"""
        _validate_url(self.url, "BranchTarget.url")
        _validate_text(self.repository_uuid, "BranchTarget.repository_uuid")

    def __repr__(self) -> str:
        """返回不包含 URL 用户信息的稳定调试表示。"""
        return f"BranchTarget(url={self.url!r}, repository_uuid={self.repository_uuid!r})"


@dataclass(frozen=True, slots=True, repr=False)
class BranchCopy:
    """描述一个固定 revision 的源到目标复制操作。

    参数：
        source: 已固定 revision 的源引用。
        target: 目标引用。
        source_path: 源引用下的安全相对路径。
        target_path: 目标引用下的安全相对路径。

    返回：
        无；构造后字段不可变。

    异常：
        源目标相同、路径逃逸或引用类型不符时抛异常。

    约束与副作用：
        ``revision`` 属性只读映射到 source revision；本类不执行 copy。
    """

    source: BranchSource
    target: BranchTarget
    source_path: str
    target_path: str

    def __post_init__(self) -> None:
        """校验复制端点和两个逻辑路径。"""
        if not isinstance(self.source, BranchSource):
            raise BranchValidationError("BranchCopy.source 必须是 BranchSource")
        if not isinstance(self.target, BranchTarget):
            raise BranchValidationError("BranchCopy.target 必须是 BranchTarget")
        if (
            self.source.url == self.target.url
            and self.source.repository_uuid == self.target.repository_uuid
        ):
            raise BranchValidationError("BranchCopy 的源和目标必须不同")
        _validate_relative_path(self.source_path, "BranchCopy.source_path")
        _validate_relative_path(self.target_path, "BranchCopy.target_path")

    @property
    def revision(self) -> int:
        """返回复制操作使用的固定源 revision。"""
        return self.source.revision

    def __repr__(self) -> str:
        """返回不包含潜在属性值的稳定复制操作表示。"""
        return (
            "BranchCopy("
            f"source={self.source!r}, target={self.target!r}, "
            f"source_path={self.source_path!r}, target_path={self.target_path!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PropertyChange:
    """描述一个工作树逻辑路径上的 SVN 属性替换。

    参数：
        path: 安全相对逻辑路径。
        property_name: 非空 SVN 属性名，例如 ``svn:externals``。
        old_value: 旧属性值摘要或完整值；分支计划中的 ``svn:externals`` 使用
            ``sha256:<digest>``，``None`` 表示属性不存在。
        new_value: 新属性值；``None`` 表示删除属性。

    返回：
        无；构造后字段不可变。

    异常：
        路径、属性名或值类型无效时抛 ``BranchValidationError``。

    约束与副作用：
        属性值可能包含敏感文本，因此 repr 只显示路径和属性名，不显示值。
    """

    path: str
    property_name: str
    old_value: str | None
    new_value: str | None

    def __post_init__(self) -> None:
        """校验属性变更的路径、名称和可空文本值。"""
        _validate_relative_path(self.path, "PropertyChange.path")
        _validate_text(self.property_name, "PropertyChange.property_name")
        for value, field_name in ((self.old_value, "old_value"), (self.new_value, "new_value")):
            if value is not None and not isinstance(value, str):
                raise BranchValidationError(f"PropertyChange.{field_name} 必须是 str 或 None")

    def __repr__(self) -> str:
        """返回不包含属性值的脱敏表示。"""
        return (
            "PropertyChange("
            f"path={self.path!r}, property_name={self.property_name!r}, "
            "old_value=<redacted>, new_value=<redacted>)"
        )


class BranchStatus(str, Enum):
    """分支受控操作的有限状态集合。"""

    PLANNED = "PLANNED"
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    CONFLICTED = "CONFLICTED"
    UNKNOWN = "UNKNOWN"


_ALLOWED_TRANSITIONS: dict[BranchStatus, frozenset[BranchStatus]] = {
    BranchStatus.PLANNED: frozenset(
        {BranchStatus.APPLIED, BranchStatus.FAILED, BranchStatus.CONFLICTED, BranchStatus.UNKNOWN}
    ),
    BranchStatus.APPLIED: frozenset(),
    BranchStatus.FAILED: frozenset(),
    BranchStatus.CONFLICTED: frozenset(),
    BranchStatus.UNKNOWN: frozenset(),
}


@dataclass(frozen=True, slots=True, repr=False)
class BranchResult:
    """记录一次分支操作的不可变结果和审计输入。

    参数：
        mutation_id: 非空幂等 mutation 标识。
        status: ``BranchStatus`` 或对应的大写字符串。
        copies: 已规划复制操作序列，边界规范为 tuple。
        property_changes: 已规划属性变化序列，边界规范为 tuple。
        message: 可选诊断消息；repr 永远不显示它，以免把秘密写入日志。

    返回：
        无；状态转移通过 ``transition`` 返回新对象。

    异常：
        mutation ID、状态、序列元素或状态转移无效时抛异常。

    约束与副作用：
        对象冻结，不执行 mutation；相同输入产生可比较的结果对象。
    """

    mutation_id: str
    status: BranchStatus
    copies: tuple[BranchCopy, ...] = field(default_factory=tuple)
    property_changes: tuple[PropertyChange, ...] = field(default_factory=tuple)
    message: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        """校验结果字段并把序列输入规范为不可变 tuple。"""
        _validate_text(self.mutation_id, "BranchResult.mutation_id")
        status: object = self.status
        if isinstance(status, str):
            try:
                status = BranchStatus(status.upper())
            except ValueError as exc:
                raise BranchValidationError("BranchResult.status 不是支持的状态") from exc
            object.__setattr__(self, "status", status)
        if not isinstance(status, BranchStatus):
            raise BranchValidationError("BranchResult.status 必须是 BranchStatus")
        if not isinstance(self.message, str):
            raise BranchValidationError("BranchResult.message 必须是 str")
        copies = tuple(cast(Iterable[object], self.copies))
        if not all(isinstance(item, BranchCopy) for item in copies):
            raise BranchValidationError("BranchResult.copies 必须只包含 BranchCopy")
        changes = tuple(cast(Iterable[object], self.property_changes))
        if not all(isinstance(item, PropertyChange) for item in changes):
            raise BranchValidationError("BranchResult.property_changes 必须只包含 PropertyChange")
        object.__setattr__(self, "copies", cast(tuple[BranchCopy, ...], copies))
        object.__setattr__(self, "property_changes", cast(tuple[PropertyChange, ...], changes))

    def transition(self, status: BranchStatus, message: str = "") -> BranchResult:
        """按有限状态机返回一个新的结果对象。

        参数：
            status: 目标终态。
            message: 新状态的可选诊断消息。

        返回：
            保留 mutation、复制和属性计划的新 ``BranchResult``。

        异常：
            非法状态或不允许的状态转移抛 ``BranchValidationError``。

        约束与副作用：
            不修改当前对象、不执行 SVN；调用方必须显式保存返回值。
        """
        if not isinstance(status, BranchStatus):
            raise BranchValidationError("目标状态必须是 BranchStatus")
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise BranchValidationError(f"状态 {self.status.value} 不允许转移到 {status.value}")
        return BranchResult(
            mutation_id=self.mutation_id,
            status=status,
            copies=self.copies,
            property_changes=self.property_changes,
            message=message,
        )
