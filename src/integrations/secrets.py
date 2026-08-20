"""基于环境变量的短期凭据提供器。

该实现用于本机和受控 CI 节点：``secret://env/NAME`` 只在 acquire 时读取一次，返回的
租约关闭后拒绝继续解析。租约和 provider 的 repr 均不包含秘密值，真实密钥服务可以在
不改变端口契约的情况下替换本模块。
"""

from __future__ import annotations

import os
import threading

from ports.secrets import SecretLease, SecretLeaseRequest, SecretProvider


class _EnvironmentSecretLease(SecretLease):
    """保存一组短期环境凭据的不可打印租约。"""

    def __init__(self, values: dict[str, str]) -> None:
        """保存秘密副本并初始化关闭状态。"""
        self._values = values
        self._closed = False
        self._lock = threading.Lock()

    def resolve(self, binding_id: str) -> str:
        """在租约有效期间解析 binding ID。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("秘密租约已关闭")
            if binding_id not in self._values:
                raise KeyError(f"未知秘密 binding: {binding_id}")
            return self._values[binding_id]

    def close(self) -> None:
        """清空秘密副本并使租约进入终态。"""
        with self._lock:
            self._closed = True
            for key in tuple(self._values):
                self._values[key] = ""
            self._values.clear()

    def __repr__(self) -> str:
        """返回不含 binding 值的固定表示。"""
        return "SecretLease(<redacted>)"


class EnvironmentSecretProvider(SecretProvider):
    """从受控环境变量读取短期秘密。"""

    def acquire(self, request: SecretLeaseRequest) -> SecretLease:
        """解析环境引用并为全部允许 binding 建立租约。"""
        if not isinstance(request, SecretLeaseRequest):
            raise TypeError("request 必须是 SecretLeaseRequest")
        locator = request.reference.reveal_locator()
        prefix = "secret://env/"
        if not locator.startswith(prefix):
            raise ValueError("EnvironmentSecretProvider 只接受 secret://env/ 引用")
        name = locator[len(prefix) :]
        if not name or any(character in name for character in "\r\n="):
            raise ValueError("环境变量引用无效")
        value = os.environ.get(name)
        if value is None or value == "":
            raise KeyError(f"环境变量不存在或为空: {name}")
        return _EnvironmentSecretLease({binding_id: value for binding_id in request.binding_ids})


class ControlledFileSecretProvider(SecretProvider):
    """从配置根目录内的受控文件读取短期秘密。"""

    def __init__(self, root: str) -> None:
        """保存绝对秘密文件根目录。"""
        from pathlib import Path

        path = Path(root).resolve()
        if not path.is_absolute():
            raise ValueError("root 必须是绝对路径")
        self._root = path

    def acquire(self, request: SecretLeaseRequest) -> SecretLease:
        """读取 secret://file/ 相对路径并建立短期租约。"""
        if not isinstance(request, SecretLeaseRequest):
            raise TypeError("request 必须是 SecretLeaseRequest")
        locator = request.reference.reveal_locator()
        prefix = "secret://file/"
        if not locator.startswith(prefix):
            raise ValueError("ControlledFileSecretProvider 只接受 secret://file/ 引用")
        relative = locator[len(prefix) :]
        path = (self._root / relative).resolve()
        if path.parent != self._root and self._root not in path.parents:
            raise ValueError("秘密文件路径越出受控根目录")
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("秘密文件不存在或不是普通文件")
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
        if not value:
            raise ValueError("秘密文件为空")
        return _EnvironmentSecretLease({binding_id: value for binding_id in request.binding_ids})
