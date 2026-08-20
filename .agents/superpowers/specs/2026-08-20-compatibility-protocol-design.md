# 第三层兼容协议设计与实施计划

> **For agentic workers:** 以 TDD 逐任务执行；每个任务先写失败测试，再实现最小代码。

**目标：** 从已经校验的 `ReleaseManifest` 与 `ReleaseSnapshot` 单向生成旧客户端可见的六字段文件列表和五类 `assetbundledb_*.txt` 数据库，并提供严格 Parser 与合成 Golden。

**范围：** 本阶段实现 `compatibility` 顶级包、协议 DTO、UTF-8/换行策略、Writer、Parser、合成 fixture 和质量门禁。真实历史输出、旧客户端 Parser、旧系统双跑和迁移验收证据不在本阶段伪造，证据保持 `PENDING`。

**架构：** `compatibility` 只能依赖 `release`，不能被 `release` 反向依赖。DTO 只能由已经验证的领域对象工厂产生；Writer 只负责确定性字节编码；Parser 返回独立只读解析视图，不调用 Writer DTO。文件名和依赖使用 UTF-8 字节排序，依赖 tuple 保序保重复。

**错误边界：** 协议输入、路径、UTF-8、换行、整数、索引和引用错误统一转换为 `CompatibilityError`；领域不变量继续使用 `PublishError`。不执行 Unity、SVN、Jenkins、CDN 或写入旧参考目录。

## 实施顺序

1. 加固 `ReleaseSnapshot` 工厂、`ReleaseEntry.object_version` 和 `ReleaseManifest.file_list_no`/`list_version` 一致性。
2. 创建 `compatibility.line_endings` 和六字段文件列表 DTO、Writer、Parser。
3. 创建 AssetBundle DTO、五库路由、Writer、Parser。
4. 创建合成 Golden、SHA256 清单、`.gitattributes` 和 `PENDING` 验收证据。
5. 执行目标测试、全量测试、Ruff、Pyright、compileall 和中文文档检查；更新实施计划与交接文档。

## 文件边界

- `src/compatibility/`：协议模型、转换、编码和解析。
- `tests/compatibility/`：每个协议模块的 TDD 测试。
- `tests/fixtures/compatibility/synthetic/`：独立写入、按 bytes 校验的合成 Golden。
- `readme/evidence/compatibility/`：真实迁移验收模板，初始状态为 `PENDING`。
- `readme/version_build/14_第三阶段兼容协议实施计划.md`、`15_下月续作交接.md`：只记录已验证状态。

## 验收标准

- 所有协议输出使用显式 UTF-8 和显式 LF/CRLF，不依赖 Windows 平台默认换行。
- 六字段文件列表和 AssetBundle 数据库 Golden 与独立 bytes fixture 完全一致。
- Parser 拒绝 BOM、混合换行、未终止行、错误字段数、非法整数、非法路径、越界引用和循环。
- 合成测试、质量测试、Ruff、Pyright、compileall 和完整 pytest 通过。
- 真实历史 fixture、旧客户端 Parser 和迁移 diff 未具备时，状态保持“实现完成但迁移验收未关闭”。
