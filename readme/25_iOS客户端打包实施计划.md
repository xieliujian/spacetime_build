# iOS 客户端打包 Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 执行。每个 Task 独立完成 RED、GREEN 和回归。

**Goal:** 实现 iOS Xcode 导出、工程配置、签名、archive、IPA、dSYM 和包体验证。

**Architecture:** 复用共享 package/release gate/workspace/Unity export；iOS 模块只产生结构化计划并通过 ProcessRunner 调用 macOS 工具。

**Tech Stack:** Python 3.10、pytest、Unity、Xcode/xcodebuild、security、codesign、plutil。

---

## 1. 状态与共同命令

- 文档状态：**实施计划完成，独立审查通过**。
- 代码状态：**规划中**。
- 每个“中文文档检查”步骤执行 `python -m pytest tests/quality/test_chinese_documentation.py -q`。
- 未经用户明确要求不创建 commit。

## 2. 模型与配置

### Task 1：iOS 模型

**Files:** Create `src/st/build/package/platforms/ios/__init__.py`, `model.py`; Test `tests/package/ios/test_model.py`。

- [ ] 写失败测试，覆盖 configuration、export method、target 集合、bundle ID、team，以及 profile/certificate/private-key SecretRef 完整映射和 project-only。
- [ ] 运行 `python -m pytest tests/package/ios/test_model.py -q`，预期 iOS 模型不存在。
- [ ] 实现不可变 IosPackageOptions、IosExportTarget 和 IosExportMethod。
- [ ] 重跑同一命令，预期通过。
- [ ] 运行中文文档检查。

### Task 2：plist 变换

**Files:** Create `src/st/build/package/platforms/ios/plist.py`; Test `tests/package/ios/test_plist.py`。

- [ ] 写失败测试，覆盖 bundle/version、URL schemes、重复键、非法类型、删除废弃键和确定性输出。
- [ ] 运行 `python -m pytest tests/package/ios/test_plist.py -q`，预期 `PlistTransformer` 不存在。
- [ ] 使用 plist parser 实现结构化变换。
- [ ] 重跑同一命令，预期 Golden 通过。
- [ ] 运行中文文档检查。

### Task 3：provisioning profile 元数据

**Files:** Create `src/st/build/package/platforms/ios/provisioning.py`; Test `tests/package/ios/test_provisioning.py`。

- [ ] 写失败测试，覆盖 SecretRef、UUID、team、application identifier、entitlements、过期时间和脱敏。
- [ ] 运行 `python -m pytest tests/package/ios/test_provisioning.py -q`，预期 `ProvisioningProfileReader` 不存在。
- [ ] 实现通过 ProcessRunner 调用 `security cms`/`plutil` 的类型化读取器。
- [ ] 重跑同一命令，预期假工具通过且日志无 profile 内容。
- [ ] 运行中文文档检查。

### Task 4：Xcode 工程变换计划

**Files:** Create `src/st/build/package/platforms/ios/xcode_project.py`; Test `tests/package/ios/test_xcode_project.py`。

- [ ] 写失败测试，覆盖 target、build setting、framework/library、entitlements、幂等和冲突。
- [ ] 运行 `python -m pytest tests/package/ios/test_xcode_project.py -q`，预期 `XcodeProjectPlan` 不存在。
- [ ] 实现不可变变换计划和专用工具请求；不在业务层修改 pbxproj 文本。
- [ ] 重跑同一命令，预期通过。
- [ ] 运行中文文档检查。

### Task 5：应用 Xcode 工程计划

**Files:** Create `src/st/build/package/platforms/ios/xcode_apply.py`, `tools/xcode/apply_project.rb`; Test `tests/package/ios/test_xcode_apply.py`。

- [ ] 写失败测试，覆盖 workspace 限制、专用 editor 请求、重复应用、冲突、取消和失败回滚。
- [ ] 运行 `python -m pytest tests/package/ios/test_xcode_apply.py -q`，预期 `XcodeProjectPlanApplier` 不存在。
- [ ] 实现 applier：通过 ProcessRunner 把结构化 JSON 请求交给固定 Ruby `xcodeproj` 工具；工具只处理白名单字段并返回 JSON 结果。
- [ ] 重跑同一命令，预期工程树 Golden 稳定且失败无半配置。
- [ ] 运行中文文档检查。

### Task 6：临时 keychain 租约

**Files:** Create `src/st/build/package/platforms/ios/keychain.py`; Test `tests/package/ios/test_keychain.py`。

- [ ] 写失败测试，覆盖从每个 target 的 certificate/private-key SecretRef 导入材料、独占临时 keychain、受限权限、异常/取消清理和日志脱敏。
- [ ] 运行 `python -m pytest tests/package/ios/test_keychain.py -q`，预期 `IosKeychainLease` 不存在。
- [ ] 实现上下文管理租约，秘密通过标准输入/受控临时文件进入 security 工具并在 finally 清理。
- [ ] 重跑同一命令，预期假 security 通过且无秘密残留。
- [ ] 运行中文文档检查。

### Task 7：签名计划

**Files:** Create `src/st/build/package/platforms/ios/signing.py`; Test `tests/package/ios/test_signing.py`。

