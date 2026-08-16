"""验证文本、命令参数与环境变量的完整凭据脱敏契约。

本模块覆盖大小写不敏感凭据键、认证头、Cookie、URL userinfo、PEM 私钥、显式
秘密、Unicode 稳定排序和长文本复杂度。测试全部为内存纯函数调用。
"""

from __future__ import annotations

from time import perf_counter

import pytest

from observability import (
    MIN_STREAMING_PENDING_CHARS,
    StreamingRedactor,
    redact_arguments,
    redact_environment,
    redact_text,
)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password=hunter2", "hunter2"),
        ("DB_PASSWD: pass-value", "pass-value"),
        ("prefixTokenSuffix=token-value", "token-value"),
        ("ACCESS_TOKEN: access-value", "access-value"),
        ("client_secret=secret-value", "secret-value"),
        ("service.API_KEY: key-value", "key-value"),
        ("authorization=Digest opaque", "opaque"),
        ("session_cookie: a=1; b=2", "a=1; b=2"),
        ("private_key=private-value", "private-value"),
    ],
)
def test_redact_text_hides_case_insensitive_compound_credential_assignments(
    text: str,
    secret: str,
) -> None:
    """验证敏感词带前后缀并使用等号或冒号时隐藏单行值。

    ``text`` 是凭据赋值，``secret`` 是不得残留的原值。返回文本必须保留键并用
    统一占位符替换秘密；函数不修改输入。
    """
    original = text

    redacted = redact_text(text)

    assert secret not in redacted
    assert redacted.endswith("<redacted>")
    assert text == original


@pytest.mark.parametrize(
    "text",
    [
        "Bearer bearer-token",
        "authorization header uses bAsIc Zm9vOmJhcg==",
        "Authorization: Bearer header-token",
    ],
)
def test_redact_text_hides_bearer_and_basic_authorization_payloads(text: str) -> None:
    """验证独立或 Header 内的 Bearer/Basic 认证载荷不会泄漏。

    ``text`` 是认证文本。返回值必须含占位符且不保留代表性载荷；测试无副作用。
    """
    redacted = redact_text(text)

    assert "<redacted>" in redacted
    assert "bearer-token" not in redacted
    assert "Zm9vOmJhcg==" not in redacted
    assert "header-token" not in redacted


def test_redact_text_hides_user_only_and_user_password_url_userinfo() -> None:
    """验证 URL 的 user-only 与 user:password 两种 userinfo 均整体隐藏。

    无参数和返回值；host、scheme 和其余路径必须保留，用户名与密码不得残留。
    测试只处理内存文本。
    """
    text = "one=https://alice@example.com/a two=ssh+git://bob:p%40ss@example.net/repo"

    redacted = redact_text(text)

    assert redacted == (
        "one=https://<redacted>@example.com/a two=ssh+git://<redacted>@example.net/repo"
    )


@pytest.mark.parametrize("label", ["RSA PRIVATE KEY", "OPENSSH PRIVATE KEY"])
def test_redact_text_replaces_complete_pem_private_key_blocks(label: str) -> None:
    """验证不同 PEM 私钥标签的首尾标记和材料整体被替换。

    ``label`` 是合法私钥块标签。返回值只保留块外文本，私钥材料和标签均不得
    残留；函数无 I/O。
    """
    text = f"before\n-----BEGIN {label}-----\nmaterial\n-----END {label}-----\nafter"

    redacted = redact_text(text)

    assert redacted == "before\n<redacted-private-key>\nafter"


def test_redact_text_uses_longest_literal_secret_match_and_ignores_empty_values() -> None:
    """验证显式秘密最长优先、按字面匹配且不会发生替换级联。

    无参数和返回值；秘密包含重叠文本及正则元字符。每个原值恰好替换为统一
    占位符，空秘密不能匹配字符间隙。
    """
    text = "abcd abc a+b?.[x] safe"

    redacted = redact_text(text, secret_values=("abc", "abcd", "a+b?.[x]", ""))

    assert redacted == "<redacted> <redacted> <redacted> safe"


