# CLI 编排验收证据

状态：`PENDING`（固定命令树和正式版本 application 已实现，真实 composition 仍待装配）

## 已完成的自动化证据

- application/CLI/中文 docstring 目标回归：`56 passed`；
- application/CLI 定向 Ruff check、Ruff format、Pyright 和 compileall：通过；
- preflight dry-run 的写端口计划为空；
- 运行记录内容对象与当前 run 索引分离，索引更新使用 CAS，重复记录写入幂等；
- 发布薄用例固定上传、远端验证、CAS 激活顺序，验证失败不调用激活；
- `FormalReleaseUseCase` 已覆盖 BuildManifest→兼容输出→UploadPlan→上传→远端验证→版本
  状态固化→CAS 激活→confirm；本地 CDN 端到端通过；
- CLI 已登记 `release build`、版本 preview/allocate、upload、activate、publish、外部探针
  和 compatibility dual-run 命令，具体端口由 composition factory 显式提供；
- 包体薄用例先经过 Release gate，再按共享 `BuildPlatform` 注册表分派。

## 尚待完成的真实证据

- [ ] 计划 18 composition factory 的生产端口装配路径；
- [ ] 隔离工作区、固定源码 revision 和 Unity 版本；
- [ ] 真实运行 ID、BuildManifest ID、ReleaseBundle ID 和 PackageManifest ID；
- [x] 本地 ObjectStore put/verify/CAS generation 逐阶段回执；
- [ ] Android、iOS、Windows 平台实际产物和验证结果；
- [ ] 取消、恢复、回滚和同 run 并发冲突的外部探针；
- [ ] 完整命令、stdout/stderr、退出码、日志 locator 和复核人。

在上述外部能力和隔离探针完成前，`spacetime-build` 只能作为显式注入服务的纯 Python
编排入口，不能标记真实构建、发布或平台打包命令可用。
