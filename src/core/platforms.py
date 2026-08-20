"""构建系统共享的平台枚举。

本模块只声明资源构建与客户端包体共用的 ``BuildPlatform``。平台值是协议和
缓存身份的一部分，因此不得由各业务包重复声明。导入本模块不执行构建或访问
外部系统。
"""

from enum import Enum


class BuildPlatform(Enum):
    """构建目标平台的稳定枚举。

    职责：
        为资源、客户端包体和发布编排提供唯一的平台身份。

    参数：
        无；成员值使用小写稳定标签。

    返回：
        无；通过 ``BuildPlatform.<NAME>`` 读取成员。

    异常：
        非法名称由 ``Enum`` 标准机制抛出。

    约束与副作用：
        不增加平台别名，不执行 I/O。
    """

    ANDROID = "android"
    IOS = "ios"
    WINDOWS = "windows"
