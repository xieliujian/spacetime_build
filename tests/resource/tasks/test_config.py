"""config 资源任务的类型化转换契约测试。

本模块验证配置转换的规划、执行和 CAS 边界；测试替身只在内存中生成字节，
不启动外部工具，也不模拟真实项目的 Schema 编译器。
"""

from pathlib import Path

import pytest

from core.platforms import BuildPlatform
from core.tasks import ArtifactCollection, BuildContext
from ports.storage import PutObjectRequest, StoredObject
from release.entries import ResourceVariant
from resource.blob_committer import BlobCommitter
from resource.model import ResourceBuildInput
from resource.task_service import ResourceBuildService
from resource.tasks.config import (
    ConfigResourceTask,
    ConfigTransformOutput,
    ConfigTransformRequest,
)


class _Store:
    """记录配置转换产物的内存对象存储替身。"""

    def __init__(self) -> None:
        """初始化写入记录。"""
        self.requests: list[PutObjectRequest] = []

    def put(self, request: PutObjectRequest) -> StoredObject:
        """保存请求并返回与请求一致的对象引用。"""
        self.requests.append(request)
        return StoredObject(request.key, request.sha256, len(request.content))


class _Transformer:
    """返回固定配置代码、BIN 和可选 TXT 的测试转换器。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.discover_requests: list[ConfigTransformRequest] = []
        self.transform_requests: list[ConfigTransformRequest] = []

    def discover_outputs(self, request: ConfigTransformRequest) -> tuple[str, ...]:
        """返回由同一 Schema 快照生成的精确配置输出。"""
        self.discover_requests.append(request)
        outputs = (
            "config/generated/ConfigReader.cs",
            "config/generated/config.bin",
        )
        if request.emit_debug_text:
            outputs += ("config/generated/config.txt",)
        return outputs

    def transform(self, request: ConfigTransformRequest) -> tuple[ConfigTransformOutput, ...]:
        """生成与规划顺序一致的固定配置字节。"""
        self.transform_requests.append(request)
        outputs = (
            ConfigTransformOutput("config/generated/ConfigReader.cs", b"reader"),
            ConfigTransformOutput("config/generated/config.bin", b"binary"),
        )
        if request.emit_debug_text:
            outputs += (ConfigTransformOutput("config/generated/config.txt", b"debug"),)
        return outputs


def _input() -> ResourceBuildInput:
    """构造固定 Windows 主资源输入。"""
    return ResourceBuildInput(
        "source-1", "schema-1", BuildPlatform.WINDOWS, ResourceVariant.MAIN, "rules-1", None
    )


def _context() -> BuildContext:
    """构造确定性资源构建上下文。"""
    return BuildContext("a" * 64, "r100", "b" * 64, None, 1)


def test_config_transformer_is_planned_and_committed_as_exact_outputs(tmp_path: Path) -> None:
    """验证转换器收到固定 Schema 身份，规划和实际输出严格相等。"""
    source = tmp_path / "config"
    source.mkdir()
    transformer = _Transformer()
    store = _Store()
    task = ConfigResourceTask(
        _input(),
        source,
        BlobCommitter(store),
        transformer=transformer,
        emit_debug_text=True,
    )

    plan = task.plan(_context())
    assert plan.spec.outputs == frozenset(
        {
            "config/generated/ConfigReader.cs",
            "config/generated/config.bin",
            "config/generated/config.txt",
        }
    )
    assert transformer.transform_requests == []
    assert transformer.discover_requests[0].schema_snapshot_id == "schema-1"

    newer_task = ConfigResourceTask(
        _input(),
        source,
        BlobCommitter(_Store()),
        transformer=transformer,
        emit_debug_text=True,
        transformer_version="2",
    )
    assert newer_task.plan(_context()).config_digest != plan.config_digest

    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))
    assert tuple(item.logical_path for item in result.result.outputs) == (
        "config/generated/ConfigReader.cs",
        "config/generated/config.bin",
        "config/generated/config.txt",
    )
    assert [request.content for request in store.requests] == [b"reader", b"binary", b"debug"]
    assert len(transformer.transform_requests) == 1


def test_config_transformer_rejects_output_drift_before_cas_write(tmp_path: Path) -> None:
    """验证转换器少产出或改变路径时不会登记部分 Blob。"""

    class _DriftingTransformer(_Transformer):
        """返回与规划集合不一致的配置转换结果。"""

        def transform(self, request: ConfigTransformRequest) -> tuple[ConfigTransformOutput, ...]:
            """故意少返回一个规划产物。"""
            del request
            return (ConfigTransformOutput("config/generated/config.bin", b"binary"),)

    source = tmp_path / "config"
    source.mkdir()
    store = _Store()
    task = ConfigResourceTask(
        _input(),
        source,
        BlobCommitter(store),
        transformer=_DriftingTransformer(),
    )

    with pytest.raises(ValueError, match="规划输出"):
        ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))
    assert store.requests == []


@pytest.mark.parametrize(
    "path",
    [
        "config/../escape.bin",
        "/absolute.bin",
    ],
)
def test_config_transform_output_rejects_unsafe_paths(path: str) -> None:
    """验证配置转换输出不能越出客户端逻辑路径边界。"""
    with pytest.raises(ValueError):
        ConfigTransformOutput(path, b"x")


def test_config_without_transformer_keeps_file_task_compatibility(tmp_path: Path) -> None:
    """验证未注入转换器时仍按原有输入目录提交文件。"""
    source = tmp_path / "config"
    source.mkdir()
    (source / "legacy.bin").write_bytes(b"legacy")
    task = ConfigResourceTask(_input(), source, BlobCommitter(_Store()))

    result = ResourceBuildService().build(task, _context(), ArtifactCollection.from_artifacts(()))

    assert tuple(item.logical_path for item in result.result.outputs) == ("config/legacy.bin",)
