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

当前验证基线为 Python 3.10.11：完整测试 51 项通过且无跳过，整体、`st.build.core`
和 `st.build.release` 覆盖率均为 93%，Ruff、Pyright 和 compileall 检查通过。

## 项目资料

- [总体设计](readme/00_全新构建系统设计.md)
- [第一阶段实施计划](readme/12_第一阶段实施计划.md)
- [第二阶段领域模型与 DAG 实施计划](readme/13_第二阶段领域模型与DAG实施计划.md)
- [第三阶段兼容协议实施计划](readme/14_第三阶段兼容协议实施计划.md)
- [续作交接](readme/15_下月续作交接.md)
- [协作与开发约束](AGENTS.md)
- [项目 Agent 技能](.agents/)
