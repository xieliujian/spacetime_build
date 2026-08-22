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
- [正式版本与迁移验收实施记录](03_正式版本与迁移验收实施记录.md)

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
  以及跨节点 `TaskResultPackage` 的确定性 TOML/framing 校验；`config` 额外提供类型化
  Schema 转换端口，绑定同一快照生成读取代码、BIN 和可选 TXT；`lua` 额外提供 source、
  bytecode、encrypted 三种模式的类型化转换端口和秘密引用边界；`shader_variant` 提供
  `collect_variant` 的 Shader 工程操作、假 Unity builder 和精确输出契约；`shader_bundle`
  提供显式 variant 输入摘要、`build_shader_bundle` Shader 工程操作、假 Unity builder、
  `depend/shader_*` 输出所有权和完整校验后 CAS 提交；`scene` 提供显式 Shader Bundle 输入
  摘要、`build_scene` 资源工程操作、假 Unity builder、`scene/` 输出所有权和 Unity Manifest
  依赖保序校验；`map`、`character`、`texture`、`ui`、`particle`、`audio`、`video` 已统一
  接入 `UnityAssetResourceTask`，各自声明输入来源、操作名、设置和输出前缀，并可由
  `UnityBatchAssetBuilder` 调用真实 Unity batchmode。

正式版本链已经有 application 实现：`FormalReleaseUseCase` 由资源 `BuildManifest` 生成
传输对象、五库/六字段兼容输出和确定性 `UploadPlan`，顺序执行上传、远端回读验证、版本
预留 `mark_ready`、入口 CAS `prepare_activation`/激活和 `confirm`；`VersionAllocator`
提供入口流身份、FileListNo 单调分配、构建幂等、持久 JSON 状态和恢复冲突。CLI 已登记
`release build`、`release version preview/allocate`、`release upload`、`release activate`、
`release publish`、`external probe` 和 `compatibility dual-run`，具体端口仍由 composition
factory 显式注入。

外部探针位于 `integrations.probes`：SVN、Unity、Jenkins、Secrets 和 ObjectStore/CDN
均执行真实注入端口调用，并写入不含秘密的 JSON 证据；没有真实地址、凭据、工具或历史输入
时明确返回 `PENDING`。旧系统双跑位于 `compatibility.legacy_acceptance`，强制源码快照、
历史产物和候选产物都在隔离根内，并对两边执行旧客户端 AssetBundle Parser 与六字段
Parser；当前合成 fixture 已通过，真实历史产物仍保持 `PENDING`。
