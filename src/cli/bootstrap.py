"""CLI composition root 的延迟装配边界。

bootstrap 不在模块导入时加载环境、创建 ProcessRunner 或连接对象存储。调用方必须
显式提供一个返回 ``CommandServices`` 的 factory；因此测试和受控部署可以注入替身，
缺少真实适配器时会在入口处得到明确的配置错误。
"""

from __future__ import annotations

from collections.abc import Callable

from cli.commands import CommandServices
from core.errors import ConfigurationError


class BootstrapError(ConfigurationError):
    """表示 composition root 没有收到完整服务 factory。"""


def build_composition_root(
    factory: Callable[[], CommandServices] | None,
) -> CommandServices:
    """调用显式 factory 创建命令服务。

    参数：
        factory: 由部署或测试提供的纯 composition factory。

    返回：
        可供 ``CommandDispatcher`` 使用的服务集合。

    异常：
        factory 缺失或返回值不是 ``CommandServices`` 时抛 ``BootstrapError``。

    约束与副作用：
        本函数只在显式调用时执行 factory；不猜测适配器、不读取全局环境。
    """
    if factory is None:
        raise BootstrapError("未提供 composition factory；真实外部适配器尚未装配")
    services = factory()
    if not isinstance(services, CommandServices):
        raise BootstrapError("composition factory 必须返回 CommandServices")
    return services


__all__ = ["BootstrapError", "build_composition_root"]
