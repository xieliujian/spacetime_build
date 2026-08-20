"""日志文本、命令参数和环境变量的纯函数凭据脱敏。

本模块集中清除常见凭据赋值、认证载荷、Cookie、URL userinfo、PEM 私钥和调用方
显式提供的秘密原文。所有公开函数都返回新元组或新字符串，不修改输入，不记录
日志，不访问环境变量，也不执行任何其他 I/O。
"""

from __future__ import annotations

import re
from typing import cast

_REDACTED = "<redacted>"
_REDACTED_LONG_LINE = "<redacted-long-line>"
_REDACTED_PRIVATE_KEY = "<redacted-private-key>"
_PRIVATE_KEY_BEGIN_SENTINEL = "-----BEGIN "
MIN_STREAMING_PENDING_CHARS = len(_PRIVATE_KEY_BEGIN_SENTINEL)
_PRIVATE_KEY_LABEL_PATTERN = r"[A-Z0-9 ]*PRIVATE KEY"
_SENSITIVE_KEY_FRAGMENT = (
    r"(?:password|passwd|token|access_token|secret|api_key|authorization|cookie|private_key)"
)
_SENSITIVE_KEY_PATTERN = re.compile(_SENSITIVE_KEY_FRAGMENT, flags=re.IGNORECASE)
_CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    rf"(?P<name>[A-Za-z0-9_.-]*{_SENSITIVE_KEY_FRAGMENT}[A-Za-z0-9_.-]*)"
    r"(?![A-Za-z0-9_.-])"
    r"(?P<separator>[ \t]*[=:][ \t]*)"
    r"[^\r\n]*",
    flags=re.IGNORECASE,
)
_AUTHORIZATION_VALUE_PATTERN = re.compile(
    r"(?P<scheme>\b(?:Bearer|Basic)[ \t]+)[^\s,;]+",
    flags=re.IGNORECASE,
)
_URL_USERINFO_PATTERN = re.compile(
    r"(?<![A-Za-z0-9+.-])"
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)"
    r"[^/@\s?#]+@",
    flags=re.IGNORECASE,
)
_PRIVATE_KEY_PATTERN = re.compile(
    rf"-----BEGIN (?P<label>{_PRIVATE_KEY_LABEL_PATTERN})-----"
    r".*?"
    r"-----END (?P=label)-----",
    flags=re.IGNORECASE | re.DOTALL,
)
_PRIVATE_KEY_BEGIN_PATTERN = re.compile(
    rf"-----BEGIN (?P<label>{_PRIVATE_KEY_LABEL_PATTERN})-----",
    flags=re.IGNORECASE,
)
_PRIVATE_KEY_BEGIN_CANDIDATE_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY-{1,4})?\Z",
    flags=re.IGNORECASE,
)
_PRIVATE_KEY_BEGIN_SENTINEL_PATTERN = re.compile(
    re.escape(_PRIVATE_KEY_BEGIN_SENTINEL),
    flags=re.IGNORECASE,
)


def _split_first_complete_line(text: str) -> tuple[str, str, str] | None:
    """从文本开头拆出第一条具有完整换行符的逻辑行。

    参数：
        text: 待扫描的 pending 文本。

    返回：
        找到完整行时返回 ``(内容, 换行符, 剩余文本)``；换行符可以是 ``\n``、
        ``\r`` 或 ``\r\n``。没有完整换行时返回 ``None``，末尾单独 ``\r`` 会
        保留等待下一 chunk，以免拆坏 CRLF。

    异常、约束与副作用：
        调用方保证输入是字符串。函数只做线性内存扫描，不执行 I/O 或原位修改。
    """
    for index, character in enumerate(text):
        if character == "\n":
            return text[:index], "\n", text[index + 1 :]
        if character != "\r":
            continue
        if index + 1 == len(text):
            return None
        if text[index + 1] == "\n":
            return text[:index], "\r\n", text[index + 2 :]
        return text[:index], "\r", text[index + 1 :]
    return None


def _logical_line_start(text: str, index: int) -> int:
    """返回指定字符位置所在逻辑行在文本中的起始索引。

    参数：
        text: 可能包含多条逻辑行的 pending 文本。
        index: 已知位于 ``text`` 边界内的目标字符索引。

    返回：
        ``index`` 之前最后一个 CR 或 LF 的后一位；不存在换行时返回零。

    异常、约束与副作用：
        调用方保证参数类型与边界合法。本函数只用于计算单行长度，不分配外部
        资源、不修改输入且无 I/O。
    """
    return max(text.rfind("\n", 0, index), text.rfind("\r", 0, index)) + 1


