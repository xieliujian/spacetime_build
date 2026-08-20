"""内容寻址 Blob 提交器的边界测试。"""

import hashlib
from pathlib import Path

import pytest

from core.artifacts import BlobRef
from core.errors import ArtifactValidationError
from ports.storage import PutObjectRequest, StoredObject
from resource.blob_committer import BlobCommitter


class _Store:
    """记录写入请求的最小对象存储替身。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.requests: list[PutObjectRequest] = []

    def put(self, request: PutObjectRequest) -> StoredObject:
        """保存请求并返回同内容引用。"""
        self.requests.append(request)
        return StoredObject(request.key, request.sha256, len(request.content))


def test_blob_committer_returns_persistent_cas_reference(tmp_path: Path) -> None:
    """验证文件按 SHA256/大小提交，locator 不指向工作区。"""
    source = tmp_path / "input.bin"
    source.write_bytes(b"payload")
    store = _Store()
    reference = BlobCommitter(store).commit(source, allowed_root=tmp_path)
    digest = hashlib.sha256(b"payload").hexdigest()
    assert reference == BlobRef(f"blobs/{digest}", digest, 7)
    assert store.requests[0].key == f"blobs/{digest}"


def test_blob_committer_rejects_missing_symlink_escape_and_directory(tmp_path: Path) -> None:
    """验证缺失、目录、符号链接和根目录外文件均被拒绝。"""
    committer = BlobCommitter(_Store())
    with pytest.raises(FileNotFoundError):
        committer.commit(tmp_path / "missing.bin", allowed_root=tmp_path)
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ValueError):
        committer.commit(directory, allowed_root=tmp_path)
    outside = tmp_path.parent / "outside-resource.bin"
    outside.write_bytes(b"outside")
    try:
        with pytest.raises(ValueError):
            committer.commit(outside, allowed_root=tmp_path)
    finally:
        outside.unlink()


def test_blob_committer_rejects_file_changed_during_read(tmp_path: Path) -> None:
    """验证读取前后 stat 不一致时不登记不稳定内容。"""
    source = tmp_path / "input.bin"
    source.write_bytes(b"payload")
    committer = BlobCommitter(_Store())
    original = committer._read_stable_bytes

    def changed(path: Path) -> tuple[bytes, int]:
        """伪造读取后文件变化。"""
        content, size = original(path)
        path.write_bytes(content + b"changed")
        return content, size

    committer._read_stable_bytes = changed  # type: ignore[method-assign]
    with pytest.raises(ArtifactValidationError):
        committer.commit(source, allowed_root=tmp_path)
