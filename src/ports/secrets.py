"""外部系统凭据的不可泄漏端口契约。

本模块只保存 ``SecretRef``、用途和不透明 binding 元数据，不解析或缓存秘密明文。
真正的凭据提供器由 ``integrations`` 实现，进程和 HTTP 适配器只在调用边界短暂使用
租约，并负责在成功、失败、超时和取消路径关闭租约。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from configuration.model import SecretRef


def _require_text(value: object, field_name: str) -> str:
    """校验不含控制字符的非空文本。"""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str")
    if not value or any(ord(character) < 0x20 for character in value):
        raise ValueError(f"{field_name} 必须是非空且不含控制字符的文本")
    return value


@dataclass(frozen=True, slots=True)
class SecretLeaseRequest:
    """描述一次短期凭据租约申请。

    参数：reference 为脱敏 ``SecretRef``；purpose 为审计用途；binding_ids 为允许
    解析的不透明标识；ttl_seconds 为有效秒数。对象不会读取秘密或访问外部系统。
    """

    reference: SecretRef
    purpose: str
    binding_ids: tuple[str, ...]
    ttl_seconds: float = 300.0

    def __post_init__(self) -> None:
        """校验引用、用途、binding 集合和租约时长。"""
        if not isinstance(self.reference, SecretRef):
            raise TypeError("reference 必须是 SecretRef")
        _require_text(self.purpose, "purpose")
        if not isinstance(self.binding_ids, tuple):
            raise TypeError("binding_ids 必须是 tuple[str, ...]")
        seen: set[str] = set()
        for binding_id in self.binding_ids:
            binding_id = _require_text(binding_id, "binding_id")
            if binding_id in seen:
                raise ValueError(f"binding_ids 存在重复项: {binding_id}")
            seen.add(binding_id)
        if isinstance(self.ttl_seconds, bool) or not isinstance(self.ttl_seconds, (int, float)):
            raise TypeError("ttl_seconds 必须是数值")
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于 0")


class SecretLease(Protocol):
    """短期秘密租约协议，解析结果只能在外部调用边界即时使用。"""

    def resolve(self, binding_id: str) -> str:
        """解析一个租约内的 binding ID。"""
        ...

    def close(self) -> None:
        """关闭租约并销毁短期材料。"""
        ...


class SecretProvider(Protocol):
    """凭据提供器的稳定端口。"""

    def acquire(self, request: SecretLeaseRequest) -> SecretLease:
        """获取短期租约。"""
        ...