def _compile_secret_pattern(secret_values: tuple[str, ...]) -> re.Pattern[str] | None:
    """校验显式秘密并编译单次、最长优先的字面匹配模式。

    参数：
        secret_values: 调用方已解析出的秘密原文元组；空字符串会被忽略。

    返回：
        没有非空秘密时返回 ``None``；否则返回按长度降序排列且逐项
        ``re.escape`` 的联合模式。

    异常、约束与副作用：
        输入不是字符串元组或成员不是字符串时抛出 ``ValueError``。模式只匹配
        原始文本，不让一次替换的占位符被后续短秘密再次处理；函数无 I/O。
    """
    secret_values_object = cast(object, secret_values)
    if not isinstance(secret_values_object, tuple):
        raise ValueError("secret_values 必须是 str 元组")
    values = cast(tuple[object, ...], secret_values_object)
    normalized: set[str] = set()
    for value_object in values:
        if not isinstance(value_object, str):
            raise ValueError("secret_values 的元素必须是 str")
        if value_object:
            normalized.add(value_object)
    if not normalized:
        return None
    ordered = sorted(normalized, key=lambda value: (-len(value), value.encode("utf-8")))
    return re.compile("|".join(re.escape(value) for value in ordered))


def _redact_validated_text(text: str, secret_pattern: re.Pattern[str] | None) -> str:
    """使用已校验的秘密模式和固定规则脱敏单段文本。

    参数：
        text: 已确认类型为 ``str`` 的原始文本。
        secret_pattern: 已编译显式秘密模式，或 ``None``。

    返回：
        新的脱敏字符串；凭据周边非秘密结构尽量保持不变。

    异常、约束与副作用：
        本内部函数要求调用方先完成类型校验。固定正则只使用可靠的 scheme 左边界
        和受限字符类，避免 URL 扫描发生二次复杂度；函数无 I/O 和原位修改。
    """
    redacted = text
    if secret_pattern is not None:
        # lambda replacement 避免原秘密中的反斜杠或占位文本参与替换串解析。
        redacted = secret_pattern.sub(lambda _match: _REDACTED, redacted)
    redacted = _PRIVATE_KEY_PATTERN.sub(_REDACTED_PRIVATE_KEY, redacted)
    redacted = _URL_USERINFO_PATTERN.sub(
        lambda match: f"{match.group('scheme')}{_REDACTED}@",
        redacted,
    )
    redacted = _CREDENTIAL_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}{_REDACTED}",
        redacted,
    )
    return _AUTHORIZATION_VALUE_PATTERN.sub(
        lambda match: f"{match.group('scheme')}{_REDACTED}",
        redacted,
    )


def redact_text(text: str, *, secret_values: tuple[str, ...] = ()) -> str:
    """脱敏日志或异常中的全部已知凭据形式。

    参数：
        text: 待处理文本，可以包含多行内容。
        secret_values: 额外秘密原文元组；空值忽略，非空值按长度降序字面匹配。

    返回：
        替换凭据赋值、Bearer/Basic 载荷、Cookie 整值、URL userinfo、PEM 私钥块
        和显式秘密后的新字符串。

    异常、约束与副作用：
        ``text`` 或 ``secret_values`` 类型非法时抛出 ``ValueError``。秘密中的正则
        元字符按字面处理；函数不修改输入、不记录日志且无 I/O。
    """
    text_object = cast(object, text)
    if not isinstance(text_object, str):
        raise ValueError("text 必须是 str")
    secret_pattern = _compile_secret_pattern(secret_values)
    return _redact_validated_text(text_object, secret_pattern)


