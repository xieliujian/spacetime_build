"""IL2CPP 内容寻址缓存键测试。"""

from services.il2cpp.cache_key import Il2CppCacheKeyFactory
from services.il2cpp.model import Il2CppBuildRequest, Il2CppExecutionMode
from core.artifacts import BlobRef
from core.platforms import BuildPlatform


def _request(
    *,
    request_id: str = "request-a",
    snapshot: str = "a",
    protection_policy: str | None = None,
) -> Il2CppBuildRequest:
    """构造可比较的缓存键请求。"""
    digest = snapshot * 64
    return Il2CppBuildRequest(
        request_id,
        BuildPlatform.ANDROID,
        "arm64-v8a",
        BlobRef(f"blobs/{digest}", digest, 10),
        "2022.3.62f2",
        "toolchain-v1",
        Il2CppExecutionMode.LOCAL,
        protection_policy,
    )


def test_il2cpp_cache_key_excludes_request_id_and_normalizes_unordered_inputs() -> None:
    """验证 request ID 不影响缓存身份，环境和工具链排列不影响结果。"""
    first = Il2CppCacheKeyFactory.create(
        _request(request_id="one"),
        command_template_version="v1",
        environment=(("B", "2"), ("A", "1")),
        toolchain_versions=(("ndk", "25"), ("unity", "2022")),
    )
    second = Il2CppCacheKeyFactory.create(
        _request(request_id="two"),
        command_template_version="v1",
        environment=(("A", "1"), ("B", "2")),
        toolchain_versions=(("unity", "2022"), ("ndk", "25")),
    )

    assert first == second
    assert len(first) == 64


def test_il2cpp_cache_key_changes_for_all_build_identity_inputs() -> None:
    """验证输入 Blob、保护策略、命令模板、环境和工具链任一变化都会改变键。"""
    baseline = Il2CppCacheKeyFactory.create(
        _request(),
        command_template_version="v1",
        environment=(("A", "1"),),
        toolchain_versions=(("ndk", "25"),),
    )
    variants = (
        Il2CppCacheKeyFactory.create(
            _request(snapshot="b"),
            command_template_version="v1",
            environment=(("A", "1"),),
            toolchain_versions=(("ndk", "25"),),
        ),
        Il2CppCacheKeyFactory.create(
            _request(protection_policy="protected"),
            command_template_version="v1",
            environment=(("A", "1"),),
            toolchain_versions=(("ndk", "25"),),
        ),
        Il2CppCacheKeyFactory.create(
            _request(),
            command_template_version="v2",
            environment=(("A", "1"),),
            toolchain_versions=(("ndk", "25"),),
        ),
        Il2CppCacheKeyFactory.create(
            _request(),
            command_template_version="v1",
            environment=(("A", "2"),),
            toolchain_versions=(("ndk", "25"),),
        ),
        Il2CppCacheKeyFactory.create(
            _request(),
            command_template_version="v1",
            environment=(("A", "1"),),
            toolchain_versions=(("ndk", "26"),),
        ),
    )

    assert all(value != baseline for value in variants)
