# IL2CPP 与 SDK Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` 或
> `superpowers:executing-plans` 逐任务执行；每个 Task 独立完成 RED、GREEN 和回归。

**Goal:** 提供可复用、内容寻址、可审计的 IL2CPP 构建服务，以及声明式 Android/iOS/Windows SDK 扩展组件。

**Architecture:** IL2CPP 以不可变请求、计划、结果和验证器为核心，本地执行经 ProcessRunner，远端执行复用计划 18 的 CiJobClient 与 ObjectStore，不恢复旧自建 HTTP 文件服务。SDK 通过计划 23 的 PackageSdkHook 产生结构化变换计划，各平台基准包在 hook 前后都必须验证；最终跨组件编排仍归计划 26。

**Tech Stack:** Python 3.10、pytest、Unity IL2CPP 工具链、Jenkins/ObjectStore 端口、XML/plist/JSON 结构化解析、Android Gradle、Ruby xcodeproj、Windows layout tools。

---

## 1. 状态、范围与旧系统边界

- 文档状态：**设计与实施计划完成，独立审查通过**。
- 代码状态：**规划中，`st.build.services` 与 `st.build.sdk` 尚不存在**。
- 只读参考：`services/client/client_buildil2cpp.py`、`services/server/service_buildil2cpp*.py`、
  `services/server/pvr_cache.py`、`package/il2cpp_encrypt.py`、`sdk/sdk_xml_process.py`、
  `sdk/sdk_project_post_process.py` 和 `sdk/sdk_post_process.py`。
- 本阶段以前置平台已经产生可验证的基准包/工程为条件；Android、iOS、Windows 可以独立推进。

不得迁移：无认证自建 HTTP 上传、任意 zip 解压、客户端提供服务器路径、MD5 作为安全身份、
共享可变临时目录、正则修改 XML/pbxproj/Gradle、SDK 中的任意脚本执行、明文渠道密钥、
把 IL2CPP/SDK 失败变成整条流水线递归重跑。

非目标：不实现应用商店发布、不定义渠道业务规则、不替代 Unity 自带编译器、不把所有渠道塞进
一个继承树、不保证受保护 IL2CPP 输出与未保护输出字节相同。

## 2. 模块结构

```text
src/st/build/services/il2cpp/
  model.py             # 请求、工具链、架构、结果和状态
  cache_key.py         # 内容寻址输入与工具链身份
  planner.py           # 本地/远端执行计划
  local.py             # ProcessRunner 本地执行
  remote.py            # CiJobClient + ObjectStore 协调
  archive.py           # 安全输入/输出归档
  protection.py        # 可选版本化保护工具计划
  validator.py         # 库、符号、metadata 与架构验证
src/st/build/sdk/
  model.py             # SdkDescriptor、目标平台和声明输入输出
  catalog.py           # TOML descriptor 加载与版本锁定
  planner.py           # hook 排序、冲突和组合
  android.py           # Manifest/Gradle/文件结构计划
  ios.py               # plist/xcode/entitlements/文件结构计划
  windows.py           # layout/config/运行库计划
  apply.py             # 调用平台既有结构化 applier
  validator.py         # SDK 后包体/工程增量验证
```

SDK descriptor 是数据，不包含 Python 模块名、shell、Gradle 代码、Ruby 代码或任意文件覆盖指令。
固定平台 applier 只接受白名单操作。具体渠道 descriptor 必须逐个加入 fixture、review 和验收，不能
通过运行时扫描目录自动启用。

## 3. IL2CPP 契约

```python
@dataclass(frozen=True, slots=True)
class Il2CppBuildRequest:
    request_id: str
    platform: BuildPlatform
    architecture: str
    input_snapshot: BlobRef
    unity_version: str
    toolchain_digest: str
    mode: Il2CppExecutionMode
    protection_policy: str | None
```

身份覆盖输入归档 SHA256、规范文件表、Unity/NDK/Xcode/MSVC 版本、命令模板版本、架构、环境白名单、
保护策略和实现版本。缓存命中必须重新校验输出归档、库架构、符号和 metadata；MD5 只可作为
历史对比字段，不能作为缓存键或完整性证明。

远端流程：客户端先把安全归档写入隔离 ObjectStore key，再以幂等键触发白名单 CI job；job 只接收
对象引用和工具链 ID，结果也写入不可变 key；协调器轮询状态、下载/验证结果。job 参数不能包含本地
绝对路径或秘密。取消停止轮询并请求 CI cancel，已生成对象按保留策略处理。

