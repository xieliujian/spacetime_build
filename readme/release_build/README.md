# 正式版本制作资料

本目录用于记录正式版本资源构建、版本生成、聚合和发布的设计与后续实施计划。

它与 `readme/version_build/` 的边界如下：

- `version_build` 保存全系统架构、阶段路线图和各基础子系统设计；
- `release_build` 保存一次正式版本制作所需的端到端业务规格；
- 基础领域约束仍以 `version_build/00_全新构建系统设计.md` 为准；
- 当前正式版本制作范围以本目录中的已批准规格为准。

## 文档索引

- [正式版本资源构建与发布设计](00_正式版本资源构建与发布设计.md)
- [第一层基础设施实施计划与完成记录](01_第一层基础设施实施计划.md)
- [第二层外部集成适配器实施记录](02_第二层外部集成适配器实施计划.md)

## 当前实现状态

正式版本制作的第一层基础设施和第二层自动化适配器已经实现并通过对应目标测试：

- `configuration`：不可变强类型配置、三层 TOML 合并、严格校验、确定性快照和编辑器服务 API；
- `observability`：稳定错误载体、日志上下文、流式凭据脱敏、统一文本日志和排他文件输出；
- `ports.process`：进程请求、结果、取消、输出 sink 和未来秘密租约的端口契约；
- `integrations.process`：不经 shell 的本地进程执行、双流捕获、超时、取消和完整进程树终止。
- 第二层：HTTP、Secrets、Workspace、SVN 读取、Unity、Jenkins、ObjectStore/CAS 的端口与
  可测试适配器；本地对象存储、环境/文件凭据和 fake 外部端口已覆盖自动化测试。
- 第三层资源公共模型：唯一 `BuildPlatform`、固定资源输入、十二类资源任务身份、精确输出
  发现、类型化 Unity 操作、内容寻址 Blob 提交、单任务执行服务、显式 BuildManifest 聚合，
  以及跨节点 `TaskResultPackage` 的确定性 TOML/framing 校验。

当前仍没有面向正式版本的 Unity 资源打包命令、版本生成、上传或正式发布命令。十二个任务
当前提供的是固定输入目录到 CAS 的自动化契约实现，不等于真实 Unity 产物已验收；真实 SVN、
Unity、Jenkins、Secrets 和供应商 CDN 尚未验收。Redirect、分包、低清、发布编排和 CLI 仍
属于后续层。第三阶段兼容协议的纯 Python DTO、Writer、Parser 和合成 Golden 已完成，但
真实历史输出双跑与旧客户端 Parser 验收仍保持 PENDING。自动化替身与本机 Python fixture
通过不代表真实外部环境已经可用。