def redact_arguments(
    arguments: tuple[str, ...],
    *,
    redacted_indexes: frozenset[int] = frozenset(),
    secret_values: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """返回脱敏后的不可变命令参数副本。

    参数：
        arguments: 原始命令参数字符串元组。
        redacted_indexes: 必须整项隐藏的参数索引集合。
        secret_values: 需要在未整项隐藏参数中按字面替换的秘密原文元组。

    返回：
        与输入等长的新元组；显式索引固定为 ``<redacted>``，其他项执行文本脱敏。

    异常、约束与副作用：
        参数容器或成员类型非法、索引不是整数、索引为负数或越界时抛出
        ``ValueError``。布尔值不作为整数索引接受；函数不修改输入且无 I/O。
    """
    arguments_object = cast(object, arguments)
    if not isinstance(arguments_object, tuple):
        raise ValueError("arguments 必须是 str 元组")
    argument_values = cast(tuple[object, ...], arguments_object)
    for argument_object in argument_values:
        if not isinstance(argument_object, str):
            raise ValueError("arguments 的元素必须是 str")

    indexes_object = cast(object, redacted_indexes)
    if not isinstance(indexes_object, frozenset):
        raise ValueError("redacted_indexes 必须是 frozenset[int]")
    index_values = cast(frozenset[object], indexes_object)
    validated_indexes: set[int] = set()
    for index_object in index_values:
        if not isinstance(index_object, int) or isinstance(index_object, bool):
            raise ValueError("redacted_indexes 的元素必须是 int")
        if index_object < 0 or index_object >= len(argument_values):
            raise ValueError(f"redacted_indexes 包含越界索引: {index_object}")
        validated_indexes.add(index_object)

    secret_pattern = _compile_secret_pattern(secret_values)
    return tuple(
        _REDACTED
        if index in validated_indexes
        else _redact_validated_text(cast(str, argument), secret_pattern)
        for index, argument in enumerate(argument_values)
    )


def redact_environment(
    environment: tuple[tuple[str, str], ...],
    *,
    secret_values: tuple[str, ...] = (),
) -> tuple[tuple[str, str], ...]:
    """校验、排序并脱敏不可变环境变量键值元组。

    参数：
        environment: 环境变量键值二元元组的元组。
        secret_values: 需要在非敏感键对应值中按字面替换的秘密原文元组。

    返回：
        按键 UTF-8 字节序稳定排序的新元组。键名包含任一敏感词时值整体固定为
        ``<redacted>``；其他值执行完整文本脱敏。

    异常、约束与副作用：
        容器结构非法、键为空、成员不是字符串或存在完全相同的重复键时抛出
        ``ValueError``。函数不读取真实进程环境、不修改输入且无 I/O。
    """
    environment_object = cast(object, environment)
    if not isinstance(environment_object, tuple):
        raise ValueError("environment 必须是字符串键值元组")
    entry_values = cast(tuple[object, ...], environment_object)
    secret_pattern = _compile_secret_pattern(secret_values)
    redacted_environment: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for entry_object in entry_values:
        if not isinstance(entry_object, tuple):
            raise ValueError("environment 的元素必须是二元元组")
        entry = cast(tuple[object, ...], entry_object)
        if len(entry) != 2:
            raise ValueError("environment 的元素必须是二元元组")
        key_object, value_object = entry
        if not isinstance(key_object, str) or not key_object:
            raise ValueError("environment 键必须是非空 str")
        if not isinstance(value_object, str):
            raise ValueError("environment 值必须是 str")
        if key_object in seen_keys:
            raise ValueError(f"environment 存在重复键: {key_object!r}")
        seen_keys.add(key_object)
        if _SENSITIVE_KEY_PATTERN.search(key_object) is not None:
            redacted_value = _REDACTED
        else:
            redacted_value = _redact_validated_text(value_object, secret_pattern)
        redacted_environment.append((key_object, redacted_value))

    redacted_environment.sort(key=lambda item: item[0].encode("utf-8"))
    return tuple(redacted_environment)


class StreamingRedactor:
    """按完整逻辑行脱敏跨 chunk 文本的有界状态机。

    职责：
        暂存尚未结束的普通逻辑行，完整后统一调用 ``redact_text``，使凭据键和值
        与 URL userinfo 即使跨 chunk 仍不会泄漏。检测到 PEM BEGIN 时立即输出一
        次私钥 marker，并按 BEGIN label 只搜索对应 END；状态仅保留该 END marker
        的有界 rolling suffix。BEGIN 候选在完整识别前超过单行上限时永久进入
        fail-closed 私钥丢弃态，直到 finalize 都不会被任意 END 恢复。块内容永不
        累计或输出；CRLF 会作为整体保留，普通超长行只输出一次固定 marker。

    参数：
        max_pending_chars: 单条未结束逻辑行允许暂存的最大字符数，默认
        ``1_048_576``；必须是非布尔整数且不小于
        ``MIN_STREAMING_PENDING_CHARS``。该最小值来自安全识别 PEM BEGIN
        sentinel 的必要长度，不是可任意调低的性能参数。

    返回：
        无；构造成功后由 ``feed`` 和 ``finalize`` 返回可安全写出的文本片段。

    异常、约束与副作用：
        上限非法时抛出 ``ValueError``。实例设计为由单一线程在外部同步后使用，
        自身不加锁；只处理内存文本，不执行 I/O、日志或环境访问。
    """

    __slots__ = (
        "_discarded_line_scan",
        "_discarding_long_line",
        "_finalized",
        "_in_private_key",
        "_pending",
        "_private_end_marker_length",
        "_private_end_pattern",
        "_private_end_scan",
        "_secret_values",
        "max_pending_chars",
    )

    def __init__(
        self,
        max_pending_chars: int = 1_048_576,
        secret_values: tuple[str, ...] = (),
    ) -> None:
        """校验上限并初始化空 pending 与生命周期状态。

        参数：
            max_pending_chars: 单条未结束逻辑行的最大字符数。

        返回：
            无。

        异常、约束与副作用：
            非整数、布尔值或小于 PEM BEGIN sentinel 必要长度时抛出
            ``ValueError``。构造只分配内存状态。
        """
        max_pending_object = cast(object, max_pending_chars)
        if (
            not isinstance(max_pending_object, int)
            or isinstance(max_pending_object, bool)
            or max_pending_object < MIN_STREAMING_PENDING_CHARS
        ):
            raise ValueError(
                "max_pending_chars 必须是至少容纳 PEM BEGIN sentinel 的整数: "
                f"{MIN_STREAMING_PENDING_CHARS}"
            )
        if not isinstance(secret_values, tuple) or any(
            not isinstance(value, str) or not value for value in secret_values
        ):
            raise ValueError("secret_values 必须是非空字符串元组")
        self.max_pending_chars = max_pending_object
        self._secret_values = secret_values
        self._pending = ""
        self._private_end_scan = ""
        self._private_end_pattern: re.Pattern[str] | None = None
        self._private_end_marker_length = 0
        self._discarded_line_scan = ""
        self._discarding_long_line = False
        self._in_private_key = False
        self._finalized = False

    @property
    def pending_size(self) -> int:
        """返回当前为跨 chunk 识别而保留的字符总数。

        没有参数；普通状态最多保留配置的单行上限及一个待判定 CR，PEM 状态只
        保留不超过当前对应 END marker 长度的 rolling suffix；fail-closed PEM
        状态不保留输入。属性不暴露原文、不修改状态且不执行 I/O，可用于监控
        内存上界。
        """
        return len(self._pending) + len(self._private_end_scan) + len(self._discarded_line_scan)

    def feed(self, text: str) -> str:
        """接收一个文本 chunk 并返回本次可安全输出的完整逻辑行。

        参数：
            text: 已解码文本片段，可以为空，也可以在凭据、PEM 或 CRLF 中间结束。

        返回：
            本次新完成逻辑行的脱敏文本；没有完整安全输出时返回空字符串。超长
            行首次越界时返回 ``<redacted-long-line>``，之后丢弃到换行。

        异常、约束与副作用：
            非字符串抛出 ``ValueError``；``finalize`` 后调用抛出 ``RuntimeError``。
            方法只更新实例 pending，不执行 I/O；调用方负责线程同步。
        """
        if self._finalized:
            raise RuntimeError("StreamingRedactor 已 finalize")
        text_object = cast(object, text)
        if not isinstance(text_object, str):
            raise ValueError("text 必须是 str")
        self._pending += text_object
        output: list[str] = []

        while True:
            if self._in_private_key:
                suffix = self._consume_private_key_text(self._pending)
                self._pending = ""
                if suffix is None:
                    return "".join(output)
                self._in_private_key = False
                self._pending = suffix
                continue

            completed = _split_first_complete_line(self._pending)
            if self._discarding_long_line:
                if completed is None:
                    scan_text = (
                        self._pending[:-1] if self._pending.endswith("\r") else self._pending
                    )
                    found_begin = self._scan_discarded_line_for_begin(scan_text)
                    self._pending = "\r" if self._pending.endswith("\r") else ""
                    if found_begin:
                        output.append(_REDACTED_PRIVATE_KEY)
                        self._pending = ""
                        self._discarding_long_line = False
                        self._enter_fail_closed_private_key()
                    return "".join(output)
                discarded, line_ending, self._pending = completed
                if self._scan_discarded_line_for_begin(discarded):
                    output.append(_REDACTED_PRIVATE_KEY)
                    self._discarding_long_line = False
                    self._enter_fail_closed_private_key()
                    continue
                output.append(line_ending)
                self._discarded_line_scan = ""
                self._discarding_long_line = False
                continue

            begin_match = _PRIVATE_KEY_BEGIN_PATTERN.search(self._pending)
            if begin_match is not None:
                line_start = _logical_line_start(self._pending, begin_match.start())
                sentinel_end = begin_match.start() - line_start + MIN_STREAMING_PENDING_CHARS
                if sentinel_end > self.max_pending_chars:
                    prefix = self._pending[:line_start]
                    self._pending = self._pending[begin_match.end() :]
                    if prefix:
                        output.append(self._redact(prefix))
                    output.append(_REDACTED_LONG_LINE)
                    output.append(_REDACTED_PRIVATE_KEY)
                    self._enter_fail_closed_private_key()
                    continue
                prefix = self._pending[: begin_match.start()]
                self._pending = self._pending[begin_match.end() :]
                if prefix:
                    output.append(self._redact(prefix))
                output.append(_REDACTED_PRIVATE_KEY)
                if begin_match.end() - line_start <= self.max_pending_chars:
                    expected_end_marker = f"-----END {begin_match.group('label')}-----"
                    self._private_end_pattern = re.compile(
                        re.escape(expected_end_marker),
                        flags=re.IGNORECASE,
                    )
                    self._private_end_marker_length = len(expected_end_marker)
                else:
                    # 单个 chunk 直接带来完整超限 BEGIN 时也保持 fail-closed，避免
                    # 安全语义随 chunk 切分方式改变。
                    self._private_end_pattern = None
                    self._private_end_marker_length = 0
                self._in_private_key = True
                continue

            if completed is None:
                candidate_text = (
                    self._pending[:-1] if self._pending.endswith("\r") else self._pending
                )
                candidate_match = _PRIVATE_KEY_BEGIN_CANDIDATE_PATTERN.search(candidate_text)
                pending_length = len(self._pending)
                if self._pending.endswith("\r"):
                    pending_length -= 1
                if pending_length > self.max_pending_chars:
                    if (
                        candidate_match is not None
                        and candidate_match.start() + MIN_STREAMING_PENDING_CHARS
                        <= self.max_pending_chars
                    ):
                        prefix = candidate_text[: candidate_match.start()]
                        if prefix:
                            output.append(self._redact(prefix))
                        output.append(_REDACTED_PRIVATE_KEY)
                        self._pending = ""
                        self._enter_fail_closed_private_key()
                        return "".join(output)
                    # marker 在进入丢弃态时只输出一次，原始长行从不返回调用方。
                    output.append(_REDACTED_LONG_LINE)
                    self._pending = "\r" if self._pending.endswith("\r") else ""
                    self._discarding_long_line = True
                    if self._scan_discarded_line_for_begin(candidate_text):
                        output.append(_REDACTED_PRIVATE_KEY)
                        self._pending = ""
                        self._discarding_long_line = False
                        self._enter_fail_closed_private_key()
                return "".join(output)

            line, line_ending, self._pending = completed
            if len(line) > self.max_pending_chars:
                candidate_match = _PRIVATE_KEY_BEGIN_CANDIDATE_PATTERN.search(line)
                if (
                    candidate_match is not None
                    and candidate_match.start() + MIN_STREAMING_PENDING_CHARS
                    <= self.max_pending_chars
                ):
                    prefix = line[: candidate_match.start()]
                    if prefix:
                        output.append(self._redact(prefix))
                    output.append(_REDACTED_PRIVATE_KEY)
                    self._enter_fail_closed_private_key()
                    continue
                if _PRIVATE_KEY_BEGIN_SENTINEL_PATTERN.search(line) is not None:
                    output.append(_REDACTED_LONG_LINE)
                    output.append(_REDACTED_PRIVATE_KEY)
                    self._enter_fail_closed_private_key()
                    continue
                output.append(f"{_REDACTED_LONG_LINE}{line_ending}")
                continue

            segment = f"{line}{line_ending}"
            output.append(self._redact(segment))

    def finalize(self) -> str:
        """结束输入并返回最后一段已脱敏 pending 文本。

        没有参数。首次调用把无换行普通尾部视为完整逻辑行；丢弃态不返回被丢弃
        原文，但保留已收到的末尾独立 ``\r``。PEM 状态无论是否闭合都直接清空
        有界扫描 suffix 并返回空，私钥 marker 已在 BEGIN 检测时输出。重复调用
        幂等返回空字符串。

        异常、约束与副作用：
            正常调用不抛出业务异常。方法将实例永久标记为结束，只更新内存状态，
            不执行 I/O；结束后 ``feed`` 固定抛出 ``RuntimeError``。
        """
        if self._finalized:
            return ""
        self._finalized = True

        if self._discarding_long_line:
            trailing_ending = "\r" if self._pending == "\r" else ""
            self._pending = ""
            self._discarded_line_scan = ""
            return trailing_ending

        if self._in_private_key:
            self._pending = ""
            self._private_end_scan = ""
            self._private_end_pattern = None
            self._private_end_marker_length = 0
            self._in_private_key = False
            return ""

        tail = self._pending
        self._pending = ""
        if tail.endswith("\r"):
            return f"{self._redact(tail[:-1])}\r"
        return self._redact(tail)

    def _redact(self, text: str) -> str:
        """使用通用规则和本次租约秘密值脱敏一段完整文本。"""
        return redact_text(text, secret_values=self._secret_values)

    def _enter_fail_closed_private_key(self) -> None:
        """进入没有可接受 END marker 的永久私钥丢弃态。

        没有参数和返回值。调用方已确认 BEGIN sentinel 出现在无法安全保留完整
        label 的超限逻辑行中；方法清空所有 rolling scanner，并以 ``None`` 表示
        任意 END 都不能恢复普通输出。只修改内存状态，不执行 I/O。
        """
        self._discarded_line_scan = ""
        self._private_end_scan = ""
        self._private_end_pattern = None
        self._private_end_marker_length = 0
        self._in_private_key = True

    def _scan_discarded_line_for_begin(self, text: str) -> bool:
        """在普通长行丢弃态有界扫描可能跨 chunk 的 PEM BEGIN sentinel。

        参数：
            text: 当前被丢弃逻辑行中新到达且不含行终止符的文本。

        返回：
            当前或历史 suffix 与当前文本拼接后发现 sentinel 时返回 ``True``；
            否则返回 ``False`` 并只保留 sentinel 长度减一的 rolling suffix。

        异常、约束与副作用：
            调用方保证 ``text`` 为字符串且尚未越过逻辑行终止符。匹配忽略大小写，
            内存占用与累计丢弃长度无关；方法不输出原文、不执行 I/O。
        """
        scanned = f"{self._discarded_line_scan}{text}"
        if _PRIVATE_KEY_BEGIN_SENTINEL_PATTERN.search(scanned) is not None:
            self._discarded_line_scan = ""
            return True
        rolling_size = MIN_STREAMING_PENDING_CHARS - 1
        self._discarded_line_scan = scanned[-rolling_size:]
        return False

    def _consume_private_key_text(self, text: str) -> str | None:
        """丢弃 PEM 内容并以有界 rolling suffix 搜索对应标签的 END marker。

        参数：
            text: 本次进入 PEM 状态的新文本，可以含任意数量逻辑行或超长行。

        返回：
            未找到 END 时返回 ``None``；找到时返回 marker 之后的 suffix，包含 END
            所在行原始终止符及其后的文本，交回普通状态处理。

        异常、约束与副作用：
            调用方保证输入为字符串。有对应 END 时，未命中只保留至多 marker
            长度减一的尾部；fail-closed 状态不保留任何输入且永不退出。错误标签
            END、正文和换行全部丢弃；方法不执行 I/O 或输出私钥材料。
        """
        end_pattern = self._private_end_pattern
        if end_pattern is None:
            self._private_end_scan = ""
            return None
        if self._private_end_marker_length <= 0:
            raise RuntimeError("PEM 状态的 END marker 长度非法")

        scanned = f"{self._private_end_scan}{text}"
        end_match = end_pattern.search(scanned)
        if end_match is not None:
            suffix = scanned[end_match.end() :]
            self._private_end_scan = ""
            self._private_end_pattern = None
            self._private_end_marker_length = 0
            return suffix

        rolling_size = self._private_end_marker_length - 1
        self._private_end_scan = scanned[-rolling_size:]
        return None
