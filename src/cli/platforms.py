"""把 CLI 平台文本严格转换为唯一的 core 平台枚举。

平台值会参与任务身份和包体请求身份，因而 CLI 边界不接受别名、大小写折叠或
隐式字符串转换。本模块只做纯内存校验，不声明第二套平台枚举，也不访问外部系统。
"""

from core.errors import ConfigurationError
from core.platforms import BuildPlatform


def parse_build_platform(value: str) -> BuildPlatform:
    """解析一个规范小写平台值。

    参数：
        value: 只能是 ``android``、``ios`` 或 ``windows`` 的非空字符串。

    返回：
        来自 ``core.platforms`` 的唯一 ``BuildPlatform`` 成员。

    异常：
        输入不是字符串或不是规范值时抛出 ``ConfigurationError``。

    约束与副作用：
        不做大小写折叠、别名兼容或平台探测；只读取输入，不产生 I/O。
    """
    if not isinstance(value, str):
        raise ConfigurationError("platform 必须是 str")
    try:
        return BuildPlatform(value)
    except ValueError as exc:
        raise ConfigurationError(
            f"platform 必须是 android、ios 或 windows，实际为 {value!r}"
        ) from exc
