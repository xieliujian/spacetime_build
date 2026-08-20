"""IL2CPP 本地与远端执行计划的纯内存生成器。

本模块把请求、固定 Unity 命令模板、工具链版本和受控环境组合成不可变计划。计划不
启动 Unity、不读取系统环境、不解析机器目录；本地执行器和远端协调器只消费这里生成
的参数序列，从而避免各入口自行拼接命令导致身份漂移或把本地路径发送给 CI。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from services.il2cpp.cache_key import Il2CppCacheKeyFactory
from services.il2cpp.model import Il2CppBuildRequest, Il2CppExecutionPlan

_KNOWN_COMMAND_TEMPLATES = frozenset({"unity-il2cpp-v1"})


def _validate_text(value: object, field_name: str) -> str:
    """校验计划身份文本非空且不含控制字符。"""
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 必须是非空且无控制字符字符串")
    return value


def _normalize_pairs(
    values: tuple[tuple[str, str], ...],
    field_name: str,
) -> tuple[tuple[str, str], ...]:
    """校验并按 UTF-8 key 排序工具链和环境变量。"""
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} 必须是 tuple")
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(f"{field_name} 的每项必须是二元 tuple")
        key, value = item
        _validate_text(key, f"{field_name} key")
        _validate_text(value, f"{field_name} value")
        folded = key.casefold()
        if folded in seen:
            raise ValueError(f"{field_name} 存在重复 key: {key}")
        seen.add(folded)
        normalized.append((key, value))
    return tuple(sorted(normalized, key=lambda pair: pair[0].encode("utf-8")))


@dataclass(frozen=True, slots=True)
class Il2CppToolchain:
    """描述一个已锁定的 Unity IL2CPP 命令模板和工具链版本集合。

    参数：
        unity_version: 工具链对应的 Unity 版本。
        unity_executable: 受控执行器使用的 Unity 命令标识；不由本类解析路径。
        command_template_version: 当前实现支持的固定命令模板版本。
        environment: 允许传递给执行器的环境白名单。
        toolchain_versions: Unity、NDK、Xcode 或 MSVC 等公开版本对。

    返回：
        可复用的不可变工具链描述。

    异常：
        字段文本、映射集合或版本对非法时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        仅保存调用方显式提供的信息，不读取全量环境、不访问文件系统、不启动工具。
    """

    unity_version: str
    unity_executable: str
    command_template_version: str
    environment: tuple[tuple[str, str], ...]
    toolchain_versions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """校验并规范化工具链版本和环境变量集合。"""
        _validate_text(self.unity_version, "unity_version")
        _validate_text(self.unity_executable, "unity_executable")
        _validate_text(self.command_template_version, "command_template_version")
        object.__setattr__(self, "environment", _normalize_pairs(self.environment, "environment"))
        object.__setattr__(
            self,
            "toolchain_versions",
            _normalize_pairs(self.toolchain_versions, "toolchain_versions"),
        )


@dataclass(frozen=True, slots=True)
class Il2CppCommandPlan:
    """描述已绑定执行计划的固定参数、环境和缓存命中状态。

    参数：
        execution: 含请求、workspace、输出 locator 和缓存键的基础计划。
        arguments: 供 ProcessRunner 或受控 CI 适配器消费的参数序列。
        environment: 仅包含调用方白名单环境。
        cache_hit: 是否由调用方确认命中已验证缓存；本对象不自行读取缓存。

    返回：
        可供本地执行器或远端协调器消费的不可变命令计划。

    异常：
        字段类型或参数序列包含控制字符时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        参数只表达意图，绝不代表命令已经执行；计划不包含秘密值和全量环境。
    """

    execution: Il2CppExecutionPlan
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    cache_hit: bool

    def __post_init__(self) -> None:
        """校验命令计划字段和参数文本。"""
        if not isinstance(self.execution, Il2CppExecutionPlan):
            raise TypeError("execution 必须是 Il2CppExecutionPlan")
        if not isinstance(self.arguments, tuple) or not self.arguments:
            raise ValueError("arguments 必须是非空 tuple")
        for argument in self.arguments:
            _validate_text(argument, "argument")
        if not isinstance(self.cache_hit, bool):
            raise TypeError("cache_hit 必须是 bool")
        _normalize_pairs(self.environment, "environment")


class Il2CppPlanner:
    """把 IL2CPP 请求和锁定工具链转换为确定性执行计划。"""

    @staticmethod
    def plan(
        request: Il2CppBuildRequest,
        workspace: Path,
        *,
        toolchain: Il2CppToolchain,
        cache_hit: bool = False,
    ) -> Il2CppCommandPlan:
        """生成固定 Unity IL2CPP 参数和内容寻址执行计划。

        参数：
            request: 已校验的 IL2CPP 构建请求。
            workspace: 绝对隔离工作区；只记录，不创建目录。
            toolchain: 显式锁定的命令模板、环境和工具链版本。
            cache_hit: 上层缓存验证结果；不在规划阶段读取缓存。

        返回：
            包含基础执行计划、固定参数、白名单环境和 cache-hit 标志的
            ``Il2CppCommandPlan``。

        异常：
            请求、工作区、工具链类型错误，Unity 版本不匹配、缺少 Unity 版本、未知
            命令模板或缓存命中标志非法时抛出 ``TypeError`` 或 ``ValueError``。

        约束与副作用：
            纯内存操作；不会执行 Unity、读取 OS 环境、创建 workspace 或发起远端请求。
        """
        if not isinstance(request, Il2CppBuildRequest):
            raise TypeError("request 必须是 Il2CppBuildRequest")
        if not isinstance(workspace, Path) or not workspace.is_absolute():
            raise ValueError("workspace 必须是绝对 Path")
        if not isinstance(toolchain, Il2CppToolchain):
            raise TypeError("toolchain 必须是 Il2CppToolchain")
        if toolchain.command_template_version not in _KNOWN_COMMAND_TEMPLATES:
            raise ValueError("未知 IL2CPP 命令模板")
        if toolchain.unity_version != request.unity_version:
            raise ValueError("Unity 版本与工具链不匹配")
        if not any(
            name.casefold() == "unity" and version == request.unity_version
            for name, version in toolchain.toolchain_versions
        ):
            raise ValueError("工具链版本集合缺少匹配的 Unity 版本")
        if not isinstance(cache_hit, bool):
            raise TypeError("cache_hit 必须是 bool")

        cache_key = Il2CppCacheKeyFactory.create(
            request,
            command_template_version=toolchain.command_template_version,
            environment=toolchain.environment,
            toolchain_versions=toolchain.toolchain_versions,
        )
        execution = Il2CppExecutionPlan(
            request=request,
            workspace=workspace,
            output_locator=f"blobs/{cache_key}",
            cache_key=cache_key,
        )
        arguments = (
            toolchain.unity_executable,
            "-batchmode",
            "-nographics",
            "-quit",
            "-executeMethod",
            "BuildIl2Cpp.Run",
            "-buildTarget",
            request.platform.value,
            "-architecture",
            request.architecture,
            "-inputLocator",
            request.input_snapshot.locator,
            "-output",
            "il2cpp-output.zip",
        )
        return Il2CppCommandPlan(execution, arguments, toolchain.environment, cache_hit)


__all__ = ["Il2CppCommandPlan", "Il2CppPlanner", "Il2CppToolchain"]
