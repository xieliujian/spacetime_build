"""完整构建配置的纯内存汇总校验测试。

本模块覆盖路径安全、Unity 绝对路径、发布入口、任务大小写折叠冲突与 release
Profile 规则，并固定 ValidationReport 的 UTF-8 稳定排序语义。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import NoReturn

import pytest

from configuration.model import (
    BuildConfig,
    LoggingConfig,
    ObjectStoreConfig,
    ProfileConfig,
    ProjectConfig,
    PublishLayoutConfig,
    SecretRef,
    TaskConfig,
    UnityToolConfig,
    VersionControlConfig,
)
from configuration.validator import ValidationIssue, ValidationReport, validate_build_config


def _valid_config() -> BuildConfig:
    """创建满足所有 Task 3 纯校验规则的完整配置。

    返回：
        包含 release Profile、绝对 Unity 路径和无冲突任务的配置。

    约束与副作用：
        所有 Path 仅作为值对象；不要求实际文件或目录存在。
    """
    return BuildConfig(
        schema_version=1,
        project=ProjectConfig(
            "spacetime",
            Path("workspace/source"),
            Path("workspace/output"),
            Path("workspace/temp"),
        ),
        profiles=(
            ("debug", ProfileConfig(True, False, False)),
            ("release", ProfileConfig(False, True, True)),
        ),
        unity=UnityToolConfig(Path("C:/Unity/Editor/Unity.exe"), 600),
        version_control=VersionControlConfig("svn", SecretRef("secret://build/svn")),
        object_store=ObjectStoreConfig("filesystem", "release-primary", Path("artifacts")),
        publish_layout=PublishLayoutConfig(
            "releases/{branch}",
            "versions/{platform}/entry.json",
        ),
        tasks=(
            ("config", TaskConfig(True, "assets/config", "bundles/config")),
            ("shader", TaskConfig(True, "assets/shader", "bundles/shader")),
        ),
        logging=LoggingConfig("INFO", "DEBUG", False, True, Path("logs"), 14),
    )


def test_validation_value_objects_are_frozen_slotted_and_stably_sorted() -> None:
    """验证 issue/report 的冻结、slots、UTF-8 path/message 排序与有效性。

    重复 issue 不在该值对象层擅自去重；排序键固定为 path 字节再到 message 字节。
    空报告有效，含 issue 的报告无效。
    """
    beta = ValidationIssue("z.path", "乙")
    alpha_second = ValidationIssue("a.path", "乙")
    alpha_first = ValidationIssue("a.path", "甲")

    report = ValidationReport((beta, alpha_second, alpha_first))

    assert report.issues == (alpha_second, alpha_first, beta)
    assert report.is_valid is False
    assert ValidationReport(()).is_valid is True
    assert not hasattr(report, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(report, "issues", ())


def test_validate_build_config_accepts_valid_config_without_filesystem_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证纯 validator 对合法配置返回空报告且不探测文件存在性。

    参数：
        monkeypatch: 仅把文件存在性 API 替换为立即失败函数；断言关注纯校验契约，
            不检查 validator 内部调用顺序。
    """

    def fail_on_io(*_args: object, **_kwargs: object) -> NoReturn:
        """在测试中把任何文件存在性访问转化为明确失败。

        异常：
            总是抛 ``AssertionError``，证明纯 validator 不应调用该边界。
        """
        raise AssertionError("纯 validator 不得访问文件系统")

    monkeypatch.setattr(Path, "exists", fail_on_io)
    monkeypatch.setattr(Path, "is_file", fail_on_io)

    report = validate_build_config(_valid_config())

    assert report.is_valid
    assert report.issues == ()


