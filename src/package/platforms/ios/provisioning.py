"""解析 iOS provisioning profile 的公开元数据。

本模块只接受已经由调用方从 CMS 容器解码得到的 plist bytes，不执行 ``security``、
``plutil`` 或其他 macOS 工具。这样可以把外部工具的进程边界留给集成适配器，并让
profile 字段解析、校验、冻结和脱敏在 Windows、Linux 和 macOS 上都能使用同一组测试。
解析结果只保存 ``SecretRef``，不会读取 locator 指向的 profile 原文或秘密材料。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import plistlib
import re
from typing import cast

from configuration.model import SecretRef

_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_REDACTED_SECRET_REF = "SecretRef(<redacted>)"


@dataclass(frozen=True, slots=True)
class ProfileMetadata:
    """描述 provisioning profile 中与签名匹配有关的公开元数据。

    参数：
        profile_ref: profile 原文或秘密材料的脱敏 ``SecretRef`` locator。
        uuid: profile 的规范 UUID 文本，不保存 profile 原文。
        team: Apple Developer Team ID。
        application_identifier: entitlements 中的 ``application-identifier``。
        entitlements: 按 UTF-8 字节序排列且递归冻结的 entitlement 键值。
        expiration_date: profile 的过期时间，始终规范化为带 UTC 时区的 ``datetime``。

    返回：
        一个字段和嵌套 entitlement 集合均不可变的元数据对象。

    异常：
        字段类型、UUID 格式、文本内容或 entitlement 结构非法时抛出 ``TypeError``
        或 ``ValueError``。

    约束与副作用：
        只保存公开摘要和 ``SecretRef``，不访问文件系统、秘密提供器或外部工具；
        ``repr`` 通过 ``SecretRef`` 的安全表示自动隐藏 profile locator。
    """

    profile_ref: SecretRef
    uuid: str
    team: str
    application_identifier: str
    entitlements: tuple[tuple[str, object], ...]
    expiration_date: datetime

    def __post_init__(self) -> None:
        """校验并冻结构造参数，确保元数据可安全缓存和重复序列化。

        参数与返回：
            无显式参数；读取当前实例字段并在冻结对象允许的范围内规范化字段。

        异常：
            ``SecretRef``、文本、UUID、entitlement 或时间类型不符合约束时抛出
            ``TypeError`` 或 ``ValueError``。

        约束与副作用：
            只执行内存校验；无当前时间比较，因此过期 profile 仍能被准确读取并交给
            上层签名门禁判断。
        """
        if not isinstance(self.profile_ref, SecretRef):
            raise TypeError("profile_ref 必须是 SecretRef")
        _validate_text(self.uuid, "uuid")
        if _UUID_PATTERN.fullmatch(self.uuid) is None:
            raise ValueError("uuid 必须是规范 UUID")
        team = _validate_text(self.team, "team")
        application_identifier = _validate_text(
            self.application_identifier,
            "application_identifier",
        )
        expiration_date = _normalize_datetime(self.expiration_date, "expiration_date")
        entitlements = _normalize_entitlements(self.entitlements)
        object.__setattr__(self, "team", team)
        object.__setattr__(self, "application_identifier", application_identifier)
        object.__setattr__(self, "expiration_date", expiration_date)
        object.__setattr__(self, "entitlements", entitlements)

    @property
    def source(self) -> SecretRef:
        """返回 ``profile_ref`` 的语义别名，供通用元数据消费者使用。"""
        return self.profile_ref

    @property
    def team_identifier(self) -> str:
        """返回 ``team`` 的明确字段别名。"""
        return self.team

    @property
    def expires_at(self) -> datetime:
        """返回 ``expiration_date`` 的时间字段别名。"""
        return self.expiration_date

    def to_redacted_dict(self) -> dict[str, object]:
        """返回不包含 profile locator 的可记录摘要。

        参数与返回：
            无参数；返回包含公开 UUID、团队、application identifier、entitlements 和
            UTC 过期时间的普通字典，``profile_ref`` 固定替换为脱敏文本。

        异常：
            当前对象已在构造时校验，正常情况下不会抛业务异常。

        约束与副作用：
            返回新建对象，不暴露内部 tuple；调用方修改返回值不会影响元数据，也不会
            触发秘密解析或文件 I/O。
        """
        return {
            "profile_ref": _REDACTED_SECRET_REF,
            "uuid": self.uuid,
            "team": self.team,
            "application_identifier": self.application_identifier,
            "entitlements": _thaw_value(dict(self.entitlements)),
            "expiration_date": self.expiration_date.isoformat(),
        }


class ProvisioningProfileReader:
    """从已解码的 plist bytes 读取 provisioning profile 元数据。

    本读取器故意不持有 ``ProcessRunner``。若调用方需要执行 ``security cms``，应在
    外部适配器中完成并把 stdout bytes 传入 ``parse_bytes``；因此本类不会启动进程、
    写临时文件、访问 SecretRef 或把 profile 原文写入日志。
    """

    @staticmethod
    def parse_bytes(profile_ref: SecretRef, payload: bytes) -> ProfileMetadata:
        """解析 XML 或 binary plist bytes，并返回已校验的公开 profile 元数据。

        参数：
            profile_ref: 对应 profile 的不透明 ``SecretRef``；只被原样保存，不会解析。
            payload: 已从 CMS 容器解码的 XML 或 binary plist bytes。

        返回：
            包含 UUID、team、application identifier、entitlements 和过期时间的
            ``ProfileMetadata``。

        异常：
            ``profile_ref`` 或 payload 类型错误、plist 无法解析、根节点不是字典、
            必需字段缺失或字段类型/值非法时抛出 ``TypeError`` 或 ``ValueError``。

        约束与副作用：
            不执行 ``security``、``plutil`` 或其他外部进程；错误消息不包含 payload
            内容，避免把 profile 原文带入日志。
        """
        if not isinstance(profile_ref, SecretRef):
            raise TypeError("profile_ref 必须是 SecretRef")
        if not isinstance(payload, bytes):
            raise TypeError("payload 必须是 bytes")
        try:
            document = plistlib.loads(payload)
        except (OSError, plistlib.InvalidFileException, ValueError, TypeError) as exc:
            raise ValueError("provisioning profile plist 无法解析") from exc
        if not isinstance(document, dict):
            raise ValueError("provisioning profile 根节点必须是字典")

        root = cast(dict[str, object], document)
        uuid = _required_text(root, "UUID")
        entitlements = _required_mapping(root, "Entitlements")
        application_identifier = _required_text(entitlements, "application-identifier")
        team = _read_team(root, entitlements)
        expiration_date = _required_datetime(root, "ExpirationDate")
        return ProfileMetadata(
            profile_ref=profile_ref,
            uuid=uuid,
            team=team,
            application_identifier=application_identifier,
            entitlements=tuple(entitlements.items()),
            expiration_date=expiration_date,
        )

    @staticmethod
    def parse(profile_ref: SecretRef, payload: bytes) -> ProfileMetadata:
        """兼容性别名：调用 ``parse_bytes`` 解析已解码的 profile plist。"""
        return ProvisioningProfileReader.parse_bytes(profile_ref, payload)

    @staticmethod
    def read(profile_ref: SecretRef, payload: bytes) -> ProfileMetadata:
        """兼容性别名：调用 ``parse_bytes`` 读取已解码的 profile plist。"""
        return ProvisioningProfileReader.parse_bytes(profile_ref, payload)


def _validate_text(value: object, field_name: str) -> str:
    """校验必需公开文本为非空且不含空白或控制字符的字符串。"""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} 不得为空或首尾含空白")
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        raise ValueError(f"{field_name} 不得包含空白或控制字符")
    return value


def _required_text(document: Mapping[str, object], key: str) -> str:
    """从 plist 字典读取并校验一个必需文本字段。"""
    if key not in document:
        raise ValueError(f"provisioning profile 缺少 {key}")
    return _validate_text(document[key], key)


def _required_mapping(document: Mapping[str, object], key: str) -> dict[str, object]:
    """读取一个必需的字符串键字典并复制为普通字典。"""
    if key not in document:
        raise ValueError(f"provisioning profile 缺少 {key}")
    value = document[key]
    if not isinstance(value, dict):
        raise TypeError(f"{key} 必须是字典")
    normalized: dict[str, object] = {}
    raw_document = cast(dict[object, object], value)
    for raw_key, raw_value in raw_document.items():
        if not isinstance(raw_key, str):
            raise TypeError(f"{key} 的 key 必须是 str")
        normalized[raw_key] = raw_value
    return normalized


def _read_team(
    root: Mapping[str, object],
    entitlements: Mapping[str, object],
) -> str:
    """读取顶层 TeamIdentifier，并以 entitlement team ID 作为兼容回退。"""
    if "TeamIdentifier" in root:
        value = root["TeamIdentifier"]
        if not isinstance(value, list):
            raise ValueError("TeamIdentifier 必须是只含一个 team 的数组")
        team_values = cast(list[object], value)
        if len(team_values) != 1:
            raise ValueError("TeamIdentifier 必须是只含一个 team 的数组")
        return _validate_text(team_values[0], "TeamIdentifier")
    if "com.apple.developer.team-identifier" in entitlements:
        return _validate_text(
            entitlements["com.apple.developer.team-identifier"],
            "com.apple.developer.team-identifier",
        )
    raise ValueError("provisioning profile 缺少 TeamIdentifier")


def _required_datetime(document: Mapping[str, object], key: str) -> datetime:
    """读取并规范化 plist 日期字段为 UTC datetime。"""
    if key not in document:
        raise ValueError(f"provisioning profile 缺少 {key}")
    return _normalize_datetime(document[key], key)


def _normalize_datetime(value: object, field_name: str) -> datetime:
    """校验日期并把无时区 plist 日期按 UTC 解释。"""
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} 必须是 datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_entitlements(value: object) -> tuple[tuple[str, object], ...]:
    """校验 entitlement 键值、递归冻结值并按 key 的 UTF-8 字节序排序。"""
    if not isinstance(value, tuple):
        raise TypeError("entitlements 必须是 tuple[tuple[str, object], ...]")
    normalized: list[tuple[str, object]] = []
    seen: set[str] = set()
    for item in cast(tuple[object, ...], value):
        if not isinstance(item, tuple):
            raise TypeError("entitlements 的每一项必须是二元 tuple")
        pair = cast(tuple[object, ...], item)
        if len(pair) != 2:
            raise TypeError("entitlements 的每一项必须是二元 tuple")
        key, raw_value = pair
        key = _validate_text(key, "entitlements key")
        if key in seen:
            raise ValueError(f"entitlements 存在重复 key: {key}")
        seen.add(key)
        normalized.append((key, _freeze_value(raw_value, f"entitlements.{key}")))
    return tuple(sorted(normalized, key=lambda item: item[0].encode("utf-8")))


def _freeze_value(value: object, field_name: str) -> object:
    """递归冻结 plist 支持的标量、数组和字典值。"""
    if value is None or isinstance(value, (str, int, float, bool, bytes, datetime)):
        return value
    if isinstance(value, list):
        items = cast(list[object], value)
        return tuple(_freeze_value(item, field_name) for item in items)
    if isinstance(value, tuple):
        items = cast(tuple[object, ...], value)
        return tuple(_freeze_value(item, field_name) for item in items)
    if isinstance(value, dict):
        pairs: list[tuple[str, object]] = []
        seen: set[str] = set()
        raw_mapping = cast(dict[object, object], value)
        for key, item in raw_mapping.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} 的字典 key 必须是 str")
            if key in seen:
                raise ValueError(f"{field_name} 存在重复 key: {key}")
            seen.add(key)
            pairs.append((key, _freeze_value(item, f"{field_name}.{key}")))
        return tuple(sorted(pairs, key=lambda pair: pair[0].encode("utf-8")))
    raise TypeError(f"{field_name} 含有不支持的 plist 类型")


def _thaw_value(value: object) -> object:
    """把冻结的摘要值复制成便于 JSON/日志适配器消费的普通容器。"""
    if isinstance(value, tuple):
        items = cast(tuple[object, ...], value)
        pairs: list[tuple[object, object]] = []
        for item in items:
            if not isinstance(item, tuple):
                return tuple(_thaw_value(candidate) for candidate in items)
            pair = cast(tuple[object, ...], item)
            if len(pair) != 2:
                return tuple(_thaw_value(candidate) for candidate in items)
            pairs.append(pair)
        result: dict[str, object] = {}
        for key, item in pairs:
            if not isinstance(key, str):
                raise TypeError("冻结字典 key 必须是 str")
            result[key] = _thaw_value(item)
        return result
    if isinstance(value, dict):
        result = {}
        raw_mapping = cast(dict[object, object], value)
        for key, item in raw_mapping.items():
            if not isinstance(key, str):
                raise TypeError("摘要字典 key 必须是 str")
            result[key] = _thaw_value(item)
        return result
    return value


__all__ = ["ProfileMetadata", "ProvisioningProfileReader"]