可选 protection 不直接重写任意 C++。它只能调用固定版本工具处理明确白名单文件，先备份到隔离
工作区并输出变换报告；必须验证 metadata header、生成代码编译和运行时 fixture。不能证明兼容时
保持该策略不可用。

## 4. SDK 契约与平台边界

`SdkDescriptor` 至少包含 sdk ID/version、目标平台、前置/后置阶段、输入 Blob、输出逻辑路径、
结构化操作、SecretRef 名称、冲突键和验证规则。catalog 锁定 descriptor 与所有 payload SHA256。

- Android：复用 XML Manifest transformer、固定 Gradle applier 和资源/库布局；禁止注入脚本文本。
- iOS：复用 plist、Xcode plan/applier、entitlements 和签名模型；SDK 不直接操作 keychain。
- Windows：复用 layout、appconfig、签名与 installer 组件；SDK 不直接调用上传器。
- hook 只能写声明范围；两个 SDK 声明同一独占键、同一目标不同内容或相反删除/写入时规划失败。
- 基准工程先验证，apply 后计算 tree diff 并只允许计划内变化，最终包体再次运行平台 validator。
- SDK SecretRef 只在对应 adapter/action 的最短租约内解析，日志与 manifest 只记录引用和公开指纹。

## 5. 实施任务

每个“中文文档检查”执行 `python -m pytest tests/quality/test_chinese_documentation.py -q`。

### Task 1：IL2CPP 模型

**Files:** Create `src/st/build/services/__init__.py`, `services/il2cpp/__init__.py`, `model.py`; Test `tests/services/il2cpp/test_model.py`。

- [ ] 写失败测试，覆盖平台、架构、BlobRef、Unity/toolchain、local/remote、protection 和非法组合。
- [ ] 运行 `python -m pytest tests/services/il2cpp/test_model.py -q`，预期模型不存在。
- [ ] 实现不可变 request/plan/result/status，复用 BuildPlatform 和 BlobRef。
- [ ] 重跑同一命令，预期退出码 0；运行中文文档检查。

### Task 2：安全归档

**Files:** Create `src/st/build/services/il2cpp/archive.py`; Test `tests/services/il2cpp/test_archive.py`。

- [ ] 写失败测试，覆盖确定性顺序/时间、路径逃逸、绝对路径、链接、重复大小写路径、压缩炸弹限额和 SHA256。
- [ ] 运行 `python -m pytest tests/services/il2cpp/test_archive.py -q`，预期 archive codec 不存在。
- [ ] 实现规范归档与受限解包，返回文件表和 Blob 摘要。
- [ ] 重跑同一命令，预期相同目录得到相同 SHA256；运行中文文档检查。

### Task 3：IL2CPP 缓存键

**Files:** Create `src/st/build/services/il2cpp/cache_key.py`; Test `tests/services/il2cpp/test_cache_key.py`。

- [ ] 写失败测试，逐一改变输入、工具链、架构、命令版本、环境和 protection，要求键变化；输入排列不影响键。
- [ ] 运行 `python -m pytest tests/services/il2cpp/test_cache_key.py -q`，预期 factory 不存在。
- [ ] 实现规范 JSON + SHA256 身份，不包含 request ID、时间和机器临时路径。
- [ ] 重跑同一命令，预期 identity Golden 通过；运行中文文档检查。

### Task 4：执行计划

**Files:** Create `src/st/build/services/il2cpp/planner.py`; Test `tests/services/il2cpp/test_planner.py`。

- [ ] 写失败测试，覆盖 Unity 版本到固定模板、架构输出、local/remote、缓存命中、缺工具链和未知模板。
- [ ] 运行 `python -m pytest tests/services/il2cpp/test_planner.py -q`，预期 `Il2CppPlanner` 不存在。
- [ ] 实现纯计划；命令为参数序列，不读取环境全量或执行工具。
- [ ] 重跑同一命令，预期计划确定；运行中文文档检查。

### Task 5：本地执行器

**Files:** Create `src/st/build/services/il2cpp/local.py`; Test `tests/services/il2cpp/test_local.py`。

- [ ] 写失败测试，覆盖 workspace、ProcessRunner、超时、非零退出、取消、缺输出和部分输出清理。
- [ ] 运行 `python -m pytest tests/services/il2cpp/test_local.py -q`，预期 local executor 不存在。
- [ ] 执行已验证 plan 并提交不可变结果 Blob；不自动重跑 Unity/IL2CPP。
- [ ] 重跑同一命令，预期假工具通过；运行中文文档检查。

