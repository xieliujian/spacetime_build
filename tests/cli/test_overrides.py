"""验证环境变量与 CLI 显式覆盖的优先级和脱敏。"""

from pathlib import Path

import pytest

from cli.config import BuildConfigLoader, ConfigSnapshot
from cli.overrides import ConfigOverrideResolver, OverrideError


def _config(tmp_path: Path) -> ConfigSnapshot:
    """创建覆盖测试用的最小 TOML 配置快照。"""
    path = tmp_path / "build.toml"
    path.write_text(
        """
[build]
profile = "release"
resources = ["config"]
max_workers = 2
[toolchain]
unity_version = "2022.3.1f1"
[source]
provider = "svn"
revision = "100"
credential = "secret://original"
[publish]
target = "staging"
subpackage = false
redirect = false
""",
        encoding="utf-8",
    )
    return BuildConfigLoader().load(path)


def test_overrides_use_cli_over_environment_and_preserve_secret_redaction(tmp_path: Path) -> None:
    """Given 同字段多层覆盖，When 解析，Then CLI 胜出且快照不含秘密文本。"""
    snapshot = _config(tmp_path)
    resolved = ConfigOverrideResolver().resolve(
        snapshot,
        environment={
            "SPACETIME_BUILD_MAX_WORKERS": "3",
            "SPACETIME_SOURCE_CREDENTIAL": "secret://env",
        },
        cli={"build.max_workers": "5"},
    )

    assert resolved.config.max_workers == 5
    assert resolved.source_of("max_workers") == "cli"
    assert resolved.config.source_credential is not None
    assert "secret://env" not in resolved.redacted_snapshot
    assert "secret://original" not in resolved.redacted_snapshot


def test_overrides_parse_boolean_integer_and_ignore_empty_environment_values(
    tmp_path: Path,
) -> None:
    """Given 显式环境值，When 转换，Then 类型严格且空值不覆盖 TOML。"""
    snapshot = _config(tmp_path)
    resolved = ConfigOverrideResolver().resolve(
        snapshot,
        environment={
            "SPACETIME_PUBLISH_SUBPACKAGE": "true",
            "SPACETIME_BUILD_MAX_WORKERS": "",
        },
        cli={},
    )

    assert resolved.config.publish_subpackage is True
    assert resolved.config.max_workers == 2


@pytest.mark.parametrize(
    ("environment", "cli"),
    (
        ({"SPACETIME_BUILD_MAX_WORKERS": "not-int"}, {}),
        ({"SPACETIME_PUBLISH_REDIRECT": "yes"}, {}),
        ({"SPACETIME_UNKNOWN": "x"}, {}),
        ({}, {"build.unknown": "4"}),
    ),
)
def test_overrides_reject_invalid_or_ambiguous_values(
    tmp_path: Path,
    environment: dict[str, str],
    cli: dict[str, str],
) -> None:
    """Given 非法类型或未白名单字段，When 覆盖，Then 统一拒绝。"""
    with pytest.raises(OverrideError):
        ConfigOverrideResolver().resolve(_config(tmp_path), environment=environment, cli=cli)
