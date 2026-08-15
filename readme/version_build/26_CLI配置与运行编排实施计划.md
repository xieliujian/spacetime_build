# CLI 配置与运行编排 Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` 或
> `superpowers:executing-plans` 逐任务执行；每个 Task 独立完成 RED、GREEN 和回归。

**Goal:** 实现唯一的 application 用例编排层和薄 CLI，使资源构建、发布、客户端打包、状态、恢复、取消与回滚具有一致的配置、事务和退出码语义。

**Architecture:** `st.build.application` 只组合现有领域服务与端口，不拼协议文本、不执行 shell；`st.build.cli` 只负责 TOML/环境变量/参数覆盖、命令路由和结果呈现。外部实现仍由计划 18 的 composition root 注入，平台组件仍由计划 23、25、27、28 提供。

**Tech Stack:** Python 3.10、`tomllib`、`argparse`、pytest、现有 `st.build.core`/`release` 模型与计划 18 端口。

---

## 1. 状态、范围与安全边界

- 文档状态：**实施计划完成，独立审查通过**。
- 代码状态：**规划中，`st.build.application` 与 `st.build.cli` 尚不存在**。
- 本文是全仓唯一的跨模块运行编排归属；平台模块不得新增自己的顶层 pipeline。
- 当前 README 不得列出任何可执行构建命令；只有对应自动化与外部验收关闭后才能标记可用。
- 旧目录 `F:\proj_se\develop\client\tools\build` 只读；不从该目录启动 controller、Jenkins、上传、签名或 SVN 写操作。

只读参考包括 `resource/res_controller.py`、`resource/res_sync_manger.py`、
`package/build_package.py`、`tool/jenkins_build.py` 和各 `local_config.py`。旧实现中依靠
JobName、环境变量和可变目录推断模式的行为不迁移，所有选择必须进入类型化请求。

非目标：不定义新兼容协议、不实现资源任务、不实现平台 builder、不保存明文凭据、不提供任意 Python/shell 插件入口。

## 2. 模块结构与依赖

```text
src/st/build/application/
  model.py               # RunId、用例输入、结果与统一状态
  preflight.py           # 配置、revision、基线和工具链前置检查
  build_resources.py     # 资源构建用例
  publish_release.py     # 发布、验证和 CAS 激活用例
  package_client.py      # Android/iOS/Windows 客户端出包用例
  operations.py          # 状态、取消、恢复和回滚用例
  records.py             # 执行记录的确定性持久化
src/st/build/cli/
  platforms.py           # 文本平台值到唯一 BuildPlatform 的严格转换
  config.py              # TOML、环境变量和默认值
  overrides.py           # 命令行覆盖与来源追踪
  parser.py              # argparse 命令树
  commands.py            # 薄命令处理器
  output.py              # 人类/JSON 输出和脱敏
  exit_codes.py          # BuildError 到稳定退出码
  bootstrap.py           # 调用计划 18 composition root
  main.py                # 无导入副作用入口
```

固定依赖为 `cli -> application -> domain/ports`，`integrations -> ports`。application 不导入
具体 SVN、Unity、Jenkins、对象存储实现；CLI 不直接调用 Planner、Executor 或平台工具。

状态持久化复用计划 18 的 `ObjectStore`：执行记录使用不可变内容对象，当前 run 索引使用 CAS；
活跃取消使用 `CancellationToken`，跨进程取消请求也写入 CAS 控制对象。本文不新增外部端口。

## 3. 类型化请求、配置和状态

```python
@dataclass(frozen=True, slots=True)
class ApplicationRequest:
    run_id: str
    profile: str
    source_revision: str
    platform: BuildPlatform
    dry_run: bool

@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    state: RunState
    record_locator: str
    artifact_ids: tuple[str, ...]
```

`BuildPlatform` 只从计划 20 的 `st.build.core.platforms` 导入；资源变体只从
`st.build.release.entries.ResourceVariant` 导入。run ID、manifest ID、bundle ID 和 package ID
不得互相替代。

配置优先级严格为：

```text
CLI 显式参数 > 白名单环境变量 > profile TOML > 默认值
```

每个最终字段记录值来源，但 SecretRef 只记录引用名。未知键、类型错误、空 profile、非法平台、
未固定 revision、冲突覆盖和配置文件路径逃逸在 preflight 阶段失败。`dry-run` 只允许解析配置、
读取状态、固定 revision 和生成计划，不获取写工作区、不执行工具、不上传、不 CAS 激活。

