---
name: st-build-development
description: 开发、重构或审查直接顶级包形式的 Python 构建系统时使用。覆盖领域模型、任务 DAG、旧客户端协议、资源构建、发布、测试和中文文档规范。
---

# ST Build Development

目标是在不修改旧工程的前提下，实现可测试、可恢复、确定性且兼容旧客户端协议的全新构建系统。

## 开始前

读取：

1. `AGENTS.md`；
2. `readme/version_build/00_全新构建系统设计.md`；
3. 当前实施计划；
4. 即将修改的源码和测试。

## 工作方式

1. 明确当前任务属于领域、兼容、发布还是集成层；
2. 先编写能复现需求的失败测试；
3. 实现满足测试的最小代码；
4. 补充中文模块、类、函数和方法 docstring；
5. 为复杂协议、不变量和异常分支补充中文注释；
6. 运行目标测试；
7. 运行格式、类型和完整测试；
8. 更新与实现直接相关的 `readme/` 文档。

## 架构约束

- 新代码直接使用 `src/<domain>/` 顶级包，当前领域包为 `core` 和 `release`；
- 领域层不依赖 SVN、Unity、Jenkins 或 CDN；
- 外部系统通过 Protocol 和适配器接入；
- BuildManifest 不包含运行状态和发布信息；
- ReleaseBundle 是主/低清激活、回滚和审计的最小单位；
- 旧协议只能由 `compatibility` 顶级包生成；
- Manifest、缓存键和协议输出必须确定性生成。

## 完成条件

- 新增测试先失败后通过；
- 相关测试、Ruff、Pyright 和 compileall 通过；
- `src/` 中文 docstring 质量检查通过；
- 未修改 `F:\proj_se\develop\client\tools\build`；
- 文档与当前实现一致；
- 未经用户明确要求不创建 Git commit。
