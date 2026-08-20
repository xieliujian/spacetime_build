"""验证 CLI 人类/JSON 输出脱敏契约。"""

from cli.output import render_error, render_success
from core.errors import ConfigurationError


def test_json_error_contains_code_and_no_traceback_or_secret() -> None:
    """Given 含 secret 文本的错误，When JSON 输出，Then 只保留脱敏字段。"""
    text = render_error(
        ConfigurationError("token=super-secret"),
        code=2,
        json_mode=True,
        run_id="run-1",
    )
    assert '"code": 2' in text
    assert "super-secret" not in text
    assert "traceback" not in text


def test_human_success_is_compact_and_redacted() -> None:
    """Given 成功结果，When 人类输出，Then 结果可读且不泄漏凭据。"""
    text = render_success({"run_id": "run-1", "token": "super-secret"}, json_mode=False)
    assert "run-1" in text
    assert "super-secret" not in text