- [ ] 写失败测试，覆盖 profile/team/bundle/identity 一致、目标间隔离、SecretRef 和参数脱敏。
- [ ] 运行 `python -m pytest tests/package/ios/test_signing.py -q`，预期 `IosSigningPlanner` 不存在。
- [ ] 实现签名匹配与 ExportOptions 数据模型。
- [ ] 重跑同一命令，预期不匹配组合全部拒绝。
- [ ] 运行中文文档检查。

## 3. 导出、构建和产物

### Task 8：Unity iOS 工程导出

**Files:** Modify `src/st/build/package/unity_export.py`; Test `tests/package/ios/test_unity_export.py`。

- [ ] 写失败测试，覆盖 iOS BuildPlatform、Xcode 输出根、ReleaseBundle 入口和 project-only。
- [ ] 运行 `python -m pytest tests/package/ios/test_unity_export.py -q`，预期 iOS operation 不受支持。
- [ ] 增加 iOS 类型化 operation，不修改 Android 分支行为。
- [ ] 运行 `python -m pytest tests/package/ios/test_unity_export.py tests/package/test_unity_export.py -q`，预期 iOS 与共享回归都通过。
- [ ] 运行中文文档检查。

### Task 9：archive

**Files:** Create `src/st/build/package/platforms/ios/archive.py`; Test `tests/package/ios/test_archive.py`。

- [ ] 写失败测试，覆盖 workspace/scheme/configuration、result bundle、非零退出、超时和取消。
- [ ] 运行 `python -m pytest tests/package/ios/test_archive.py -q`，预期 `IosArchiveBuilder` 不存在。
- [ ] 实现参数序列化的 `xcodebuild archive` 调用。
- [ ] 重跑同一命令，预期假 xcodebuild 通过。
- [ ] 运行中文文档检查。

### Task 10：IPA export

**Files:** Create `src/st/build/package/platforms/ios/export.py`; Test `tests/package/ios/test_export.py`。

- [ ] 写失败测试，覆盖每个 IosExportTarget、ExportOptions、输出发现、重复 IPA 和部分失败。
- [ ] 运行 `python -m pytest tests/package/ios/test_export.py -q`，预期 `IosIpaExporter` 不存在。
- [ ] 实现 `xcodebuild -exportArchive` 调用，每个目标独立目录。
- [ ] 重跑同一命令，预期假工具通过。
- [ ] 运行中文文档检查。

### Task 11：dSYM

**Files:** Create `src/st/build/package/platforms/ios/symbols.py`; Test `tests/package/ios/test_symbols.py`。

- [ ] 写失败测试，覆盖 archive dSYMs、UUID、缺符号、重复符号和确定性 archive。
- [ ] 运行 `python -m pytest tests/package/ios/test_symbols.py -q`，预期 `IosSymbolCollector` 不存在。
- [ ] 实现 dSYM 收集、UUID 报告和 Blob 提交。
- [ ] 重跑同一命令，预期 Golden 通过。
- [ ] 运行中文文档检查。

### Task 12：IPA 验证

**Files:** Create `src/st/build/package/platforms/ios/validator.py`; Test `tests/package/ios/test_validator.py`。

- [ ] 写失败测试，覆盖 zip 安全、Payload 结构、bundle/version、codesign、profile、架构、资源入口和 dSYM UUID。
- [ ] 运行 `python -m pytest tests/package/ios/test_validator.py -q`，预期 `IosPackageValidator` 不存在。
- [ ] 实现类型化验证报告；外部签名检查经 ProcessRunner。
- [ ] 重跑同一命令，预期损坏 fixture 全拒绝。
- [ ] 运行中文文档检查。

## 4. 组合和验收

### Task 13：自动化门禁

- [ ] 运行 `python -m pytest tests/package/ios tests/package tests/quality/test_chinese_documentation.py -q`。
- [ ] 运行 `python -m pytest --cov=st.build.package.platforms.ios --cov-report=term-missing --cov-fail-under=90 tests/package/ios`。
- [ ] 运行 `python -m pytest -q`、`python -m ruff format --check .`、`python -m ruff check .`、`python -m pyright` 和 `python -m compileall -q src tests`，全部退出码 0。

### Task 14：真实 iOS 平台验收

**Files:** Create `tests/integration/package/test_ios_package_probe.py`, `readme/evidence/package/iOS包体验证.md`。

- [ ] 在受控 macOS 节点运行 `python -m pytest tests/integration/package/test_ios_package_probe.py -q --run-external`。
- [ ] 探针生成 distribution 测试 IPA/dSYM，通过共享 PackageUploader 上传隔离前缀并验证 UploadReceipt。
- [ ] 验证签名、bundle ID、版本、架构、ReleaseBundle 入口、安装和冷启动。
- [ ] 记录 macOS/Xcode/Unity、源码 revision、PackageManifest ID、产物 SHA256、设备和退出码。
- [ ] 环境缺失或失败时保持 `PENDING`，不得用 Windows 假工具替代。

## 5. 后续入口

计划 28 在已验证基准 IPA 上实现 SDK、加固和重签扩展；计划 31 负责与旧隔离流程双跑、灰度和回滚证据。
