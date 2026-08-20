"""application 运行前的只读身份、工具链和平台能力检查。

preflight 在任何工作区获取、Unity 执行、对象上传或版本入口 CAS 之前运行。它只
消费已经解析的 CLI 配置和显式注入的只读探针；``dry_run`` 只返回计划摘要，不调用
写端口。真实 SVN、Unity 和平台检查由外部适配器通过小型 Protocol 提供。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from application.model import ApplicationRequest
from cli.config import BuildConfig
from core.errors import ConfigurationError
from core.platforms import BuildPlatform

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PreflightError(ConfigurationError):
    """表示运行请求、工具链或平台能力未通过启动前检查。"""


class PreflightProbe(Protocol):
    """提供只读平台和工具链能力检查的端口。"""

    def supports_platform(self, platform: BuildPlatform) -> bool:
        """返回当前节点是否支持目标平台。"""
        ...

    def supports_toolchain(self, platform: BuildPlatform, unity_version: str) -> bool:
        """返回当前节点是否具有请求的 Unity 工具链。"""
        ...


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """不可变的 preflight 只读结果和后续写计划。

    参数：
        run_id: application 请求运行身份。
        platform: 通过检查的共享平台。
        source_revision: 已固定源码 revision。
        baseline_id、release_bundle_id、package_id: 可选内容寻址身份。
        dry_run: 是否禁止后续执行写操作。
        checks: 按固定顺序记录的成功检查标签。
        planned_writes: 非 dry-run 时允许后续事务使用的写端口标签。

    返回：
        无；结果冻结且适合写入运行记录。

    异常：
        字段形状错误时抛 ``PreflightError``。

    约束与副作用：
        本对象不代表任何写操作已经发生；``planned_writes`` 只是计划摘要。
    """

    run_id: str
    platform: BuildPlatform
    source_revision: str
    baseline_id: str | None
    release_bundle_id: str | None
    package_id: str | None
    dry_run: bool
    checks: tuple[str, ...]
    planned_writes: tuple[str, ...]

    def __post_init__(self) -> None:
        """校验 preflight 输出只包含成功检查和受控写标签。"""
        if not isinstance(self.run_id, str) or not self.run_id:
            raise PreflightError("run_id 必须是非空 str")
        if not isinstance(self.platform, BuildPlatform):
            raise PreflightError("platform 必须是 BuildPlatform")
        if not isinstance(self.source_revision, str) or not self.source_revision:
            raise PreflightError("source_revision 必须是非空 str")
        if not isinstance(self.dry_run, bool):
            raise PreflightError("dry_run 必须是 bool")
        if not isinstance(self.checks, tuple) or any(
            not isinstance(item, str) for item in self.checks
        ):
            raise PreflightError("checks 必须是 tuple[str, ...]")
        if not isinstance(self.planned_writes, tuple) or any(
            item not in {"workspace", "process", "object_store", "cas"}
            for item in self.planned_writes
        ):
            raise PreflightError("planned_writes 含未知写端口")
        if self.dry_run and self.planned_writes:
            raise PreflightError("dry_run 不得包含写计划")

    @property
    def ready(self) -> bool:
        """返回结果是否可交给后续 application 用例。"""
        return True


def _validate_optional_id(value: str | None, field_name: str) -> None:
    """校验可选身份为空或为小写 SHA256。"""
    if value is not None and (_SHA256.fullmatch(value) is None):
        raise PreflightError(f"{field_name} 必须是 64 位小写 SHA256 或 None")


class _AlwaysSupportedProbe:
    """没有注入节点探针时使用的纯内存开发替身。"""

    def supports_platform(self, platform: BuildPlatform) -> bool:
        """默认允许共享枚举中的平台，不探测本机。"""
        return isinstance(platform, BuildPlatform)

    def supports_toolchain(self, platform: BuildPlatform, unity_version: str) -> bool:
        """默认接受非空工具链版本，不访问 Unity。"""
        return isinstance(platform, BuildPlatform) and bool(unity_version)


class PreflightService:
    """执行 application 请求的只读 preflight。"""

    def __init__(self, probe: PreflightProbe | None = None) -> None:
        """保存可选只读能力探针；不创建任何外部适配器。"""
        self._probe: PreflightProbe = probe if probe is not None else _AlwaysSupportedProbe()

    def run(
        self,
        request: ApplicationRequest,
        config: BuildConfig,
        *,
        baseline_id: str | None = None,
        release_bundle_id: str | None = None,
        package_id: str | None = None,
    ) -> PreflightResult:
        """检查请求身份、内容寻址 ID、平台和 Unity 版本能力。

        参数：
            request: 已通过 application 模型校验的运行请求。
            config: 已通过 CLI 白名单校验的配置。
            baseline_id、release_bundle_id、package_id: 业务阶段可选身份。

        返回：
            所有检查通过的 ``PreflightResult``；dry-run 的写计划为空。

        异常：
            请求/配置类型、身份格式或只读能力不满足时抛 ``PreflightError``。

        约束与副作用：
            只调用探针的只读方法；不获取工作区、不执行进程、不 put 对象、不 CAS。
        """
        if not isinstance(request, ApplicationRequest):
            raise PreflightError("request 必须是 ApplicationRequest")
        if not isinstance(config, BuildConfig):
            raise PreflightError("config 必须是 BuildConfig")
        _validate_optional_id(baseline_id, "baseline_id")
        _validate_optional_id(release_bundle_id, "release_bundle_id")
        _validate_optional_id(package_id, "package_id")
        if not self._probe.supports_platform(request.platform):
            raise PreflightError(f"平台能力检查失败: {request.platform.value}")
        if not self._probe.supports_toolchain(request.platform, config.unity_version):
            raise PreflightError(f"工具链能力检查失败: {config.unity_version}")
        checks = ("fixed_revision", "identity_format", "platform", "toolchain")
        planned_writes = () if request.dry_run else ("workspace", "process", "object_store", "cas")
        return PreflightResult(
            request.run_id,
            request.platform,
            request.source_revision,
            baseline_id,
            release_bundle_id,
            package_id,
            request.dry_run,
            checks,
            planned_writes,
        )

    def check(
        self, request: ApplicationRequest, config: BuildConfig, **kwargs: str | None
    ) -> PreflightResult:
        """``run`` 的语义别名，便于 composition root 使用动词 check。"""
        return self.run(request, config, **kwargs)


__all__ = ["PreflightError", "PreflightProbe", "PreflightResult", "PreflightService"]
