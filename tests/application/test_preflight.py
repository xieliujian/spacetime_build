"""验证 application preflight 的身份、能力和 dry-run 只读边界。"""

import pytest

from application.model import ApplicationRequest
from application.preflight import PreflightError, PreflightService
from cli.config import BuildConfig
from core.platforms import BuildPlatform


class _Probe:
    """记录 preflight 使用的只读能力探针调用。"""

    def __init__(self, supported: bool = True) -> None:
        """创建记录调用的能力探针替身。"""
        self.supported = supported
        self.calls: list[tuple[str, object]] = []

    def supports_platform(self, platform: BuildPlatform) -> bool:
        """返回平台能力并记录调用。"""
        self.calls.append(("platform", platform))
        return self.supported

    def supports_toolchain(self, platform: BuildPlatform, unity_version: str) -> bool:
        """返回工具链能力并记录调用。"""
        self.calls.append(("toolchain", (platform, unity_version)))
        return self.supported


def _config() -> BuildConfig:
    """创建 preflight 使用的完整配置。"""
    return BuildConfig(
        profile="release",
        resources=("config", "scene"),
        max_workers=2,
        unity_version="2022.3.62f2",
        source_provider="svn",
        source_revision="123",
        source_credential=None,
        publish_target="staging",
        publish_subpackage=True,
        publish_redirect=False,
    )


def test_preflight_validates_ids_and_never_writes_in_dry_run() -> None:
    """Given 固定请求和能力探针，When dry-run，Then 只读通过且没有写计划。"""
    probe = _Probe()
    request = ApplicationRequest("run-1", "release", "123", BuildPlatform.ANDROID, True)
    digest = "a" * 64
    result = PreflightService(probe).run(
        request,
        _config(),
        baseline_id=digest,
        release_bundle_id=digest,
        package_id=digest,
    )

    assert result.ready is True
    assert result.dry_run is True
    assert result.planned_writes == ()
    assert len(probe.calls) == 2


@pytest.mark.parametrize("field", ("baseline_id", "release_bundle_id", "package_id"))
def test_preflight_rejects_non_sha_ids(field: str) -> None:
    """Given 非内容寻址 ID，When preflight，Then 在任何执行前失败。"""
    request = ApplicationRequest("run-1", "release", "123", BuildPlatform.WINDOWS, False)
    kwargs = {field: "not-an-id"}
    with pytest.raises(PreflightError):
        PreflightService().run(request, _config(), **kwargs)


def test_preflight_rejects_missing_platform_capability() -> None:
    """Given 能力探针拒绝平台，When preflight，Then 返回配置/工具错误而非计划。"""
    request = ApplicationRequest("run-1", "release", "123", BuildPlatform.IOS, False)
    with pytest.raises(PreflightError, match="平台"):
        PreflightService(_Probe(False)).run(request, _config())