### Task 6：远端协调器

**Files:** Create `src/st/build/services/il2cpp/remote.py`; Test `tests/services/il2cpp/test_remote.py`。

- [ ] 写失败测试，覆盖对象上传、CI 白名单参数、幂等触发、轮询、超时、取消、陈旧/伪造结果和哈希错误。
- [ ] 运行 `python -m pytest tests/services/il2cpp/test_remote.py -q`，预期 coordinator 不存在。
- [ ] 组合 ObjectStore 与 CiJobClient；请求只包含 object key/toolchain/request identity，不实现 HTTP server。
- [ ] 重跑同一命令，预期故障注入和重复请求通过；运行中文文档检查。

### Task 7：IL2CPP 输出验证

**Files:** Create `src/st/build/services/il2cpp/validator.py`; Test `tests/services/il2cpp/test_validator.py`。

- [ ] 写失败测试，覆盖库架构、必需文件、符号/metadata 对应、归档清单、输入 identity 和损坏结果。
- [ ] 运行 `python -m pytest tests/services/il2cpp/test_validator.py -q`，预期 validator 不存在。
- [ ] 实现平台类型化验证报告，外部二进制检查经 ProcessRunner。
- [ ] 重跑同一命令，预期损坏 fixture 全部拒绝；运行中文文档检查。

### Task 8：可选保护计划

**Files:** Create `src/st/build/services/il2cpp/protection.py`; Test `tests/services/il2cpp/test_protection.py`。

- [ ] 写失败测试，覆盖策略版本、白名单文件、备份、工具报告、字符串/metadata fixture、失败回滚和取消。
- [ ] 运行 `python -m pytest tests/services/il2cpp/test_protection.py -q`，预期 protection planner 不存在。
- [ ] 实现固定工具请求和变换后验证；不移植旧正则源码重写器。
- [ ] 重跑同一命令，预期计划外文件变化被拒绝；运行中文文档检查。

### Task 9：SDK 模型与 catalog

**Files:** Create `src/st/build/sdk/__init__.py`, `model.py`, `catalog.py`; Test `tests/sdk/test_model.py`, `test_catalog.py`。

- [ ] 写失败测试，覆盖 ID/version/platform/stage、输入输出、冲突键、SecretRef、SHA256 锁和禁止可执行字段。
- [ ] 运行 `python -m pytest tests/sdk/test_model.py tests/sdk/test_catalog.py -q`，预期 SDK 模型不存在。
- [ ] 使用 `tomllib` 实现 descriptor schema/catalog；拒绝 module、script、command 和未锁定 payload。
- [ ] 重跑同一命令，预期恶意 descriptor fixture 被拒绝；运行中文文档检查。

### Task 10：SDK 组合规划

**Files:** Create `src/st/build/sdk/planner.py`; Test `tests/sdk/test_planner.py`。

- [ ] 写失败测试，覆盖拓扑顺序、版本约束、循环依赖、路径/独占键冲突、删除写入冲突和空集合。
- [ ] 运行 `python -m pytest tests/sdk/test_planner.py -q`，预期 `SdkHookPlanner` 不存在。
- [ ] 实现纯计划并输出 shared PackageSdkHook 可消费的有序操作。
- [ ] 重跑同一命令，预期输入排列不影响结果；运行中文文档检查。

### Task 11：Android SDK 操作

**Files:** Create `src/st/build/sdk/android.py`; Test `tests/sdk/test_android.py`。

- [ ] 写失败测试，覆盖 Manifest 节点、Gradle 白名单字段、aar/jar/so/resource 布局、ABI 和冲突。
- [ ] 运行 `python -m pytest tests/sdk/test_android.py -q`，预期 Android mapper 不存在。
- [ ] 映射到计划 23 的结构化 transformer/applier，不生成 Gradle 代码字符串。
- [ ] 重跑同一命令，预期工程 tree diff Golden 通过；运行中文文档检查。

### Task 12：iOS SDK 操作

**Files:** Create `src/st/build/sdk/ios.py`; Test `tests/sdk/test_ios.py`。

- [ ] 写失败测试，覆盖 plist、framework/library、build setting、entitlements、资源和 target 冲突。
- [ ] 运行 `python -m pytest tests/sdk/test_ios.py -q`，预期 iOS mapper 不存在。
- [ ] 映射到计划 25 的 plist/Xcode plan，不直接解析 pbxproj 或操作 keychain。
- [ ] 重跑同一命令，预期结构与重复应用 Golden 通过；运行中文文档检查。

