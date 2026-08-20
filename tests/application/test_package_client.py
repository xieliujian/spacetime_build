"""验证客户端打包用例经过 Release gate 并按平台注册表分派。"""

from application.package_client import PackageClientUseCase
from core.platforms import BuildPlatform
from package.model import PackageRequest


class _Gate:
    """记录并接受已验证 ReleaseBundle 的测试 gate。"""

    def require_verified(self, release_bundle_id: str, verification: object) -> object:
        """返回验证凭证。"""
        return verification


class _Builder:
    """返回结构化假包体结果的 builder。"""

    def __init__(self) -> None:
        """创建记录构建调用次数的 builder。"""
        self.calls = 0

    def build(self, request: PackageRequest, verification: object) -> object:
        """记录一次平台构建。"""
        self.calls += 1
        return {"package": request.request_id}


def test_package_use_case_dispatches_by_shared_platform_and_keeps_release_separate() -> None:
    """Given Android builder，When 打包，Then 只调用对应平台且返回成功。"""
    request = PackageRequest(
        BuildPlatform.ANDROID,
        "123",
        "a" * 64,
        "2022.3.62f2",
        "com.example.game",
        "1.0.0",
        1,
        "release",
    )
    builder = _Builder()
    result = PackageClientUseCase(
        _Gate(),
        {BuildPlatform.ANDROID: builder},
    ).run(request, object())

    assert result.state.value == "succeeded"
    assert builder.calls == 1
