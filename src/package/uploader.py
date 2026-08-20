"""PackageManifest 之后的客户端包体内容寻址上传。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from core.errors import PublishError
from package.manifest import PackageManifest
from ports.storage import ObjectStore, PutObjectRequest


@dataclass(frozen=True, slots=True)
class PackageUploadReport:
    """包体对象上传摘要。"""

    uploaded_keys: tuple[str, ...]


class PackageUploader:
    """按 PackageManifest 身份幂等上传包体对象。"""

    def __init__(self, object_store: ObjectStore) -> None:
        """绑定对象存储端口。"""
        if not isinstance(object_store, ObjectStore):
            raise TypeError("object_store 必须是 ObjectStore")
        self._object_store = object_store

    def upload(
        self, manifest: PackageManifest, contents: Mapping[str, bytes]
    ) -> PackageUploadReport:
        """校验全部包体内容并按确定性对象键上传。

        参数：
            manifest: 已由工厂创建的 PackageManifest。
            contents: 逻辑路径到完整文件 bytes 的映射。

        返回：
            按 manifest 逻辑路径顺序上传成功的对象键摘要。

        异常：
            manifest、内容缺失、SHA256/大小不一致或对象回执异常时抛出 ``TypeError`` /
            ``ValueError`` / ``PublishError``。

        约束与副作用：
            只上传包体对象，不修改 manifest、不激活资源版本、不执行商店发布。
        """
        if not isinstance(manifest, PackageManifest):
            raise TypeError("manifest 必须是 PackageManifest")
        if not isinstance(contents, Mapping):
            raise TypeError("contents 必须是 Mapping")
        requests: list[PutObjectRequest] = []
        for logical_path, blob, _kind in sorted(
            manifest.payload.artifacts, key=lambda item: item[0].encode("utf-8")
        ):
            content = contents.get(logical_path)
            if not isinstance(content, bytes):
                raise ValueError(f"缺少包体产物内容: {logical_path}")
            if len(content) != blob.size or hashlib.sha256(content).hexdigest() != blob.sha256:
                raise ValueError(f"包体产物内容身份不匹配: {logical_path}")
            key = f"packages/{manifest.manifest_id}/{logical_path}"
            requests.append(PutObjectRequest(key, content, blob.sha256))
        uploaded: list[str] = []
        for request in requests:
            stored = self._object_store.put(request)
            if (
                stored.key != request.key
                or stored.sha256 != request.sha256
                or stored.size != len(request.content)
            ):
                raise PublishError(f"包体对象上传回执不一致: {request.key}")
            uploaded.append(request.key)
        return PackageUploadReport(tuple(uploaded))