### Task 13：Windows SDK 操作

**Files:** Create `src/st/build/sdk/windows.py`; Test `tests/sdk/test_windows.py`。

- [ ] 写失败测试，覆盖 dll/runtime/config/Launcher、签名前后阶段、目标冲突和保留路径。
- [ ] 运行 `python -m pytest tests/sdk/test_windows.py -q`，预期 Windows mapper 不存在。
- [ ] 映射到计划 27 layout/config hook，不直接签名、构建安装器或上传。
- [ ] 重跑同一命令，预期计划内 tree diff 通过；运行中文文档检查。

### Task 14：SDK 安全应用

**Files:** Create `src/st/build/sdk/apply.py`; Test `tests/sdk/test_apply.py`。

- [ ] 写失败测试，覆盖基准树验证、原子应用、计划外变化、重复应用、异常回滚、取消和 SecretRef 最短租约。
- [ ] 运行 `python -m pytest tests/sdk/test_apply.py -q`，预期 applier 不存在。
- [ ] 调用平台固定 applier 并对 before/after tree manifest 做严格 diff。
- [ ] 重跑同一命令，预期失败不留半变换或秘密；运行中文文档检查。

### Task 15：SDK 后验证

**Files:** Create `src/st/build/sdk/validator.py`; Test `tests/sdk/test_validator.py`。

- [ ] 写失败测试，覆盖 descriptor 锁、声明输出、包体结构、签名失效、资源入口和平台 validator 委托。
- [ ] 运行 `python -m pytest tests/sdk/test_validator.py -q`，预期 validator 不存在。
- [ ] 实现增量报告并强制重跑受影响平台 validator。
- [ ] 重跑同一命令，预期篡改/漏文件 fixture 全部拒绝；运行中文文档检查。

### Task 16：自动化门禁

- [ ] 运行 `python -m pytest tests/services/il2cpp tests/sdk tests/package tests/quality/test_chinese_documentation.py -q`。
- [ ] 分别运行 `--cov=st.build.services.il2cpp` 与 `--cov=st.build.sdk`，各自覆盖率不低于 90%。
- [ ] 运行全量 pytest、Ruff、Pyright 和 compileall，预期全部退出码 0。

### Task 17：真实 IL2CPP 节点验收

**Files:** Create `tests/integration/services/test_il2cpp_probe.py`, `readme/evidence/services/IL2CPP节点验收.md`。

- [ ] 在受控工具链节点运行
  `python -m pytest tests/integration/services/test_il2cpp_probe.py -q --run-external`，分别验证 local/remote、缓存命中、取消和损坏结果。
- [ ] 记录输入/输出 SHA256、Unity/编译器版本、架构、CI build、对象 key、日志 locator 和退出码。
- [ ] protection 只有编译与运行时 fixture 同时通过时才标记该策略可用；否则保持 `PENDING`。

### Task 18：逐渠道 SDK 验收

**Files:** Create `tests/integration/sdk/test_sdk_probe.py`, `readme/evidence/sdk/渠道SDK验收矩阵.md`。

- [ ] 对每个已登记 descriptor 运行
  `python -m pytest tests/integration/sdk/test_sdk_probe.py -q --run-external --sdk-id <受控ID>`。
- [ ] 记录 descriptor/payload SHA256、基准 PackageManifest、tree diff、最终 PackageManifest、签名/安装/启动和退出码。
- [ ] 一个渠道通过不代表其他渠道或平台通过；矩阵逐单元关闭，未知渠道保持不可选择。

## 6. 排查与完成标准

| 症状 | 首查证据 |
| --- | --- |
| 远端重复构建 | request identity、CI idempotency key、对象 key |
| IL2CPP 命中错误缓存 | toolchain digest、输入 archive、validator 报告 |
| protection 编译失败 | 策略/工具版本、tree diff、变换报告 |
| SDK 覆盖文件 | descriptor ownership、hook plan、before/after manifest |
| 平台签名失效 | hook stage、最终平台 validator、签名指纹 |
| 渠道秘密泄漏 | SecretRef 租约、redacted request、日志扫描 |

只有基准包与扩展包均通过真实平台验收，某一 IL2CPP 模式或 SDK descriptor 才能标记可用。
计划 31 逐能力关闭迁移差异；未登记、未锁定或未验收的 SDK 必须 fail closed。
