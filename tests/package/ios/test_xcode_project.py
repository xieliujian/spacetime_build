"""验证 iOS Xcode 工程结构化变换计划的确定性、幂等和冲突边界。

测试只消费内存中的计划和专用工具请求，不创建、解析或修改 ``pbxproj`` 文件，也不
执行 Xcode 或 Ruby 工具。
"""

from __future__ import annotations

import json

import pytest

from package.platforms.ios.xcode_project import (
    XcodeBuildSetting,
    XcodeEntitlement,
    XcodeFramework,
    XcodeLibrary,
    XcodeProjectPlan,
    XcodeProjectPlanner,
    XcodeTargetPlan,
)


def _target(
    name: str = "Game",
    *,
    settings: tuple[XcodeBuildSetting, ...] = (),
    frameworks: tuple[XcodeFramework, ...] = (),
    libraries: tuple[XcodeLibrary, ...] = (),
    entitlements: tuple[XcodeEntitlement, ...] = (),
) -> XcodeTargetPlan:
    """创建测试 target 计划。"""
    return XcodeTargetPlan(
        name=name,
        build_settings=settings,
        frameworks=frameworks,
        libraries=libraries,
        entitlements=entitlements,
    )


def test_xcode_project_plan_normalizes_targets_and_structured_changes() -> None:
    """验证 target 及其四类结构化变更按稳定键排序并保留值。"""
    plan = XcodeProjectPlanner.plan(
        project_path="Game.xcodeproj",
        targets=(
            _target(
                "Unity-iPhone",
                settings=(
                    XcodeBuildSetting("PRODUCT_BUNDLE_IDENTIFIER", "com.example.game"),
                    XcodeBuildSetting("SWIFT_VERSION", "5.0"),
                ),
                frameworks=(XcodeFramework("StoreKit.framework"),),
                libraries=(XcodeLibrary("libz.tbd"),),
                entitlements=(XcodeEntitlement("aps-environment", "production"),),
            ),
            _target("Game"),
        ),
    )

    assert plan == XcodeProjectPlan(
        project_path="Game.xcodeproj",
        targets=(
            _target(
                "Game",
            ),
            _target(
                "Unity-iPhone",
                settings=(
                    XcodeBuildSetting("PRODUCT_BUNDLE_IDENTIFIER", "com.example.game"),
                    XcodeBuildSetting("SWIFT_VERSION", "5.0"),
                ),
                frameworks=(XcodeFramework("StoreKit.framework"),),
                libraries=(XcodeLibrary("libz.tbd"),),
                entitlements=(XcodeEntitlement("aps-environment", "production"),),
            ),
        ),
    )


def test_xcode_project_plan_merges_duplicate_equal_changes_idempotently() -> None:
    """验证同一 target 的重复同值变更可以合并，重复应用计划不会改变结果。"""
    plan = XcodeProjectPlanner.plan(
        project_path="Game.xcodeproj",
        targets=(
            _target(
                settings=(
                    XcodeBuildSetting("ENABLE_BITCODE", "NO"),
                    XcodeBuildSetting("ENABLE_BITCODE", "NO"),
                ),
                frameworks=(XcodeFramework("UIKit.framework"), XcodeFramework("UIKit.framework")),
                libraries=(XcodeLibrary("libc++.tbd"), XcodeLibrary("libc++.tbd")),
                entitlements=(
                    XcodeEntitlement("com.apple.developer.team-identifier", "TEAM"),
                    XcodeEntitlement("com.apple.developer.team-identifier", "TEAM"),
                ),
            ),
        ),
    )

    assert plan.targets[0].build_settings == (XcodeBuildSetting("ENABLE_BITCODE", "NO"),)
    assert plan.targets[0].frameworks == (XcodeFramework("UIKit.framework"),)
    assert plan.targets[0].libraries == (XcodeLibrary("libc++.tbd"),)
    assert plan.targets[0].entitlements == (
        XcodeEntitlement("com.apple.developer.team-identifier", "TEAM"),
    )
    assert XcodeProjectPlanner.plan(project_path="Game.xcodeproj", targets=plan.targets) == plan


@pytest.mark.parametrize(
    "targets",
    (
        (
            _target(settings=(XcodeBuildSetting("SWIFT_VERSION", "5.0"),)),
            _target(settings=(XcodeBuildSetting("SWIFT_VERSION", "5.9"),)),
        ),
        (
            _target(frameworks=(XcodeFramework("UIKit.framework"),)),
            _target(frameworks=(XcodeFramework("StoreKit.framework"),)),
        ),
        (
            _target(entitlements=(XcodeEntitlement("aps-environment", "development"),)),
            _target(entitlements=(XcodeEntitlement("aps-environment", "production"),)),
        ),
    ),
)
def test_xcode_project_plan_rejects_conflicting_changes(
    targets: tuple[XcodeTargetPlan, ...],
) -> None:
    """验证同一 target 的不同值声明不会由规划器静默覆盖。"""
    with pytest.raises(ValueError, match="冲突"):
        XcodeProjectPlanner.plan(project_path="Game.xcodeproj", targets=targets)


def test_xcode_project_plan_rejects_duplicate_target_and_invalid_project_path() -> None:
    """验证 target 名称重复及工程路径越界/文本污染都会失败。"""
    with pytest.raises(ValueError, match="target"):
        XcodeProjectPlanner.plan(
            project_path="Game.xcodeproj",
            targets=(_target(), _target()),
        )
    with pytest.raises(ValueError):
        XcodeProjectPlanner.plan(project_path="../Game.xcodeproj", targets=(_target(),))
    with pytest.raises(ValueError):
        XcodeProjectPlanner.plan(project_path="Game\\Game.xcodeproj", targets=(_target(),))


def test_xcode_project_plan_generates_deterministic_tool_request_without_pbxproj_editing() -> None:
    """验证专用工具请求是稳定 JSON 数据，且只描述白名单结构变更。"""
    plan = XcodeProjectPlanner.plan(
        project_path="Game.xcodeproj",
        targets=(
            _target(
                settings=(XcodeBuildSetting("PRODUCT_NAME", "Game"),),
                frameworks=(XcodeFramework("Foundation.framework", weak=True),),
                libraries=(XcodeLibrary("libsqlite3.tbd", weak=True),),
                entitlements=(XcodeEntitlement("get-task-allow", False),),
            ),
        ),
    )

    first = plan.to_tool_request().to_json()
    second = plan.to_tool_request().to_json()

    assert first == second
    document = json.loads(first)
    assert document == {
        "operation": "apply_xcode_project_plan",
        "project_path": "Game.xcodeproj",
        "targets": [
            {
                "build_settings": [{"key": "PRODUCT_NAME", "value": "Game"}],
                "entitlements": [{"key": "get-task-allow", "value": False}],
                "frameworks": [{"name": "Foundation.framework", "weak": True}],
                "libraries": [{"name": "libsqlite3.tbd", "weak": True}],
                "name": "Game",
            }
        ],
    }
    assert b"pbxproj" not in first