统一状态为 `CREATED -> PREFLIGHTED -> PLANNED -> RUNNING -> VERIFYING -> SUCCEEDED`，并有
`FAILED`、`CANCEL_REQUESTED`、`CANCELLED`、`CONFLICTED` 终态/分支。每次状态变化创建新记录并
CAS 更新索引；不能覆盖历史记录或从终态倒退。

## 4. 用例事务边界

| 用例 | 成功边界 | 失败/恢复边界 |
| --- | --- | --- |
| 资源构建 | 已验证 `BuildManifest` 持久化 | 只复用 `VerifiedFrontier`，不提交部分输出 |
| 发布 | `ReleaseBundle` 远端验证后 CAS 激活 | 上传对象可保留；冲突不重试，按记录恢复 |
| 客户端打包 | 已验证 `PackageManifest` 持久化，可选上传有回执 | 不改变 ReleaseBundle 激活状态 |
| 回滚 | 历史 Bundle 复核后 CAS 切换 | 不删除当前/历史不可变对象 |
| 取消 | 停止新调度并终止受控进程 | 保留已完成不可变产物和诊断记录 |

CLI 命令集合规划为 `plan`、`resource build`、`release publish`、`package build`、`run status`、
`run cancel`、`run resume` 和 `release rollback`。分支与美术组件在计划 29/30 稳定后才能由本文
的命令注册机制接入；现阶段不得提前宣称命令可用。

稳定退出码：0 成功；2 配置；3 规划；4 源码；5 工具；6 产物校验；7 发布/CAS 冲突；
8 兼容协议；9 取消；10 未分类内部错误。JSON 输出包含 code、error type、run ID、message 和
log locator，不包含 traceback 或 secret；`--debug` 只增加脱敏诊断。

## 5. 实施任务

每个“中文文档检查”步骤均执行
`python -m pytest tests/quality/test_chinese_documentation.py -q`。未经用户明确要求不创建 commit。

### Task 1：共享平台枚举转换

**Files:** Create `src/st/build/cli/platforms.py`; Test `tests/cli/test_platforms.py`, `tests/core/test_platforms.py`。

- [ ] 写失败测试，固定 `android`、`ios`、`windows` 的严格转换并拒绝别名、大小写漂移和未知值。
- [ ] 运行 `python -m pytest tests/cli/test_platforms.py -q`，预期 `parse_build_platform` 不存在。
- [ ] 实现只返回 `st.build.core.platforms.BuildPlatform` 的 converter，不声明新枚举。
- [ ] 运行 `python -m pytest tests/cli/test_platforms.py tests/core/test_platforms.py -q`，预期退出码 0；运行中文文档检查。

### Task 2：application 请求和状态

**Files:** Create `src/st/build/application/__init__.py`, `model.py`; Test `tests/application/test_model.py`。

- [ ] 写失败测试，覆盖 run ID、固定 revision、平台、dry-run、状态转移和终态不可逆。
- [ ] 运行 `python -m pytest tests/application/test_model.py -q`，预期 `ApplicationRequest` 不存在。
- [ ] 实现不可变请求、`RunState`、`RunResult` 和状态转移校验；不包含适配器对象。
- [ ] 重跑同一命令，预期退出码 0；运行中文文档检查。

### Task 3：TOML profile 加载

**Files:** Create `src/st/build/cli/__init__.py`, `config.py`; Test `tests/cli/test_config.py`。

- [ ] 写失败测试，覆盖默认值、profile 继承、未知键、类型错误、路径逃逸、SecretRef 和 UTF-8 TOML。
- [ ] 运行 `python -m pytest tests/cli/test_config.py -q`，预期 `BuildConfigLoader` 不存在。
- [ ] 使用 `tomllib` 实现白名单 schema 到不可变配置对象的转换；禁止任意对象构造和动态 import。
- [ ] 重跑同一命令，预期配置 fixture 全通过且日志不含测试 secret；运行中文文档检查。

### Task 4：环境变量与 CLI 覆盖

**Files:** Create `src/st/build/cli/overrides.py`; Test `tests/cli/test_overrides.py`。