def test_redact_arguments_replaces_explicit_indexes_and_redacts_remaining_text() -> None:
    """验证 argv 显式索引整项隐藏，其他项继续执行文本与秘密脱敏。

    无参数和返回值；输入元组必须保持不变，输出与输入等长。
    """
    arguments = ("tool", "--password=argv-secret", "raw-secret", "--visible")

    redacted = redact_arguments(
        arguments,
        redacted_indexes=frozenset({3}),
        secret_values=("raw-secret",),
    )

    assert redacted == (
        "tool",
        "--password=<redacted>",
        "<redacted>",
        "<redacted>",
    )
    assert arguments == ("tool", "--password=argv-secret", "raw-secret", "--visible")


@pytest.mark.parametrize("indexes", [frozenset({-1}), frozenset({2}), frozenset({True})])
def test_redact_arguments_rejects_negative_out_of_range_or_boolean_indexes(
    indexes: frozenset[int],
) -> None:
    """验证 argv 脱敏索引只接受范围内的真实整数。

    ``indexes`` 含负数、越界值或布尔值。调用必须抛出 ``ValueError``；输入无
    修改且测试无 I/O。
    """
    with pytest.raises(ValueError, match="redacted_indexes"):
        redact_arguments(("one", "two"), redacted_indexes=indexes)


def test_redact_environment_hides_sensitive_keys_and_utf8_sorts_other_values() -> None:
    """验证环境敏感键整值隐藏，普通值脱敏并按键 UTF-8 字节序排序。

    无参数和返回值；混合 ASCII、重音字母和中文键，输出应确定排序且不修改输入。
    """
    environment = (
        ("中", "visible"),
        ("PASSWORD_FILE", "entire-value"),
        ("é", "token=embedded"),
        ("a", "literal-value"),
    )

    redacted = redact_environment(environment, secret_values=("literal-value",))

    assert redacted == tuple(
        sorted(
            (
                ("中", "visible"),
                ("PASSWORD_FILE", "<redacted>"),
                ("é", "token=<redacted>"),
                ("a", "<redacted>"),
            ),
            key=lambda item: item[0].encode("utf-8"),
        )
    )
    assert environment[1][1] == "entire-value"


def test_redact_environment_rejects_duplicate_exact_keys() -> None:
    """验证重复环境键在转换为映射前被确定拒绝。

    无参数和返回值；两个完全相同键应抛出 ``ValueError``，不读取真实环境。
    """
    with pytest.raises(ValueError, match="重复键"):
        redact_environment((("PATH", "one"), ("PATH", "two")))


def test_redact_text_preserves_unrelated_unicode_and_scales_linearly() -> None:
    """验证无凭据 Unicode 文本保持原样且长 scheme 候选不会出现二次复杂度。

    无参数和返回值；构造 100,000 字符长文本并使用宽松三秒上限。测试仅计算
    内存字符串和耗时，不调用外部资源。
    """
    plain_text = "构建完成 " + ("A" * 100_000)

    started_at = perf_counter()
    redacted = redact_text(plain_text)
    elapsed_seconds = perf_counter() - started_at

    assert redacted == plain_text
    assert elapsed_seconds < 3.0, f"长文本脱敏耗时过高: {elapsed_seconds:.6f} 秒"


