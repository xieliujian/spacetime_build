"""Gradle 结构化配置计划测试。"""

import pytest

from package.platforms.android.gradle_config import GradleConfigurationPlanner
from package.platforms.android.model import (
    AndroidAbi,
    AndroidBuildType,
    AndroidOutputKind,
    AndroidPackageOptions,
)


def test_gradle_configuration_plan_is_deterministic_and_whitelisted() -> None:
    """验证配置计划包含版本/ABI且仓库地址按白名单冻结。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.APK,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        3,
    )
    plan = GradleConfigurationPlanner.plan(options, repositories=("https://repo.example.com",))
    assert plan.application_id == "com.example.game"
    assert plan.repositories == ("https://repo.example.com",)
    assert (
        GradleConfigurationPlanner.plan(options, repositories=("https://repo.example.com",)) == plan
    )


def test_gradle_configuration_plan_rejects_untrusted_repository() -> None:
    """验证不受信任仓库不能进入 Gradle 配置计划。"""
    options = AndroidPackageOptions(
        AndroidOutputKind.AAB,
        (AndroidAbi.ARM64_V8A,),
        AndroidBuildType.RELEASE,
        "com.example.game",
        3,
    )
    with pytest.raises(ValueError):
        GradleConfigurationPlanner.plan(options, repositories=("file:///tmp/repo",))