- [ ] 写失败测试，覆盖四级优先级、字段来源、空环境变量、重复参数、布尔/整数解析和 secret 脱敏。
- [ ] 运行 `python -m pytest tests/cli/test_overrides.py -q`，预期 `ConfigOverrideResolver` 不存在。
- [ ] 实现显式字段映射和冲突错误；不把完整环境变量集合传入业务层。
- [ ] 重跑同一命令，预期确定性结果与脱敏快照通过；运行中文文档检查。

### Task 5：preflight 与 dry-run

**Files:** Create `src/st/build/application/preflight.py`; Test `tests/application/test_preflight.py`。

- [ ] 写失败测试，覆盖 revision 固定、baseline/release/package ID、工具链版本、平台能力和 dry-run 零写调用。
- [ ] 运行 `python -m pytest tests/application/test_preflight.py -q`，预期 `PreflightService` 不存在。
- [ ] 实现只读前置检查和不可变 `PreflightResult`；用记录型替身证明 dry-run 未调用 workspace/process/put/CAS。
- [ ] 重跑同一命令，预期退出码 0；运行中文文档检查。

### Task 6：执行记录存储

**Files:** Create `src/st/build/application/records.py`; Test `tests/application/test_records.py`。

- [ ] 写失败测试，覆盖规范 JSON、未知 schema、陈旧 ID、状态倒退、CAS 冲突、重复写和并发更新。
- [ ] 运行 `python -m pytest tests/application/test_records.py -q`，预期 `RunRecordRepository` 不存在。
- [ ] 用现有 ObjectStore 实现不可变记录与 CAS 索引，不新增文件系统/数据库端口。
- [ ] 重跑同一命令，预期并发 fixture 只有一个 CAS 成功；运行中文文档检查。

### Task 7：资源构建用例

**Files:** Create `src/st/build/application/build_resources.py`; Test `tests/application/test_build_resources.py`。

- [ ] 写失败测试，覆盖任务 plan、DAG、Executor、Frontier、BuildManifest factory、取消和部分失败。
- [ ] 运行 `python -m pytest tests/application/test_build_resources.py -q`，预期 `BuildResourcesUseCase` 不存在。
- [ ] 按计划 20 的组件顺序组合服务并逐阶段写记录；不在用例中实现资源规则或 Unity 参数。
- [ ] 重跑同一命令，预期假任务端到端产生确定性 manifest；运行中文文档检查。

### Task 8：发布用例

**Files:** Create `src/st/build/application/publish_release.py`; Test `tests/application/test_publish_release.py`。

- [ ] 写失败测试，覆盖 merger 到 activator 的顺序、协议校验、上传失败、远端哈希失败、CAS 冲突和取消。
- [ ] 运行 `python -m pytest tests/application/test_publish_release.py -q`，预期 `PublishReleaseUseCase` 不存在。
- [ ] 仅组合计划 21 的单职责服务；远端验证证明是激活的强制输入，冲突返回 `CONFLICTED`。
- [ ] 重跑同一命令，预期失败分支不激活且已上传对象可恢复；运行中文文档检查。

### Task 9：客户端打包用例

**Files:** Create `src/st/build/application/package_client.py`; Test `tests/application/test_package_client.py`。

- [ ] 写失败测试，覆盖 Release gate、平台 dispatch、PackageManifest、可选上传、取消和平台能力缺失。
- [ ] 运行 `python -m pytest tests/application/test_package_client.py -q`，预期 `PackageClientUseCase` 不存在。
- [ ] 通过注册表组合 Android/iOS/Windows 组件；不写平台分支流水线，不允许包体失败改变发布入口。
- [ ] 重跑同一命令，预期三个假平台使用同一事务语义；运行中文文档检查。

### Task 10：状态、取消、恢复与回滚用例

**Files:** Create `src/st/build/application/operations.py`; Test `tests/application/test_operations.py`。

- [ ] 写失败测试，覆盖未知 run、终态取消、重复取消、恢复身份漂移、发布恢复和 Bundle 级回滚冲突。
- [ ] 运行 `python -m pytest tests/application/test_operations.py -q`，预期 operations 用例不存在。
- [ ] 实现薄用例，恢复委托 Frontier/ReleaseRecoveryPlanner，回滚委托 ReleaseRollbackPlanner。
- [ ] 重跑同一命令，预期所有操作幂等且无递归启动；运行中文文档检查。

### Task 11：CLI 命令树和退出码

**Files:** Create `src/st/build/cli/parser.py`, `exit_codes.py`; Test `tests/cli/test_parser.py`, `test_exit_codes.py`。

