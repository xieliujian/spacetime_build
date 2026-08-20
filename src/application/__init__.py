"""构建系统 application 用例层的公共入口。

application 只组合领域模型、端口和显式注入的服务；它不拼接旧协议、不直接调用
shell，也不在导入时读取配置或创建外部适配器。具体用例模块按事务边界分别实现。
"""

from application.model import (
    ApplicationRequest,
    RunResult,
    RunState,
    can_transition,
    transition_run_state,
)

__all__ = [
    "ApplicationRequest",
    "RunResult",
    "RunState",
    "can_transition",
    "transition_run_state",
]