def test_redaction_public_apis_reject_invalid_container_and_member_types() -> None:
    """验证文本、秘密、argv、索引和环境结构不能靠错误运行时类型绕过。

    无参数和返回值；每个非法公开输入均应抛出 ``ValueError``，函数不得读取真实
    环境或修改传入容器。
    """
    with pytest.raises(ValueError, match="text"):
        redact_text(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="secret_values"):
        redact_text("value", secret_values=["secret"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="secret_values.*元素"):
        redact_text("value", secret_values=(object(),))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="arguments"):
        redact_arguments(["tool"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="arguments.*元素"):
        redact_arguments((object(),))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="redacted_indexes"):
        redact_arguments(("tool",), redacted_indexes={0})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="environment"):
        redact_environment([("A", "B")])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "environment",
    [
        (["A", "B"],),
        (("A",),),
        (("", "value"),),
        (("A", object()),),
    ],
)
def test_redact_environment_rejects_invalid_entries(environment: object) -> None:
    """验证环境元素必须是非空字符串键与字符串值组成的二元元组。

    ``environment`` 覆盖列表元素、错误长度、空键与错误值类型。调用应抛出
    ``ValueError``，测试不读取进程环境。
    """
    with pytest.raises(ValueError, match="environment"):
        redact_environment(environment)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "secrets"),
    [
        ("password=LEAK\n", ("LEAK",)),
        ("Authorization: Bearer TOKEN\n", ("TOKEN",)),
        ("remote=https://user:pass@example.com/path\n", ("user", "pass")),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nPRIVATE_MATERIAL\n-----END RSA PRIVATE KEY-----\n",
            ("PRIVATE_MATERIAL",),
        ),
    ],
)
def test_streaming_redactor_matches_whole_text_at_every_character_split(
    text: str,
    secrets: tuple[str, ...],
) -> None:
    """验证凭据在任意字符边界切分时都与整段脱敏结果完全一致。

    ``text`` 覆盖键值、认证、URL 与多行 PEM，``secrets`` 是不得出现在任何输出
    中的原文。测试遍历全部二段切分点，并额外逐字符 feed 后 finalize。
    """
    expected = redact_text(text)

    for split_index in range(len(text) + 1):
        redactor = StreamingRedactor()
        output = (
            redactor.feed(text[:split_index])
            + redactor.feed(text[split_index:])
            + redactor.finalize()
        )
        assert output == expected, f"切分点 {split_index} 结果不一致"
        assert all(secret not in output for secret in secrets)

    character_redactor = StreamingRedactor()
    character_output = "".join(character_redactor.feed(character) for character in text)
    character_output += character_redactor.finalize()
    assert character_output == expected
    assert all(secret not in character_output for secret in secrets)


def test_streaming_redactor_preserves_crlf_lone_cr_and_multiple_lines() -> None:
    """验证 CRLF 跨 chunk 不拆坏，独立 CR 与后续多行仍保持原始换行。

    无参数和返回值；输入块刻意都结束在 CR。输出拼接必须等于整段
    ``redact_text``，秘密不得泄漏。
    """
    chunks = (
        "visible\r",
        "\npassword=CRLF_SECRET\r",
        "\nnext\r",
        "Authorization: Bearer FINAL_SECRET",
    )
    text = "".join(chunks)
    redactor = StreamingRedactor()

    output = "".join(redactor.feed(chunk) for chunk in chunks)
    output += redactor.finalize()

    assert output == redact_text(text)
    assert "CRLF_SECRET" not in output
    assert "FINAL_SECRET" not in output
    assert "\r\n" in output
    assert "next\r" in output


def test_streaming_redactor_caps_long_line_and_discards_until_newline() -> None:
    """验证超长无换行输入只输出一次固定 marker 并丢弃到下个换行。

    无参数和返回值；超过最小安全上限后继续输入秘密和跨 chunk CRLF。超长原文绝不
    输出，换行保持且之后的正常敏感行继续按完整规则脱敏。
    """
    redactor = StreamingRedactor(max_pending_chars=MIN_STREAMING_PENDING_CHARS)

    assert redactor.feed("123456789012") == "<redacted-long-line>"
    assert redactor.feed("LONG_SECRET") == ""
    assert redactor.feed("\r") == ""
    resumed = redactor.feed("\ntoken=X\n")

    assert resumed == "\r\ntoken=<redacted>\n"
    assert redactor.finalize() == ""
    combined = "<redacted-long-line>" + resumed
    assert "123456789012" not in combined
    assert "LONG_SECRET" not in combined
    assert "token=X" not in combined


def test_streaming_redactor_finalize_is_idempotent_and_closes_feed() -> None:
    """验证 finalize 脱敏无换行尾部、重复调用为空且之后禁止 feed。

    无参数和返回值；尾部凭据必须只返回一次，错误输入类型防御为
    ``ValueError``，结束后的 feed 固定抛出 ``RuntimeError``。
    """
    redactor = StreamingRedactor()
    with pytest.raises(ValueError, match="text"):
        redactor.feed(object())  # type: ignore[arg-type]

    assert redactor.feed("password=TAIL_SECRET") == ""
    assert redactor.finalize() == "password=<redacted>"
    assert redactor.finalize() == ""
    with pytest.raises(RuntimeError, match="finalize"):
        redactor.feed("later")


