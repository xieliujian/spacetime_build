"""application 层共享的请求、状态和结果模型。

本模块固化跨资源构建、发布和客户端打包用例共同使用的身份边界。请求只保存
固定 revision 和平台等公开输入，运行记录只描述可恢复状态；manifest、bundle
和 package ID 互不替代。所有对象都是不可变值对象，导入模块不产生外部副作用。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum

from core.errors import ConfigurationError
from core.platforms import BuildPlatform


class RunState(Enum):
    """统一 application 运行生命周期状态。"""

    CREATED = "created"
    PREFLIGHTED = "preflighted"
    PLANNED = "planned"
    RUNNING = "running"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    CONFLICTED = "conflicted"


_TERMINAL_STATES = frozenset(
    {
        RunState.SUCCEEDED,
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.CONFLICTED,
    }
)
_RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.PREFLIGHTED, RunState.FAILED, RunState.CANCEL_REQUESTED}),
    RunState.PREFLIGHTED: frozenset({RunState.PLANNED, RunState.FAILED, RunState.CANCEL_REQUESTED}),
    RunState.PLANNED: frozenset({RunState.RUNNING, RunState.FAILED, RunState.CANCEL_REQUESTED}),
    RunState.RUNNING: frozenset({RunState.VERIFYING, RunState.FAILED, RunState.CANCEL_REQUESTED}),
    RunState.VERIFYING: frozenset(
        {RunState.SUCCEEDED, RunState.FAILED, RunState.CONFLICTED, RunState.CANCEL_REQUESTED}
    ),
    RunState.CANCEL_REQUESTED: frozenset({RunState.CANCELLED, RunState.FAILED}),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
    RunState.CONFLICTED: frozenset(),
}


def _validate_public_text(value: object, field_name: str) -> str:
    """校验身份字段为非空且无控制字符的公开文本。"""
    if not isinstance(value, str):
        raise ConfigurationError(f"{field_name} 必须是 str")
    if not value or not value.strip():
        raise ConfigurationError(f"{field_name} 不得为空")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ConfigurationError(f"{field_name} 不得包含控制字符")
    return value


def _validate_fixed_revision(value: object) -> str:
    """校验 revision 已固定且不使用常见浮动别名。"""
    revision = _validate_public_text(value, "source_revision")
    if revision.casefold() in {"head", "latest", "tip"}:
        raise ConfigurationError("source_revision 必须固定，不能使用浮动 revision")
    return revision


@dataclass(frozen=True, slots=True)
class ApplicationRequest:
    """描述一次 application 用例执行的不可变公共请求。

    参数：
        run_id: 当前运行的外部身份；不能与 manifest、bundle 或 package ID 混用。
        profile: 已解析的配置 Profile 名称。
        source_revision: 已固定的源码或输入快照 revision。
        platform: 来自 ``core.platforms`` 的共享目标平台。
        dry_run: 是否只生成计划而禁止写端口和工具执行。

    返回：
        无；构造后通过字段读取请求。

    异常：
        字段类型、空值或浮动 revision 非法时抛出 ``ConfigurationError``。

    约束与副作用：
        ``frozen=True``；不保存适配器、不读取环境变量、不产生外部副作用。
    """

    run_id: str
    profile: str
    source_revision: str
    platform: BuildPlatform
    dry_run: bool

    def __post_init__(self) -> None:
        """在进入任何用例前校验身份和 dry-run 类型。"""
        _validate_public_text(self.run_id, "run_id")
        _validate_public_text(self.profile, "profile")
        _validate_fixed_revision(self.source_revision)
        if not isinstance(self.platform, BuildPlatform):
            raise ConfigurationError("platform 必须是 BuildPlatform")
        if not isinstance(self.dry_run, bool):
            raise ConfigurationError("dry_run 必须是 bool")


@dataclass(frozen=True, slots=True)
class RunResult:
    """承载一次用例的终态结果和可追踪对象身份。

    参数：
        run_id: 与原始 ``ApplicationRequest`` 一致的运行身份。
        state: 必须是成功、失败、取消或冲突终态。
        record_locator: 执行记录的受约束相对定位。
        artifact_ids: 按用例产生顺序保存的不可变产物身份元组。

    返回：
        无；构造后通过字段读取结果。

    异常：
        非终态、空定位、绝对路径、反斜杠或非法产物 ID 时抛出
        ``ConfigurationError``。

    约束与副作用：
        不读取记录存储，不写入任何对象；结果本身不可变。
    """

    run_id: str
    state: RunState
    record_locator: str
    artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        """校验结果是可持久化的终态摘要。"""
        _validate_public_text(self.run_id, "run_id")
        if not isinstance(self.state, RunState):
            raise ConfigurationError("state 必须是 RunState")
        if self.state not in _TERMINAL_STATES:
            raise ConfigurationError("RunResult.state 必须是终态")
        locator = _validate_public_text(self.record_locator, "record_locator")
        if locator.startswith(("/", "\\")) or "\\" in locator:
            raise ConfigurationError("record_locator 必须是正斜杠相对定位")
        if any(part in {"", ".", ".."} for part in locator.split("/")):
            raise ConfigurationError("record_locator 含非法路径段")
        if not isinstance(self.artifact_ids, tuple):
            raise ConfigurationError("artifact_ids 必须是 tuple[str, ...]")
        for artifact_id in self.artifact_ids:
            _validate_public_text(artifact_id, "artifact_id")


def can_transition(current: RunState, target: RunState) -> bool:
    """返回统一状态机是否允许一次单向转移。

    参数：
        current: 已持久化的当前状态。
        target: 希望写入的新状态。

    返回：
        允许时为 ``True``；终态或未声明边为 ``False``。

    异常：
        参数不是 ``RunState`` 时抛出 ``ConfigurationError``。

    约束与副作用：
        纯函数，不改变记录，也不执行 CAS。
    """
    if not isinstance(current, RunState) or not isinstance(target, RunState):
        raise ConfigurationError("current 和 target 必须是 RunState")
    return target in _RUN_TRANSITIONS[current]


def transition_run_state(current: RunState, target: RunState) -> RunState:
    """校验并返回一次合法的统一状态转移结果。

    参数：
        current: 当前状态。
        target: 目标状态。

    返回：
        校验通过的 ``target``，供调用方创建下一条不可变记录。

    异常：
        参数类型错误或转移未在状态机中声明时抛出 ``ConfigurationError``。

    约束与副作用：
        不原地修改任何状态；终态不可逆，调用方必须显式持久化返回值。
    """
    if not can_transition(current, target):
        raise ConfigurationError(f"不允许状态转移: {current.value} -> {target.value}")
    return target
