# AGENTS.md

本仓库用于重新实现 SE 项目的 Unity 构建系统，主要语言为 Python。

## 当前状态

- 第一阶段工程骨架和第二阶段纯 Python 领域内核已经实现并通过质量门禁。
- 第三阶段旧客户端兼容协议的纯 Python DTO、Writer、Parser 和合成 Golden 已实现，真实历史
  双跑和旧客户端解析验收仍为 PENDING；资源构建第三层公共模型和自动化任务契约已实现；
  application/CLI 的请求状态、TOML Profile、覆盖、preflight、CAS 记录、资源/发布/包体/操作
  薄用例、命令树、退出码、脱敏输出和延迟入口已实现，真实 Unity、发布、平台打包或迁移命令
  仍不可宣称可用。
- iOS/Windows 的部分纯 Python 包体计划、IL2CPP 归档/规划/验证、SDK descriptor/catalog/规划
  以及 branch Task 1-7 已实现并有自动化测试；IL2CPP/SDK 执行、installer/平台验证、branch
  后续 apply、art 和真实平台验收仍为 PENDING。
- 总体设计见 `readme/version_build/00_全新构建系统设计.md`。
- 第一阶段计划见 `readme/version_build/12_第一阶段实施计划.md`。
- 第二阶段记录见 `readme/version_build/13_第二阶段领域模型与DAG实施计划.md`。
- 第三阶段计划见 `readme/version_build/14_第三阶段兼容协议实施计划.md`。
- 尚未实现的命令不得写入本文档或宣称可用。

## 迁移边界

- `F:\proj_se\develop\client\tools\build` 是只读参考源。
- 禁止修改参考源中的任何文件。
- 禁止直接从参考源运行可能写入文件、提交 SVN 或上传资源的入口。
- 如需旧系统双跑，必须先复制到隔离工作区，并使用输入快照。
- 新代码和文档只写入当前仓库。

## Python 结构

- 最低版本：Python 3.10。
- 各功能域直接作为顶级导入包，不增加工程名或构建系统名包裹层。
- 源码采用 `src/<domain>/` 布局，当前已实现 `src/core/`、`src/release/`、`src/compatibility/`、
  `src/resource/`、`src/application/`、`src/cli/`、部分 `src/package/platforms/`、`src/services/il2cpp/`、
  `src/sdk/` 和 `src/branch/`。
- 核心领域层不得依赖 SVN、Unity、Jenkins 或具体 CDN 实现。
- 外部系统通过 Protocol 和适配器接入。
- 构建产物模型与发布模型必须分离。
- 旧客户端协议只能由 `compatibility` 模块生成。

## 命名规范

- 包和模块：`snake_case`。
- 类和异常：`PascalCase`。
- 函数、方法、变量和参数：`snake_case`。
- 常量：`UPPER_SNAKE_CASE`。
- 内部 API：单下划线前缀。
- 路径使用 `pathlib.Path`，客户端逻辑路径统一使用 `/`。

## 中文注释

- 每个 Python 模块必须有详细中文模块 docstring。
- 每个类、异常、函数和方法必须有详细中文 docstring。
- docstring 应说明职责、参数、返回值、异常、约束和副作用。
- 复杂算法、协议边界、异常分支和外部副作用必须添加中文行内注释。
- 注释重点解释原因和不变量，不要逐行复述代码。
- 测试代码同样使用中文 docstring 或 Given/When/Then 注释说明验证目标。

## 实现规则

- 使用测试驱动开发：先写失败测试，再写最小实现。
- 领域模型优先使用不可变 `dataclass`。
- 所有公开 API 必须有完整类型标注。
- 禁止 `from ... import *`。
- 禁止模块导入时执行构建或产生外部副作用。
- 业务层禁止直接调用 `os.system`。
- 不得用递归方式重启完整构建流水线。
- Manifest、缓存键和协议输出必须确定性生成。
- 客户端协议变更必须有 Golden 测试和旧客户端解析验证。

## 客户端兼容约束

必须保持兼容：

- AssetBundle 相对路径；
- `assetbundledb_*.txt`；
- 六字段文件列表；
- Redirect 名称、偏移和长度；
- 分包 bit flag；
- CDN 版本目录和版本入口语义。

## Git

- 未经用户明确要求，不创建 commit。
- 不修改或覆盖用户已有改动。
- 禁止使用破坏性 Git 命令。

## 文档

- 人类可读文档位于 `readme/`。
- 文档必须与已实现代码一致。
- 未完成能力必须明确标记为规划中，不得写成已经可用。
- 每个主要模块文档应说明职责、输入、输出、失败场景和排查方式。