@pytest.mark.parametrize(
    "max_pending_chars",
    [0, -1, True, "8", *range(1, MIN_STREAMING_PENDING_CHARS)],
)
def test_streaming_redactor_rejects_invalid_pending_limits(max_pending_chars: object) -> None:
    """验证 pending 上限拒绝无法识别 PEM BEGIN sentinel 的配置。

    ``max_pending_chars`` 覆盖零、负数、布尔、错误类型及 1 到公开最小值前一位；
    构造必须抛出 ``ValueError`` 且不分配待处理秘密状态。
    """
    with pytest.raises(ValueError, match="max_pending_chars"):
        StreamingRedactor(max_pending_chars=max_pending_chars)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "max_pending_chars",
    [MIN_STREAMING_PENDING_CHARS, MIN_STREAMING_PENDING_CHARS + 1],
)
def test_streaming_redactor_minimum_limits_fail_closed_for_characterwise_pem(
    max_pending_chars: int,
) -> None:
    """验证最小安全上限两侧逐字符 PEM 进入永久脱敏态且不泄漏短正文。

    ``max_pending_chars`` 是公开最小值及其后一位。BEGIN sentinel 可被完整识别，
    后续 label 超限时必须只输出一次私钥占位符；KEY、END 与其后文本均不恢复。
    """
    text = "-----BEGIN PRIVATE KEY-----\nKEY\n-----END PRIVATE KEY-----\nAFTER\n"
    redactor = StreamingRedactor(max_pending_chars=max_pending_chars)

    output = "".join(redactor.feed(character) for character in text)

    assert MIN_STREAMING_PENDING_CHARS == len("-----BEGIN ")
    assert output == "<redacted-private-key>"
    assert "KEY" not in output
    assert "AFTER" not in output
    assert redactor.pending_size <= max_pending_chars
    assert redactor.finalize() == ""


def test_streaming_redactor_never_leaks_after_overlong_private_key_line() -> None:
    """复现 PEM 内部超长行清空旧状态后泄漏后续私钥材料的问题。

    无参数和返回值；上限为 64，BEGIN 后输入 65 字符行、代表性秘密和 END。
    全部返回片段都不得含秘密，完整私钥块只允许输出私钥占位符。
    """
    redactor = StreamingRedactor(max_pending_chars=64)
    chunks = (
        "-----BEGIN PRIVATE KEY-----\n",
        f"{'B' * 65}\n",
        "LEAKED_KEY_MATERIAL\n",
        "-----END PRIVATE KEY-----\n",
    )

    output = "".join(redactor.feed(chunk) for chunk in chunks)
    output += redactor.finalize()

    assert "LEAKED_KEY_MATERIAL" not in output
    assert "B" * 65 not in output
    assert output == "<redacted-private-key>\n"


def test_streaming_redactor_keeps_unclosed_private_key_state_bounded_for_many_lines() -> None:
    """验证永久不闭合 PEM 的内部状态不随累计行数增长且 finalize 不泄漏。

    无参数和返回值；BEGIN 后输入 10,000 条 32 字符材料行，已超过旧实现和配置
    上限。每次 feed 除首次 marker 外均为空，公开 pending 大小保持常量有界。
    """
    redactor = StreamingRedactor(max_pending_chars=64)
    assert redactor.feed("-----BEGIN PRIVATE KEY-----\n") == "<redacted-private-key>"

    for _index in range(10_000):
        assert redactor.feed(f"{'K' * 32}\n") == ""
        assert redactor.pending_size <= 128

    assert redactor.finalize() == ""
    assert redactor.pending_size == 0
    assert redactor.finalize() == ""


