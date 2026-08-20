"""Android Gradle 结构化配置计划。"""

from __future__ import annotations

from dataclasses import dataclass

from package.platforms.android.model import (
    AndroidAbi,
    AndroidBuildType,
    AndroidOutputKind,
    AndroidPackageOptions,
)


@dataclass(frozen=True, slots=True)
class GradleConfigurationPlan:
    """可重复应用的 Android Gradle 配置数据。"""

    application_id: str
    version_code: int
    build_type: AndroidBuildType
    output_kind: AndroidOutputKind
    abis: tuple[AndroidAbi, ...]
    repositories: tuple[str, ...]
    offline_lock: bool


class GradleConfigurationPlanner:
    """从 Android 选项生成不含脚本代码的配置计划。"""

    @staticmethod
    def plan(
        options: AndroidPackageOptions,
        *,
        repositories: tuple[str, ...] = (),
        offline_lock: bool = True,
    ) -> GradleConfigurationPlan:
        """冻结应用 ID、版本、ABI、仓库白名单和离线锁定策略。

        参数：
            options: 已校验 Android 包体选项。
            repositories: 允许的 HTTPS 仓库 URL，不接受本地或脚本协议。
            offline_lock: 是否要求使用锁定的离线依赖集合。

        返回：
            可确定性比较和后续 applier 消费的 ``GradleConfigurationPlan``。

        异常：
            输入类型、仓库协议、重复仓库或布尔类型非法时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            纯内存规划；不拼接 Gradle 代码、不读写工程、不访问网络。
        """
        if not isinstance(options, AndroidPackageOptions):
            raise TypeError("options 必须是 AndroidPackageOptions")
        if not isinstance(repositories, tuple):
            raise TypeError("repositories 必须是 tuple")
        if not isinstance(offline_lock, bool):
            raise TypeError("offline_lock 必须是 bool")
        normalized = tuple(sorted(set(repositories), key=lambda value: value.encode("utf-8")))
        if len(normalized) != len(repositories):
            raise ValueError("repositories 不得重复")
        for repository in normalized:
            if (
                not isinstance(repository, str)
                or not repository.startswith("https://")
                or any(char in repository for char in "\r\n")
            ):
                raise ValueError("repositories 只能是无换行 HTTPS URL")
        return GradleConfigurationPlan(
            options.application_id,
            options.version_code,
            options.build_type,
            options.output_kind,
            options.abis,
            normalized,
            offline_lock,
        )
