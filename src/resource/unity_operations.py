"""类型化 Unity 操作和旧项目参数映射。

资源任务只生成 ``UnityOperation``，不直接拼接旧项目的 ``-BUILD_*`` 参数。
``LegacyUnityFlagMapper`` 是唯一的兼容映射边界，输出稳定排序的参数序列，并在
操作模型中校验输出根和参数键，避免把路径逃逸或重复键交给 Unity 适配器。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from resource.model import ResourceProjectRole

_SAFE_SEGMENT = re.compile(r"^[^/\\.][^/\\]*$")
_OPERATION_FLAGS = {
    "build_config": "-BUILD_CONFIG",
    "build_shader_variant": "-BUILD_SHADER",
    "build_shader_bundle": "-BUILD_SHADER",
    "build_scene": "-BUILD_SCENE",
    "build_map": "-BUILD_MAP",
    "build_character": "-BUILD_CHARACTER",
    "build_texture": "-BUILD_TEXTURE",
    "build_ui": "-BUILD_UI",
    "build_particle": "-BUILD_PARTICLE",
    "build_audio": "-BUILD_AUDIO",
    "build_video": "-BUILD_VIDEO",
    "build_lua": "-BUILD_LUA",
}


class UnityProjectRole(Enum):
    """旧 Unity 工程的类型化角色。

    职责：
        约束 Unity 请求使用的工程类别，并与资源任务操作身份分离。

    参数：
        无；成员值为稳定文本标签。

    返回：
        无；通过枚举成员访问。

    异常：
        非法名称由 ``Enum`` 标准机制抛出。

    约束与副作用：
        只描述操作，不启动 Unity。
    """

    RESOURCE = ResourceProjectRole.RESOURCE.value
    SHADER = ResourceProjectRole.SHADER.value
    CONFIG = ResourceProjectRole.CONFIG.value


def _validate_segment(value: str, *, field_name: str) -> None:
    """校验逻辑名称或输出根为安全单路径段。

    参数：
        value: 待校验字符串。
        field_name: 错误消息字段名。

    返回：
        ``None``，表示字符串可以作为协议逻辑段。

    异常：
        空值、点段、分隔符或控制字符会抛出 ``ValueError``。

    约束与副作用：
        纯函数；不解析本地绝对路径。
    """
    if not isinstance(value, str) or _SAFE_SEGMENT.fullmatch(value) is None:
        raise ValueError(f"{field_name} 必须是安全的相对路径段")
    if any(ord(char) < 0x20 for char in value):
        raise ValueError(f"{field_name} 不得包含控制字符")


@dataclass(frozen=True, slots=True)
class UnityOperation:
    """一次可确定性编码的 Unity 构建操作。

    职责：
        保存操作名称、工程角色、唯一参数键和精确预期输出根，供 Unity 适配器
        后续转换为 ``UnityBatchRequest``。

    参数：
        name: 稳定操作名称，如 ``build_scene``。
        project_role: Unity 工程角色。
        arguments: 排序后的字符串键值对；键不得重复。
        expected_output_roots: 相对输出根集合，使用 ``/`` 分隔。

    返回：
        无；通过字段读取操作。

    异常：
        字段类型、重复键、换行或不安全路径时抛出 ``TypeError`` / ``ValueError``。

    约束与副作用：
        冻结对象；参数会按 UTF-8 键值排序，不执行工具。
    """

    name: str
    project_role: UnityProjectRole
    arguments: tuple[tuple[str, str], ...]
    expected_output_roots: tuple[str, ...]

    def __post_init__(self) -> None:
        """校验并规范 Unity 操作字段。

        参数：
            无；读取实例字段。

        返回：
            ``None``；成功时参数元组已按稳定字节序冻结。

        异常：
            非法名称、参数或输出根时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            仅通过 ``object.__setattr__`` 写回冻结元组，不产生 I/O。
        """
        _validate_segment(self.name, field_name="name")
        if not isinstance(self.project_role, UnityProjectRole):
            raise TypeError("project_role 必须是 UnityProjectRole")
        if not isinstance(self.arguments, tuple):
            raise TypeError("arguments 必须是 tuple[tuple[str, str], ...]")
        keys: set[str] = set()
        for pair in self.arguments:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("arguments 每项必须是 (key, value) 元组")
            key, value = pair
            _validate_segment(key, field_name="argument key")
            if not isinstance(value, str) or any(char in value for char in "\r\n"):
                raise ValueError("argument value 必须是无换行字符串")
            if key in keys:
                raise ValueError(f"arguments 存在重复 key: {key!r}")
            keys.add(key)
        normalized_arguments = tuple(
            sorted(
                self.arguments, key=lambda pair: (pair[0].encode("utf-8"), pair[1].encode("utf-8"))
            )
        )
        object.__setattr__(self, "arguments", normalized_arguments)
        if not isinstance(self.expected_output_roots, tuple) or not self.expected_output_roots:
            raise ValueError("expected_output_roots 必须是非空 tuple")
        for root in self.expected_output_roots:
            _validate_segment(root, field_name="expected_output_root")
        if len(set(self.expected_output_roots)) != len(self.expected_output_roots):
            raise ValueError("expected_output_roots 不得重复")


class LegacyUnityFlagMapper:
    """把类型化 Unity 操作转换为旧项目参数序列。

    职责：
        集中维护旧项目 ``-BUILD_*`` flag 与操作名的对应关系，使资源任务不依赖
        旧参数协议。

    参数：
        通过静态方法接收已校验的 ``UnityOperation``。

    返回：
        ``arguments_for`` 返回旧 flag 加稳定排序键值参数组成的 tuple。

    异常：
        未登记操作名时抛出 ``ValueError``，参数类型错误时抛出 ``TypeError``。

    约束与副作用：
        纯函数；不启动 Unity，不写日志和文件。
    """

    @staticmethod
    def arguments_for(operation: UnityOperation) -> tuple[str, ...]:
        """生成旧 Unity batchmode 的稳定参数序列。

        参数：
            operation: 已校验的类型化 Unity 操作。

        返回：
            以旧 ``-BUILD_*`` 开头、随后按键排序的 ``-key value`` 参数元组。

        异常：
            操作类型错误或名称没有兼容映射时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            纯函数；不改变 operation。
        """
        if not isinstance(operation, UnityOperation):
            raise TypeError("operation 必须是 UnityOperation")
        try:
            flag = _OPERATION_FLAGS[operation.name]
        except KeyError as exc:
            raise ValueError(f"没有旧 Unity flag 映射: {operation.name!r}") from exc
        values: list[str] = [flag]
        for key, value in operation.arguments:
            values.extend((f"-{key}", value))
        return tuple(values)
