"""验证 CLI TOML profile 的白名单解码和继承。"""

from pathlib import Path

import pytest

from cli.config import BuildConfigLoader, ConfigError


def _write_config(path: Path, body: str) -> Path:
    """写入测试用 UTF-8 TOML 文件并返回路径。"""
    path.write_text(body, encoding="utf-8")
    return path


def test_loader_applies_profile_inheritance_and_records_toml_sources(tmp_path: Path) -> None:
    """Given base/profile TOML，When 加载，Then 得到不可变配置和来源追踪。"""
    path = _write_config(
        tmp_path / "build.toml",
        """
[build]
profile = "android_release"
resources = ["config", "scene"]

[toolchain]
unity_version = "2022.3.62f2"

[source]
provider = "svn"
revision = "12345"
credential = "secret://svn/build"

[publish]
target = "staging"
subpackage = true
redirect = false

[profiles.base]
max_workers = 2

[profiles.android_release]
extends = "base"
max_workers = 4
""",
    )

    snapshot = BuildConfigLoader().load(path)

    assert snapshot.config.profile == "android_release"
    assert snapshot.config.max_workers == 4
    assert snapshot.config.source_credential is not None
    assert snapshot.source_of("max_workers") == "profile"
    assert snapshot.source_of("publish_target") == "toml"
    assert repr(snapshot.config.source_credential) == "SecretRef(<redacted>)"


@pytest.mark.parametrize(
    "body",
    (
        "[build]\nunknown = true\n",
        "[profiles.base]\nextends = 'missing'\n",
        "[profiles.a]\nextends = 'b'\n[profiles.b]\nextends = 'a'\n",
        "[build]\nresources = ['../escape']\n",
        "[build]\nmax_workers = '4'\n",
    ),
)
def test_loader_rejects_unknown_types_inheritance_and_path_escape(
    tmp_path: Path, body: str
) -> None:
    """Given 非法 schema，When 加载，Then 以统一 ConfigError 失败。"""
    path = _write_config(tmp_path / "invalid.toml", body)
    with pytest.raises(ConfigError):
        BuildConfigLoader().load(path)


def test_loader_rejects_non_utf8_toml(tmp_path: Path) -> None:
    """Given 非 UTF-8 配置文件，When 加载，Then 不向业务层泄漏解码异常。"""
    path = tmp_path / "invalid.toml"
    path.write_bytes(b"[build]\nprofile = '\xff'\n")
    with pytest.raises(ConfigError):
        BuildConfigLoader().load(path)
