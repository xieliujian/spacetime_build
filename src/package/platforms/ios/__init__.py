"""iOS 客户端打包平台模型与后续工具入口。

本包只暴露 iOS 打包领域的稳定类型；模型负责校验 bundle ID、构建配置、导出方法、
目标集合、团队引用和脱敏秘密引用的关系。Xcode、codesign、security 等外部工具由
后续模块通过受控适配器接入，导入本包不会执行平台命令或产生文件系统副作用。
"""

from package.platforms.ios.model import IosExportMethod, IosExportTarget, IosPackageOptions

__all__ = ["IosExportMethod", "IosExportTarget", "IosPackageOptions"]