def test_streaming_redactor_discards_overlong_private_key_line_without_second_marker() -> None:
    """验证 PEM 内超长无换行材料不触发普通长行 marker 或退出私钥状态。

    无参数和返回值；内部材料分块后远超 64 字符，再输入换行、秘密和 END。输出
    只能包含一次私钥 marker 与 END 行终止符，内部状态始终有界。
    """
    redactor = StreamingRedactor(max_pending_chars=64)
    output = redactor.feed("-----BEGIN PRIVATE KEY-----\n")
    output += redactor.feed("M" * 10_000)
    assert redactor.pending_size <= 128
    output += redactor.feed("\nLEAKED_AFTER_LONG_LINE\n")
    output += redactor.feed("-----END PRIVATE KEY-----\n")
    output += redactor.finalize()

    assert output == "<redacted-private-key>\n"
    assert "<redacted-long-line>" not in output
    assert "LEAKED_AFTER_LONG_LINE" not in output
    assert "M" * 64 not in output


def test_streaming_redactor_finds_overlong_end_line_at_every_marker_split() -> None:
    """验证超长 END 行和 marker 任意 chunk 切分仍以有界 suffix 正确退出。

    无参数和返回值；END 前有 256 字符私钥材料，END 后同一行有普通 suffix 与
    CRLF。遍历 marker 每个切分点，输出必须只保留私钥 marker、suffix 和终止符。
    """
    end_marker = "-----END OPENSSH PRIVATE KEY-----"
    expected = "<redacted-private-key>visible-suffix\r\n"

    for split_index in range(len(end_marker) + 1):
        redactor = StreamingRedactor(max_pending_chars=64)
        output = redactor.feed("-----BEGIN OPENSSH PRIVATE KEY-----\n")
        output += redactor.feed(f"{'Q' * 256}{end_marker[:split_index]}")
        assert redactor.pending_size <= 128
        output += redactor.feed(f"{end_marker[split_index:]}visible-suffix\r")
        output += redactor.feed("\n")
        output += redactor.finalize()

        assert output == expected, f"END marker 切分点 {split_index} 结果不一致"
        assert "Q" * 64 not in output


def test_streaming_redactor_handles_same_line_private_key_and_suffix() -> None:
    """验证同一逻辑行包含 BEGIN、END 与后缀时只替换私钥块。

    无参数和返回值；marker 本身逐字符输入以覆盖 BEGIN/END 跨 chunk。输出须等于
    整段 ``redact_text``，END 后 suffix 及原 CRLF 完整保留。
    """
    text = "prefix-----BEGIN RSA PRIVATE KEY-----SECRET-----END RSA PRIVATE KEY-----suffix\r\n"
    redactor = StreamingRedactor(max_pending_chars=64)

    output = "".join(redactor.feed(character) for character in text)
    output += redactor.finalize()

    assert output == redact_text(text)
    assert output == "prefix<redacted-private-key>suffix\r\n"
    assert "SECRET" not in output


def test_streaming_redactor_ignores_mismatched_private_key_end_until_matching_end() -> None:
    """复现错误 END 标签让旧状态机提前退出并泄漏后续私钥材料的问题。

    无参数和返回值；RSA BEGIN 后先出现普通 PRIVATE KEY END，再输入代表性秘密，
    最后才给出正确 RSA END。错误 END 不得清状态或输出任何块内内容。
    """
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "SECRET_A\n"
        "-----END PRIVATE KEY-----\n"
        "LEAKED_AFTER_MISMATCH\n"
        "-----END RSA PRIVATE KEY-----\n"
    )
    redactor = StreamingRedactor(max_pending_chars=64)

    output = "".join(redactor.feed(line) for line in text.splitlines(keepends=True))
    output += redactor.finalize()

    assert "SECRET_A" not in output
    assert "LEAKED_AFTER_MISMATCH" not in output
    assert output == redact_text(text)
    assert output == "<redacted-private-key>\n"


