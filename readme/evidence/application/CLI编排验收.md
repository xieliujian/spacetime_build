# CLI 编排验收证据

状态：`PENDING`

## 已完成的自动化证据

- application/CLI/中文 docstring 目标回归：`56 passed`；
- application/CLI 定向 Ruff check、Ruff format、Pyright 和 compileall：通过；
- preflight dry-run 的写端口计划为空；
- 运行记录内容对象与当前 run 索引分离，索引更新使用 CAS，重复记录写入幂等；
- 发布薄用例固定上传、远端验证、CAS 激活顺序，验证失败不调用激活；
- 包体薄用例先经过 Release gate，再按共享 `BuildPlatform` 注册表分派。

## 尚待完成的真实证据

- [ ] 计划 18 composition factory 的真实端口装配路径；
- [ ] 隔离工作区、固定源码 revision 和 Unity 版本；
- [ ] 真实运行 ID、BuildManifest ID、ReleaseBundle ID 和 PackageManifest ID；
- [ ] ObjectStore put/verify/CAS generation 逐阶段回执；
- [ ] Android、iOS、Windows 平台实际产物和验证结果；
- [ ] 取消、恢复、回滚和同 run 并发冲突的外部探针；
- [ ] 完整命令、stdout/stderr、退出码、日志 locator 和复核人。

在上述外部能力和隔离探针完成前，`spacetime-build` 只能作为显式注入服务的纯 Python
编排入口，不能标记真实构建、发布或平台打包命令可用。
