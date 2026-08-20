"""iOS Xcode 工程的结构化变换计划。

本模块只描述后续专用工程编辑器需要执行的白名单变换：target、build setting、
framework、library 和 entitlements。规划阶段不读取工程目录，不解析或拼接
``pbxproj`` 文本，也不执行 Xcode、Ruby 或其他外部工具；Task 5 的应用器可以把
``XcodeProjectToolRequest`` 交给受控工具处理。所有公开计划均为不可变对象，并在
规划时完成确定性排序、重复合并和冲突拒绝。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import TypeAlias, cast

_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
EntitlementValue: TypeAlias = str | bool | int


@dataclass(frozen=True, slots=True)
class XcodeBuildSetting:
    """描述一个 target 的 Xcode build setting 键值。

    参数：
        key: Xcode 白名单设置名，例如 ``PRODUCT_BUNDLE_IDENTIFIER``。
        value: 设置值；规划器将其作为字符串原样传给专用工程工具。

    返回：
        一个不可变的设置项。

    异常：
        键或值不是非空且无控制字符的字符串时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        该类型不定义任意设置名白名单；具体调用方可在进入规划器前选择业务白名单，
        本模块只保证结构安全和同一键的冲突可检测。不执行 I/O。
    """

    key: str
    value: str

    def __post_init__(self) -> None:
        """校验 build setting 的键和值，避免控制字符进入工具请求。"""
        _validate_text(self.key, "build setting key")
        _validate_text(self.value, "build setting value")


@dataclass(frozen=True, slots=True)
class XcodeFramework:
    """描述一个 target 需要链接的 framework。

    参数：
        name: framework 文件名或受控工程相对名称。
        weak: 是否以 weak link 方式链接。

    返回：
        一个不可变 framework 变更项。

    异常：
        名称不是安全的非空文本，或 ``weak`` 不是布尔值时抛出 ``TypeError`` 或
        ``ValueError``。

    约束与副作用：
        名称只作为结构化标识保存，不根据本机文件系统解析路径，不检查 framework
        是否存在。
    """

    name: str
    weak: bool = False

    def __post_init__(self) -> None:
        """校验 framework 名称和 weak-link 标记。"""
        _validate_text(self.name, "framework name")
        if not isinstance(self.weak, bool):
            raise TypeError("framework weak 必须是 bool")


@dataclass(frozen=True, slots=True)
class XcodeLibrary:
    """描述一个 target 需要链接的静态库、动态库或 ``.tbd`` 库。

    参数：
        name: 库文件名或受控工程相对名称。
        weak: 是否以 weak link 方式链接。

    返回：
        一个不可变 library 变更项。

    异常：
        名称不是安全的非空文本，或 ``weak`` 不是布尔值时抛出 ``TypeError`` 或
        ``ValueError``。

    约束与副作用：
        该模型不扫描文件、不判断库类型，也不修改工程。
    """

    name: str
    weak: bool = False

    def __post_init__(self) -> None:
        """校验 library 名称和 weak-link 标记。"""
        _validate_text(self.name, "library name")
        if not isinstance(self.weak, bool):
            raise TypeError("library weak 必须是 bool")


@dataclass(frozen=True, slots=True)
class XcodeEntitlement:
    """描述一个 target 的 entitlements 键值。

    参数：
        key: entitlement 键名。
        value: plist 中可表达的字符串、布尔值或整数。

    返回：
        一个不可变 entitlement 变更项。

    异常：
        键不合法，或值不是支持的 plist 标量类型时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        仅保留结构化标量，不编码 plist，不读取 profile，也不触及工程文件。
    """

    key: str
    value: EntitlementValue

    def __post_init__(self) -> None:
        """校验 entitlement 键和可序列化的标量值。"""
        _validate_text(self.key, "entitlement key")
        if not isinstance(self.value, (str, bool, int)):
            raise TypeError("entitlement value 必须是 str、bool 或 int")
        if isinstance(self.value, str):
            _validate_text(self.value, "entitlement value")


@dataclass(frozen=True, slots=True)
class XcodeTargetPlan:
    """描述一个 Xcode target 的全部白名单变换项。

    参数：
        name: target 名称。
        build_settings: target 级 build setting 声明。
        frameworks: target 级 framework 声明。
        libraries: target 级 library 声明。
        entitlements: target 级 entitlement 声明。

    返回：
        一个不可变但尚未排序的 target 计划；通常由 ``XcodeProjectPlanner.plan``
        规范化后供后续应用器消费。

    异常：
        名称、集合类型或集合项类型非法时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        构造只校验结构，不合并重复项；重复同值项和冲突项由项目规划器统一处理，
        从而保证不同 target 输入顺序得到同一个规范计划。
    """

    name: str
    build_settings: tuple[XcodeBuildSetting, ...] = ()
    frameworks: tuple[XcodeFramework, ...] = ()
    libraries: tuple[XcodeLibrary, ...] = ()
    entitlements: tuple[XcodeEntitlement, ...] = ()

    def __post_init__(self) -> None:
        """校验 target 名称和四类不可变变更集合。"""
        _validate_text(self.name, "target name")
        _validate_items(self.build_settings, XcodeBuildSetting, "build_settings")
        _validate_items(self.frameworks, XcodeFramework, "frameworks")
        _validate_items(self.libraries, XcodeLibrary, "libraries")
        _validate_items(self.entitlements, XcodeEntitlement, "entitlements")


@dataclass(frozen=True, slots=True)
class XcodeProjectToolRequest:
    """供后续专用 Xcode 工程编辑器消费的确定性请求。

    参数：
        project_path: workspace 内的相对 ``.xcodeproj`` 路径。
        targets: 已经规范化的 target 计划。

    返回：
        一个只包含白名单字段的不可变工具请求；``to_json`` 返回确定性 UTF-8 JSON。

    异常：
        路径或 target 类型非法时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        请求不包含脚本、不包含原始工程文本，也不提供任意文件写入指令；序列化
        过程只在内存中执行。
    """

    project_path: str
    targets: tuple[XcodeTargetPlan, ...]

    def __post_init__(self) -> None:
        """校验工具请求中的路径和 target 集合。"""
        _validate_project_path(self.project_path)
        _validate_items(self.targets, XcodeTargetPlan, "targets")

    def as_dict(self) -> dict[str, object]:
        """返回供 JSON 编码的固定白名单字典，不执行外部操作。"""
        return {
            "operation": "apply_xcode_project_plan",
            "project_path": self.project_path,
            "targets": [_target_dict(target) for target in self.targets],
        }

    def to_json(self) -> bytes:
        """返回稳定 UTF-8 JSON 请求字节，不写入文件或启动工程工具。"""
        return (
            json.dumps(
                self.as_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class XcodeProjectPlan:
    """绑定工程路径和已规范化 target 变换的不可变计划。

    参数：
        project_path: workspace 内的相对 ``.xcodeproj`` 路径。
        targets: 按 target 名称排序且无冲突的 target 计划。

    返回：
        可比较、可重复序列化并可交给后续应用器的结构化工程计划。

    异常：
        路径或 target 集合类型非法时抛出 ``TypeError`` 或 ``ValueError``；业务冲突
        由 ``XcodeProjectPlanner.plan`` 在构造前拒绝。

    约束与副作用：
        计划对象只表达意图，不代表工程已经变更；构造和转换均不访问文件系统。
    """

    project_path: str
    targets: tuple[XcodeTargetPlan, ...]

    def __post_init__(self) -> None:
        """校验计划字段的基本类型和结构。"""
        _validate_project_path(self.project_path)
        _validate_items(self.targets, XcodeTargetPlan, "targets")

    def to_tool_request(self) -> XcodeProjectToolRequest:
        """将计划转换为后续专用工具可消费的不可变请求。"""
        return XcodeProjectToolRequest(self.project_path, self.targets)


class XcodeProjectPlanner:
    """把未排序的 Xcode 变换声明收敛为确定性、无冲突的计划。"""

    @staticmethod
    def plan(
        project_path: str,
        *,
        targets: tuple[XcodeTargetPlan, ...],
    ) -> XcodeProjectPlan:
        """校验并规范化 Xcode 工程变换声明。

        参数：
            project_path: workspace 内的相对 ``.xcodeproj`` 路径，不允许绝对路径、反斜杠、
                ``.`` 或 ``..`` 路径段。
            targets: target 计划 tuple；每个 target 名称必须唯一。

        返回：
            target、设置、链接项和 entitlements 均稳定排序、重复同值已合并的
            ``XcodeProjectPlan``。

        异常：
            输入类型非法、target 重复、同一键或链接项出现不同值，或 framework 与
            library 使用同一名称时抛出 ``TypeError`` 或 ``ValueError``。

        约束与副作用：
            规划是纯内存操作，不读取或修改 pbxproj，不调用 Xcode/Ruby，不检查工程
            目标实际存在性；不同输入排列只要声明集合相同就得到相同计划。
        """
        _validate_project_path(project_path)
        _validate_items(targets, XcodeTargetPlan, "targets")
        normalized_targets: list[XcodeTargetPlan] = []
        names: set[str] = set()
        for target in targets:
            if target.name in names:
                raise ValueError(f"target 声明冲突: {target.name}")
            names.add(target.name)
            normalized_targets.append(_normalize_target(target))
        normalized_targets.sort(key=lambda item: item.name.encode("utf-8"))
        return XcodeProjectPlan(project_path, tuple(normalized_targets))


def _validate_text(value: object, field_name: str) -> None:
    """校验公开标识文本非空、无空白和控制字符。"""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} 不得为空或首尾含空白")
    if any(char.isspace() or unicodedata.category(char).startswith("C") for char in value):
        raise ValueError(f"{field_name} 不得包含空白或控制字符")


def _validate_project_path(value: object) -> None:
    """校验工程路径是 workspace 内安全的相对 Xcode project 路径。"""
    _validate_text(value, "project_path")
    assert isinstance(value, str)
    if "\\" in value or value.startswith("/") or _DRIVE_PATTERN.match(value):
        raise ValueError("project_path 必须是相对正斜杠路径")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("project_path 不得包含空段、. 或 ..")
    if not value.endswith(".xcodeproj"):
        raise ValueError("project_path 必须以 .xcodeproj 结尾")


def _validate_items(values: object, item_type: type[object], field_name: str) -> None:
    """校验不可变集合及其元素类型，防止可变输入穿透计划边界。"""
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} 必须是 tuple")
    typed_values = cast(tuple[object, ...], values)
    if not all(isinstance(item, item_type) for item in typed_values):
        raise TypeError(f"{field_name} 的每一项类型错误")


def _normalize_target(target: XcodeTargetPlan) -> XcodeTargetPlan:
    """规范化一个 target 的四类声明并检测同键冲突。"""
    settings = _normalize_settings(target.build_settings)
    frameworks = _normalize_frameworks(target.frameworks)
    libraries = _normalize_libraries(target.libraries)
    framework_names = {item.name for item in frameworks}
    library_names = {item.name for item in libraries}
    overlap = framework_names & library_names
    if overlap:
        name = sorted(overlap, key=lambda value: value.encode("utf-8"))[0]
        raise ValueError(f"target {target.name} 的 framework/library 冲突: {name}")
    entitlements = _normalize_entitlements(target.entitlements)
    return XcodeTargetPlan(target.name, settings, frameworks, libraries, entitlements)


def _normalize_settings(values: tuple[XcodeBuildSetting, ...]) -> tuple[XcodeBuildSetting, ...]:
    """按键规范化 build settings，并拒绝同键不同值。"""
    by_key: dict[str, XcodeBuildSetting] = {}
    for item in values:
        existing = by_key.get(item.key)
        if existing is not None and existing.value != item.value:
            raise ValueError(f"build setting 冲突: {item.key}")
        by_key[item.key] = item
    return tuple(sorted(by_key.values(), key=lambda item: item.key.encode("utf-8")))


def _normalize_frameworks(values: tuple[XcodeFramework, ...]) -> tuple[XcodeFramework, ...]:
    """按名称规范化 framework，并拒绝 weak 标记冲突。"""
    by_name: dict[str, XcodeFramework] = {}
    for item in values:
        existing = by_name.get(item.name)
        if existing is not None and existing.weak != item.weak:
            raise ValueError(f"framework 冲突: {item.name}")
        by_name[item.name] = item
    normalized = tuple(sorted(by_name.values(), key=lambda item: item.name.encode("utf-8")))
    return normalized


def _normalize_libraries(values: tuple[XcodeLibrary, ...]) -> tuple[XcodeLibrary, ...]:
    """按名称规范化 library，并拒绝 weak 标记冲突。"""
    by_name: dict[str, XcodeLibrary] = {}
    for item in values:
        existing = by_name.get(item.name)
        if existing is not None and existing.weak != item.weak:
            raise ValueError(f"library 冲突: {item.name}")
        by_name[item.name] = item
    normalized = tuple(sorted(by_name.values(), key=lambda item: item.name.encode("utf-8")))
    return normalized


def _normalize_entitlements(values: tuple[XcodeEntitlement, ...]) -> tuple[XcodeEntitlement, ...]:
    """按键规范化 entitlements，并拒绝同键不同标量值。"""
    by_key: dict[str, XcodeEntitlement] = {}
    for item in values:
        existing = by_key.get(item.key)
        if existing is not None and existing.value != item.value:
            raise ValueError(f"entitlement 冲突: {item.key}")
        by_key[item.key] = item
    return tuple(sorted(by_key.values(), key=lambda item: item.key.encode("utf-8")))


def _target_dict(target: XcodeTargetPlan) -> dict[str, object]:
    """把规范化 target 转成只含工具白名单字段的 JSON 字典。"""
    return {
        "build_settings": [
            {"key": item.key, "value": item.value} for item in target.build_settings
        ],
        "entitlements": [{"key": item.key, "value": item.value} for item in target.entitlements],
        "frameworks": [{"name": item.name, "weak": item.weak} for item in target.frameworks],
        "libraries": [{"name": item.name, "weak": item.weak} for item in target.libraries],
        "name": target.name,
    }


__all__ = [
    "EntitlementValue",
    "XcodeBuildSetting",
    "XcodeEntitlement",
    "XcodeFramework",
    "XcodeLibrary",
    "XcodeProjectPlan",
    "XcodeProjectPlanner",
    "XcodeProjectToolRequest",
    "XcodeTargetPlan",
]