def test_streaming_redactor_keeps_wrong_end_permanently_closed() -> None:
    """验证只有错误 END 的 PEM 永久保持丢弃态并在 finalize 静默结束。

    无参数和返回值；RSA BEGIN 后只有普通 PRIVATE KEY END 和后续秘密。除首次
    marker 外不得输出块内容，rolling pending 受对应 RSA END 长度约束。
    """
    expected_end = "-----END RSA PRIVATE KEY-----"
    redactor = StreamingRedactor(max_pending_chars=64)
    output = redactor.feed("-----BEGIN RSA PRIVATE KEY-----\n")
    output += redactor.feed("-----END PRIVATE KEY-----\n")
    output += redactor.feed("PERMANENTLY_HIDDEN\n")

    assert output == "<redacted-private-key>"
    assert "PERMANENTLY_HIDDEN" not in output
    assert redactor.pending_size < len(expected_end)
    assert redactor.finalize() == ""
    assert redactor.pending_size == 0


@pytest.mark.parametrize(
    "label",
    ["PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "ENCRYPTED PRIVATE KEY"],
)
def test_streaming_redactor_supports_matching_private_key_labels_case_insensitively(
    label: str,
) -> None:
    """验证常见 PEM label 仅由同标签 END 结束且允许大小写差异。

    ``label`` 覆盖普通、RSA、EC 与 ENCRYPTED 私钥。BEGIN 使用大写，END 使用
    小写；流式结果必须等于整段脱敏并保留正确 END 行终止符。
    """
    text = f"-----BEGIN {label}-----\nLABEL_SECRET\n-----end {label.lower()}-----\r\n"
    redactor = StreamingRedactor(max_pending_chars=64)

    output = "".join(redactor.feed(character) for character in text)
    output += redactor.finalize()

    assert output == redact_text(text)
    assert output == "<redacted-private-key>\r\n"
    assert "LABEL_SECRET" not in output


def test_streaming_redactor_checks_wrong_and_correct_end_at_every_split() -> None:
    """验证错误与正确 END marker 的每个 chunk 切分点都保持标签状态。

    无参数和返回值；错误普通 END 后插入秘密，最终正确 RSA END 才允许退出。分别
    遍历两个 marker 的所有切分点，结果始终等于整段脱敏且 pending 有界。
    """
    wrong_end = "-----END PRIVATE KEY-----"
    correct_end = "-----END RSA PRIVATE KEY-----"
    expected = "<redacted-private-key>\n"

    def run_case(wrong_split: int, correct_split: int) -> str:
        """按指定错误/正确 END 切分点运行一次完整 RSA PEM 场景。"""
        redactor = StreamingRedactor(max_pending_chars=64)
        output = redactor.feed("-----BEGIN RSA PRIVATE KEY-----\nSECRET_A\n")
        output += redactor.feed(wrong_end[:wrong_split])
        output += redactor.feed(f"{wrong_end[wrong_split:]}\nLEAKED_AFTER_MISMATCH\n")
        assert redactor.pending_size < len(correct_end)
        output += redactor.feed(correct_end[:correct_split])
        output += redactor.feed(f"{correct_end[correct_split:]}\n")
        output += redactor.finalize()
        return output

    for split_index in range(len(wrong_end) + 1):
        output = run_case(split_index, len(correct_end) // 2)
        assert output == expected
        assert "LEAKED_AFTER_MISMATCH" not in output

    for split_index in range(len(correct_end) + 1):
        output = run_case(len(wrong_end) // 2, split_index)
        assert output == expected
        assert "LEAKED_AFTER_MISMATCH" not in output


def test_streaming_redactor_redacts_private_key_label_longer_than_legacy_cap() -> None:
    """复现超过旧 64 字符前缀上限的合法 PEM 标签被逐行明文输出。

    无参数和返回值；65 字符前缀的完整 BEGIN 行仍低于 pending 上限。流式结果
    必须与整段 ``redact_text`` 一致，私钥材料及标签均不得泄漏。
    """
    label = f"{'A' * 65}PRIVATE KEY"
    text = f"-----BEGIN {label}-----\nLONG_LABEL_SECRET\n-----END {label}-----\n"
    redactor = StreamingRedactor(max_pending_chars=512)

    output = "".join(redactor.feed(line) for line in text.splitlines(keepends=True))
    output += redactor.finalize()

    assert output == redact_text(text)
    assert output == "<redacted-private-key>\n"
    assert "LONG_LABEL_SECRET" not in output
    assert label not in output


@pytest.mark.parametrize("prefix_length", [64, 65, 484, 485])
def test_streaming_redactor_matches_whole_text_for_long_labels_at_pending_boundaries(
    prefix_length: int,
) -> None:
    """验证合法长标签和 BEGIN 行 pending 边界在逐字符输入时保持整段语义。

    ``prefix_length`` 覆盖旧上限两侧，以及使 BEGIN 行分别接近和恰好达到 512
    字符的长度。流式输出必须与 ``redact_text`` 一致且不含标签或材料。
    """
    max_pending_chars = 512
    label = f"{'A' * prefix_length}PRIVATE KEY"
    begin_marker = f"-----BEGIN {label}-----"
    text = f"{begin_marker}\nBOUNDARY_SECRET\n-----END {label}-----\n"
    redactor = StreamingRedactor(max_pending_chars=max_pending_chars)

    output = "".join(redactor.feed(character) for character in text)
    output += redactor.finalize()

    assert len(begin_marker) in {91, 92, 511, 512}
    assert output == redact_text(text)
    assert output == "<redacted-private-key>\n"
    assert "BOUNDARY_SECRET" not in output
    assert label not in output


def test_streaming_redactor_fail_closes_overlong_incomplete_begin_candidate() -> None:
    """验证超限时尚未完整的 BEGIN 候选永久进入有界私钥丢弃态。

    无参数和返回值；逐字符输入让候选在完整 label 出现前超过 64 字符，随后提供
    错误 END、秘密、正确 END 与块外文本。状态不得因任何 END 恢复，且只输出
    一次私钥占位符，``finalize`` 静默结束。
    """
    max_pending_chars = 64
    label = f"{'A' * 65}PRIVATE KEY"
    text = (
        f"-----BEGIN {label}-----\n"
        "-----END PRIVATE KEY-----\n"
        "LEAK_AFTER_FAKE_END\n"
        f"-----END {label}-----\n"
        "LEAK_AFTER_TRUE_END\n"
    )
    redactor = StreamingRedactor(max_pending_chars=max_pending_chars)

    chunks = [redactor.feed(character) for character in text]

    assert "".join(chunks) == "<redacted-private-key>"
    assert sum(chunk == "<redacted-private-key>" for chunk in chunks) == 1
    assert redactor.pending_size <= max_pending_chars
    assert redactor.finalize() == ""
    assert redactor.pending_size == 0


def test_streaming_redactor_finds_begin_sentinel_after_long_line_discard_starts() -> None:
    """复现普通长行丢弃态漏扫后续 PEM sentinel 并泄漏正文的问题。

    无参数和返回值；64 字符上限先被 65 个普通字符触发，再于同一逻辑行出现
    BEGIN。单 chunk、逐字符和全部二段切分必须产生相同 fail-closed 输出，KEY、
    任意 END 与其后内容均不得恢复，内部 pending 始终有界。
    """
    max_pending_chars = 64
    text = f"{'X' * 65}-----BEGIN PRIVATE KEY-----\nKEY\n-----END PRIVATE KEY-----\nAFTER\n"
    expected = "<redacted-long-line><redacted-private-key>"

    def redact_chunks(chunks: tuple[str, ...]) -> str:
        """按给定 chunk 运行真实状态机，并在每步验证公开内存上界。"""
        redactor = StreamingRedactor(max_pending_chars=max_pending_chars)
        output: list[str] = []
        for chunk in chunks:
            output.append(redactor.feed(chunk))
            assert redactor.pending_size <= max_pending_chars
        output.append(redactor.finalize())
        return "".join(output)

    assert redact_chunks((text,)) == expected
    assert redact_chunks(tuple(text)) == expected
    for split_index in range(len(text) + 1):
        output = redact_chunks((text[:split_index], text[split_index:]))
        assert output == expected, f"sentinel 切分点 {split_index} 结果不一致"
        assert "KEY" not in output
        assert "AFTER" not in output