- [ ] 写失败测试，固定命令/参数、互斥项、帮助输出和上述 0/2..10 退出码映射。
- [ ] 运行 `python -m pytest tests/cli/test_parser.py tests/cli/test_exit_codes.py -q`，预期 parser/mapper 不存在。
- [ ] 使用 `argparse` 实现纯解析与错误映射；解析阶段不构造外部适配器。
- [ ] 重跑同一命令，预期帮助 Golden 与错误码通过；运行中文文档检查。

### Task 12：输出、命令处理器和 bootstrap

**Files:** Create `src/st/build/cli/output.py`, `commands.py`, `bootstrap.py`; Test `tests/cli/test_commands.py`, `test_output.py`, `test_bootstrap.py`。

- [ ] 写失败测试，覆盖 JSON/人类输出、secret/路径脱敏、命令到唯一 use case、缺 adapter 和构造零副作用。
- [ ] 运行 `python -m pytest tests/cli/test_commands.py tests/cli/test_output.py tests/cli/test_bootstrap.py -q`，预期目标模块不存在。
- [ ] 绑定计划 18 factory 并调用 application；handler 不包含领域判断，output 不输出原始 traceback。
- [ ] 重跑同一命令并扫描测试输出中的测试 secret，预期退出码 0 且无泄漏；运行中文文档检查。

### Task 13：可执行入口

**Files:** Create `src/st/build/cli/main.py`; Modify `pyproject.toml`; Test `tests/cli/test_main.py`, `tests/test_package_import.py`。

- [ ] 写失败测试，要求导入无副作用、`main(argv)` 返回整数、Ctrl+C 映射取消且 console script 指向唯一入口。
- [ ] 运行 `python -m pytest tests/cli/test_main.py tests/test_package_import.py -q`，预期入口不存在。
- [ ] 实现 `main` 和 console script；仅在函数调用后加载配置与 composition root。
- [ ] 重跑同一命令，预期退出码 0；运行中文文档检查。

### Task 14：替身端到端与并发门禁

**Files:** Create `tests/integration/application/test_cli_workflows.py`, `test_cli_concurrency.py`。

- [ ] 写端到端测试，覆盖 plan、资源、发布、三个假平台打包、状态、取消、恢复、回滚与同 run 并发冲突。
- [ ] 运行 `python -m pytest tests/integration/application -q`，预期在组合未完整时失败。
- [ ] 只补必要 composition/fixture，不绕过公开 CLI/application API。
- [ ] 重跑同一命令，预期退出码 0；确认 dry-run 的写端口调用计数为 0。

### Task 15：阶段门禁与真实编排探针

**Files:** Create `tests/integration/application/test_controlled_workflow_probe.py`, `readme/evidence/application/CLI编排验收.md`。

- [ ] 运行 `python -m pytest tests/application tests/cli tests/integration/application -q` 和
  `python -m pytest --cov=st.build.application --cov=st.build.cli --cov-report=term-missing --cov-fail-under=90 tests/application tests/cli`，预期退出码 0。
- [ ] 运行全量 pytest、Ruff、Pyright、compileall 和中文文档检查，预期全部退出码 0。
- [ ] 在已配置隔离外部环境运行
  `python -m pytest tests/integration/application/test_controlled_workflow_probe.py -q --run-external`，记录 run ID、revision、manifest/bundle/package ID、CAS generation 和退出码。
- [ ] 外部能力缺失或失败时证据保持 `PENDING`；只更新自动化状态，不宣称真实构建命令可用。

## 6. 失败排查与完成标准

| 症状 | 首查证据 |
| --- | --- |
| 覆盖未生效 | 最终配置字段来源、profile、CLI argv |
| dry-run 产生写入 | 端口调用审计、workspace lease、ObjectStore put/CAS |
| 状态卡住 | 当前/前一 record ID、CAS generation、取消时间线 |
| 恢复重复执行 | plan identity、Frontier、已验证阶段记录 |
| 发布后包体失败 | PackageManifest、ReleaseBundle gate、平台日志 locator |
| CLI 返回错误码 | BuildError 类型、exit code mapper、脱敏 JSON |

完成要求是 application 能以替身端口完整组合各用例，CLI 解析与退出码稳定，状态可恢复且
dry-run 经测试证明无写副作用。真实 Unity、存储和平台节点未验收前，对应命令仍标记规划中。
