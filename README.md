# spacetime_build

本项目用于在不修改旧构建工具的前提下，重新实现 SE 项目的 Unity 构建系统。工程采用
Python 3.10+ 和 `src` 布局，顶级导入命名空间为 `st.build`。

当前已完成第一阶段工程骨架和第二阶段纯 Python 领域内核，包括不可变产物模型、
确定性 BuildManifest、任务 DAG、恢复 Frontier、同步参考 Executor，以及协议无关的
ReleaseSnapshot、ReleaseManifest、ReleaseBundle 和激活状态机。

第三阶段旧客户端兼容协议仍处于规划中；六字段文件列表、`assetbundledb_*.txt`、
Redirect/分包协议输出、SVN/Unity/Jenkins/CDN 适配器、资源构建任务和 CLI 尚未实现。
因此当前版本可用于领域模型与规划执行逻辑的开发验证，但还不能执行真实 Unity 构建、
上传、激活或回滚。

阶段 3 至阶段 14 的设计与实施文档已经按模块写入 `readme/`，17 至 31 号文档已完成独立审查，覆盖兼容协议、外部适配器、
资源、发布、CLI、Android、iOS、Windows、IL2CPP/SDK、分支、美术辅助和端到端迁移。
这些文档是后续 TDD 实施依据，不表示对应代码或平台能力已经可用。

当前验证基线为 Python 3.10.11：完整测试 51 项通过且无跳过，整体、`st.build.core`
和 `st.build.release` 覆盖率均为 93%，Ruff、Pyright 和 compileall 检查通过。

## 项目资料

- [总体设计](readme/00_全新构建系统设计.md)
- [第一阶段实施计划](readme/12_第一阶段实施计划.md)
- [第二阶段领域模型与 DAG 实施计划](readme/13_第二阶段领域模型与DAG实施计划.md)
- [第三阶段兼容协议实施计划](readme/14_第三阶段兼容协议实施计划.md)
- [续作交接](readme/15_下月续作交接.md)
- [第三阶段兼容协议设计](readme/16_第三阶段兼容协议设计.md)
- [全系统实施总路线图](readme/17_全系统实施总路线图.md)
- [外部集成适配器设计与实施计划](readme/18_外部集成适配器设计与实施计划.md)
- [资源构建流水线设计](readme/19_资源构建流水线设计.md)
- [资源构建实施计划](readme/20_资源构建实施计划.md)
- [发布缓存与恢复实施计划](readme/21_发布缓存与恢复实施计划.md)
- [Android 客户端打包设计](readme/22_Android客户端打包设计.md)
- [Android 客户端打包实施计划](readme/23_Android客户端打包实施计划.md)
- [iOS 客户端打包设计](readme/24_iOS客户端打包设计.md)
- [iOS 客户端打包实施计划](readme/25_iOS客户端打包实施计划.md)
- [CLI 配置与运行编排实施计划](readme/26_CLI配置与运行编排实施计划.md)
- [Windows 客户端打包设计与实施计划](readme/27_Windows客户端打包设计与实施计划.md)
- [IL2CPP 与 SDK 实施计划](readme/28_IL2CPP与SDK实施计划.md)
- [分支构建能力实施计划](readme/29_分支构建能力实施计划.md)
- [美术辅助能力实施计划](readme/30_美术辅助能力实施计划.md)
- [端到端迁移与验收计划](readme/31_端到端迁移与验收计划.md)
- [协作与开发约束](AGENTS.md)
- [项目 Agent 技能](.agents/)
