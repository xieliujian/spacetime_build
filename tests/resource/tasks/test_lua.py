"""Lua 资源任务的模式、秘密引用和输出契约测试。

本模块使用内存转换器验证源码、字节码和加密模式的任务边界；不执行真实 Lua
编译器或加密工具，也不把明文密钥放入任务请求。
"""

from pathlib import Path

import pytest

from configuration.model import SecretRef
from core.platforms import BuildPlatform
from core.tasks import ArtifactCollection, BuildContext
from ports.storage import PutObjectRequest, StoredObject
from release.entries import ResourceVariant
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput
from resource.task_service import ResourceBuildService
from resource.tasks.lua import (
    LuaBuildMode,
    LuaResourceTask,
    LuaTransformOutput,
    LuaTransformRequest,
)


class _Store:
    """记录 Lua 产物的内存对象存储替身。"""

    def __init__(self) -> None:
        """初始化写入记录。"""
        self.requests: list[PutObjectRequest] = []

    def put(self, request: PutObjectRequest) -> StoredObject:
        """保存请求并返回与请求一致的对象引用。"""
        self.requests.append(request)
        return StoredObject(request.key, request.sha256, len(request.content))


class _Transformer:
    """返回固定 Lua 输出并记录类型化请求。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.discover_requests: list[LuaTransformRequest] = []
        self.transform_requests: list[LuaTransformRequest] = []

    def discover_outputs(self, request: LuaTransformRequest) -> tuple[str, ...]:
        """返回 Lua 任务唯一的精确脚本输出。"""
        self.discover_requests.append(request)
        return ("script/hotfix.lua", "script/main.lua")

    def transform(self, request: LuaTransformRequest) -> tuple[LuaTransformOutput, ...]:
        """返回按规划顺序编码的 Lua 输出字节。"""
        self.transform_requests.append(request)
        suffix = request.mode.value.encode("ascii")
        return (
            LuaTransformOutput("script/hotfix.lua", b"hotfix:" + suffix),
            LuaTransformOutput("script/main.lua", b"main:" + suffix),
        )


def _input() -> ResourceBuildInput:
    """构造固定 Windows 主资源输入。"""
    return ResourceBuildInput(
        "source-1", "lua-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-1", None
    )


def _context() -> BuildContext:
    """构造确定性资源构建上下文。"""
    return BuildContext("a" * 64, "r100", "b" * 64, None, 1)


def test_lua_transformer_binds_mode_hotfix_and_commits_outputs(tmp_path: Path) -> None:
    """验证 bytecode 请求携带编译版本、hotfix 入口且不暴露秘密内容。"""
    source = tmp_path / "lua"
    source.mkdir()
    transformer = _Transformer()
    store = _Store()
    task = LuaResourceTask(
        _input(),
        source,
        BlobCommitter(store),
        transformer=transformer,
        mode=LuaBuildMode.BYTECODE,
        hotfix_entry="hotfix.lua",
        compiler_version="lua-5.4.6",
        transformer_version="2",
    )

    plan = task.plan(_context())
    assert plan.spec.outputs == frozenset({"script/hotfix.lua", "script/main.lua"})
    assert transformer.transform_requests == []
    request = transformer.discover_requests[0]
    assert request.mode is LuaBuildMode.BYTECODE
    assert request.hotfix_entry == "hotfix.lua"
    assert request.compiler_version == "lua-5.4.6"
    assert "secret://" not in repr(request)

    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))

    assert tuple(item.logical_path for item in result.result.outputs) == (
        "script/hotfix.lua",
        "script/main.lua",
    )
    assert [request.content for request in store.requests] == [
        b"hotfix:bytecode",
        b"main:bytecode",
    ]


def test_lua_encrypted_mode_requires_typed_strategy_and_key_reference(tmp_path: Path) -> None:
    """验证 encrypted 模式要求策略版本和 SecretRef，且请求表示脱敏。"""
    source = tmp_path / "lua"
    source.mkdir()
    key_ref = SecretRef("secret://lua/release-key")
    request = LuaTransformRequest(
        source,
        "script",
        LuaBuildMode.ENCRYPTED,
        "hotfix.lua",
        "lua-5.4.6",
        "cipher-v3",
        key_ref,
    )

    assert request.encryption_strategy_version == "cipher-v3"
    assert request.encryption_key_ref is key_ref
    assert "secret://lua/release-key" not in repr(request)

    with pytest.raises(ValueError, match="encryption_strategy_version"):
        LuaTransformRequest(
            source,
            "script",
            LuaBuildMode.ENCRYPTED,
            None,
            "lua-5.4.6",
            None,
            key_ref,
        )
    with pytest.raises(ValueError, match="encryption_key_ref"):
        LuaTransformRequest(
            source,
            "script",
            LuaBuildMode.ENCRYPTED,
            None,
            "lua-5.4.6",
            "cipher-v3",
            None,
        )


def test_lua_transformer_rejects_output_drift_before_cas_write(tmp_path: Path) -> None:
    """验证转换器少产出时不会登记任何 Lua Blob。"""

    class _DriftingTransformer(_Transformer):
        """返回与规划集合不一致的 Lua 结果。"""

        def transform(self, request: LuaTransformRequest) -> tuple[LuaTransformOutput, ...]:
            """故意省略一个规划产物。"""
            del request
            return (LuaTransformOutput("script/main.lua", b"main"),)

    source = tmp_path / "lua"
    source.mkdir()
    store = _Store()
    task = LuaResourceTask(
        _input(), source, BlobCommitter(store), transformer=_DriftingTransformer()
    )

    with pytest.raises(ValueError, match="规划输出"):
        ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))
    assert store.requests == []


def test_lua_without_transformer_keeps_source_file_compatibility(tmp_path: Path) -> None:
    """验证 source 默认模式仍按原有目录文件提交脚本。"""
    source = tmp_path / "lua"
    source.mkdir()
    (source / "main.lua").write_bytes(b"return 1")
    task = LuaResourceTask(_input(), source, BlobCommitter(_Store()))

    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))

    assert tuple(item.logical_path for item in result.result.outputs) == ("script/main.lua",)


def test_lua_compatibility_mode_rejects_ignored_transform_options(tmp_path: Path) -> None:
    """验证无转换器时不会静默忽略 hotfix 和排除配置。"""
    source = tmp_path / "lua"
    source.mkdir()
    with pytest.raises(ValueError, match="hotfix_entry"):
        LuaResourceTask(
            _input(),
            source,
            BlobCommitter(_Store()),
            hotfix_entry="hotfix.lua",
        )
    with pytest.raises(ValueError, match="exclude_patterns"):
        LuaResourceTask(
            _input(),
            source,
            BlobCommitter(_Store()),
            exclude_patterns=("debug/",),
        )