def test_validate_build_config_aggregates_project_unity_publish_and_profile_errors() -> None:
    """验证不同主要区域的路径与规则错误一次性稳定汇总。

    Project 三路径分别覆盖绝对、父段与当前目录；同时加入相对 Unity、非法发布
    入口和 release Lua 组合，确保 validator 不在首错处停止。
    """
    config = _valid_config()
    invalid = replace(
        config,
        project=ProjectConfig(
            "spacetime",
            Path("C:/absolute/source"),
            Path("workspace/../escape"),
            Path("."),
        ),
        unity=UnityToolConfig(Path("Unity/Editor/Unity.exe"), 600),
        publish_layout=PublishLayoutConfig(
            "releases/{branch}",
            "versions//../entry.json",
        ),
        profiles=(
            ("debug", ProfileConfig(True, False, False)),
            ("release", ProfileConfig(False, False, True)),
        ),
    )

    report = validate_build_config(invalid)
    paths = tuple(issue.path for issue in report.issues)

    assert report.is_valid is False
    assert paths == tuple(sorted(paths, key=lambda item: item.encode("utf-8")))
    assert set(paths) == {
        "profile.release.encrypt_lua",
        "project.output_root",
        "project.source_root",
        "project.temp_root",
        "publish.layout.version_entry_key",
        "tools.unity.executable",
    }


@pytest.mark.parametrize(
    "version_entry_key",
    [
        "",
        "   ",
        "/versions/entry.json",
        "C:/versions/entry.json",
        r"versions\entry.json",
        "versions//entry.json",
        "versions/./entry.json",
        "versions/../entry.json",
    ],
)
def test_validate_build_config_rejects_unsafe_version_entry_keys(
    version_entry_key: str,
) -> None:
    """验证版本入口采用 TaskConfig 等价的受约束相对 ``/`` 路径规则。

    参数：
        version_entry_key: 一个空白、绝对、反斜杠或含非法路径段的 key。
    """
    config = _valid_config()
    invalid_layout = PublishLayoutConfig(
        config.publish_layout.root_prefix,
        "placeholder/entry.json",
    )
    # frozen 模型正常构造时已挡住空白值；这里模拟受损反序列化对象，证明跨层纯校验
    # 仍执行 defense-in-depth，而不是把模型局部检查当作唯一边界。
    object.__setattr__(invalid_layout, "version_entry_key", version_entry_key)
    invalid = replace(
        config,
        publish_layout=invalid_layout,
    )

    report = validate_build_config(invalid)

    assert tuple(issue.path for issue in report.issues) == ("publish.layout.version_entry_key",)


def test_validate_build_config_allows_template_braces_in_version_entry_key() -> None:
    """验证安全相对入口路径中的模板花括号保持允许。

    花括号属于布局模板业务语义，只要各路径段非空且不是点段，就不应被纯路径
    规则误拒绝。
    """
    config = _valid_config()
    templated = replace(
        config,
        publish_layout=PublishLayoutConfig(
            config.publish_layout.root_prefix,
            "{branch}/{platform}/{version}/entry.json",
        ),
    )

    assert validate_build_config(templated).is_valid


@pytest.mark.parametrize(
    "root_prefix",
    [
        "",
        "../../escape",
        "/absolute/root",
        r"release\root",
        "release//root",
        "C:/release/root",
        "release/%2e/root",
        "release/%/root",
        "release/\u200b/root",
    ],
)
def test_validate_build_config_rejects_unsafe_publish_root_prefix(
    root_prefix: str,
) -> None:
    """验证发布根前缀复用安全相对模板路径规则并汇总固定 issue。

    参数：
        root_prefix: 空值、逃逸、绝对、错误分隔、空段、盘符、URL 转义或
            Unicode ``C*`` 字符路径。

    约束与副作用：
        通过受损冻结对象覆盖空值，以证明纯 validator 在模型局部约束后仍提供
        defense-in-depth；不访问目录或解析模板。
    """
    config = _valid_config()
    invalid_layout = PublishLayoutConfig(
        "safe/root",
        config.publish_layout.version_entry_key,
    )
    object.__setattr__(invalid_layout, "root_prefix", root_prefix)
    invalid = replace(config, publish_layout=invalid_layout)

    report = validate_build_config(invalid)

    assert tuple(issue.path for issue in report.issues) == ("publish.layout.root_prefix",)


