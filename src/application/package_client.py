"""客户端包体 application 用例编排。

包体用例先要求 ReleaseBundle 已被远端验证，再通过共享 ``BuildPlatform`` 注册表
分派 Android/iOS/Windows 组件。平台 builder 只负责自身结构化结果；本模块不把
包体失败写入资源版本入口，也不复制 Unity、签名或安装器规则。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from application.model import RunState
from core.errors import BuildError
from core.platforms import BuildPlatform
from package.model import PackageRequest


class PackageGate(Protocol):
    """Release gate 的最小协议。"""

    def require_verified(self, release_bundle_id: str, verification: object) -> object:
        """返回与请求 Bundle ID 匹配的验证凭证。"""
        ...


class PackageBuilder(Protocol):
    """单个平台包体 builder 的最小协议。"""

    def build(self, request: PackageRequest, verification: object) -> object:
        """生成已验证或待验证的 PackageManifest 结果。"""
        ...


class PackageUploader(Protocol):
    """可选包体上传器的最小协议。"""

    def upload(self, manifest: object, contents: Mapping[str, bytes]) -> object:
        """上传已生成 PackageManifest 对应的内容。"""
        ...


@dataclass(frozen=True, slots=True)
class PackageClientResult:
    """客户端打包结果与可选上传摘要。"""

    state: RunState
    package: object | None
    upload_report: object | None
    error: str | None


class PackageClientUseCase:
    """组合 Release gate、平台注册表和可选包体上传。"""

    def __init__(
        self,
        gate: PackageGate,
        builders: Mapping[BuildPlatform, PackageBuilder],
        uploader: PackageUploader | None = None,
    ) -> None:
        """保存 gate、平台 builder 和可选 uploader；不构造平台适配器。"""
        self._gate = gate
        self._builders = dict(builders)
        self._uploader = uploader

    def run(
        self,
        request: PackageRequest,
        verification: object,
        *,
        contents: Mapping[str, bytes] | None = None,
    ) -> PackageClientResult:
        """先验证 ReleaseBundle，再执行选定平台并可选上传。

        参数：
            request: 含独立 package request ID 的不可变请求。
            verification: 发布阶段的验证凭证。
            contents: uploader 需要的逻辑路径到 bytes 映射；默认不上传。

        返回：
            成功、失败或平台缺失的不可变结果；资源 ReleaseBundle 不会被修改。

        异常：
            业务 gate、builder 或 uploader 异常被转换为 FAILED 结果。

        约束与副作用：
            gate 失败时不调用 builder；上传只在 PackageManifest 结果和内容明确提供时发生。
        """
        if not isinstance(request, PackageRequest):
            raise BuildError("request 必须是 PackageRequest")
        try:
            verified = self._gate.require_verified(request.release_bundle_id, verification)
            builder = self._builders.get(request.platform)
            if builder is None:
                raise BuildError(f"平台缺少包体 builder: {request.platform.value}")
            package = builder.build(request, verified)
            upload_report: object | None = None
            if self._uploader is not None and contents is not None:
                upload_report = self._uploader.upload(package, contents)
            return PackageClientResult(RunState.SUCCEEDED, package, upload_report, None)
        except Exception as exc:
            return PackageClientResult(RunState.FAILED, None, None, str(exc))

    def build(self, *args: object, **kwargs: object) -> PackageClientResult:
        """``run`` 的命令处理器友好别名。"""
        return self.run(*args, **kwargs)  # type: ignore[arg-type]


__all__ = [
    "PackageBuilder",
    "PackageClientResult",
    "PackageClientUseCase",
    "PackageGate",
    "PackageUploader",
]