def test_validate_build_config_allows_templated_publish_root_prefix() -> None:
    """验证合法发布根前缀保留 branch/platform/job 模板花括号。

    路径各段均非空且没有点段、转义或控制字符时，纯 validator 应返回有效报告。
    """
    config = _valid_config()
    templated = replace(
        config,
        publish_layout=PublishLayoutConfig(
            "{branch}/{platform}/data/{job}",
            config.publish_layout.version_entry_key,
        ),
    )

    assert validate_build_config(templated).is_valid


@pytest.mark.parametrize(
    "version_entry_key",
    [
        "versions/%2e/entry.json",
        "versions/%2E%2E/entry.json",
        "versions/%252e/entry.json",
    ],
)
def test_validate_build_config_rejects_url_escaped_version_entry_keys(
    version_entry_key: str,
) -> None:
    """验证版本入口拒绝一次或多次 URL 转义形成的点段绕过。

    参数：
        version_entry_key: 包含大小写 ``%2e`` 或双重转义 ``%252e`` 的安全外观 key。

    约束与副作用：
        纯 validator 不执行 URL 解码，而是在入口 key 边界拒绝任意百分号。
    """
    config = _valid_config()
    escaped = replace(
        config,
        publish_layout=PublishLayoutConfig(
            config.publish_layout.root_prefix,
            version_entry_key,
        ),
    )

    report = validate_build_config(escaped)

    assert tuple(issue.path for issue in report.issues) == ("publish.layout.version_entry_key",)


def test_validate_build_config_reports_every_enabled_casefolded_output_conflict() -> None:
    """验证所有启用任务的 output 按 casefold 检查冲突项。

    同组的大小写变体及不同任务完全相同 output 均拒绝，并把每个涉及任务的
    output 字段路径加入稳定报告；共享 source 不属于所有权冲突。
    """
    config = _valid_config()
    conflicting = replace(
        config,
        tasks=(
            ("alpha", TaskConfig(True, "Assets/Config", "Bundles/Shared")),
            ("beta", TaskConfig(True, "assets/config", "bundles/shared")),
            ("gamma", TaskConfig(True, "assets/other", "bundles/shared")),
        ),
    )

    report = validate_build_config(conflicting)

    assert tuple(issue.path for issue in report.issues) == (
        "tasks.alpha.output",
        "tasks.beta.output",
        "tasks.gamma.output",
    )


def test_validate_build_config_allows_enabled_tasks_to_share_source() -> None:
    """验证多个启用任务可以读取同一 source，只要 output 所有权不同。

    source 是可共享输入而非产物所有权；大小写折叠相同也不应产生 issue。
    """
    config = _valid_config()
    shared_source = replace(
        config,
        tasks=(
            ("alpha", TaskConfig(True, "Assets/Shared", "bundles/alpha")),
            ("beta", TaskConfig(True, "assets/shared", "bundles/beta")),
        ),
    )

    assert validate_build_config(shared_source).is_valid


def test_validate_build_config_ignores_disabled_tasks_in_output_conflicts() -> None:
    """验证禁用任务不占用 output，不能误拒绝启用任务的唯一所有权。

    同一大小写折叠 output 可出现在一个启用任务和多个禁用任务中；只有同时启用
    的两个任务才构成规划冲突。
    """
    config = _valid_config()
    disabled_duplicate = replace(
        config,
        tasks=(
            ("active", TaskConfig(True, "assets/active", "Bundles/Shared")),
            ("disabled", TaskConfig(False, "assets/disabled", "bundles/shared")),
        ),
    )

    assert validate_build_config(disabled_duplicate).is_valid


def test_validate_build_config_requires_release_profile() -> None:
    """验证完整配置必须声明名称精确为 ``release`` 的 Profile 规则。

    其他合法 Profile 不能替代 release，缺失时报告固定虚拟字段路径，且不抛异常。
    """
    config = _valid_config()
    missing_release = replace(
        config,
        profiles=(("debug", ProfileConfig(True, False, False)),),
    )

    report = validate_build_config(missing_release)

    assert report.issues == (ValidationIssue("profile.release", "必须声明 release Profile"),)
