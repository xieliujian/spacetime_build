# Android 客户端打包 Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 执行。每个 Task 独立完成 RED、GREEN 和回归。

**Goal:** 实现共享 package 基础和 Android APK/AAB 构建、签名、符号与验证。

**Architecture:** 共享模型与 Android 平台模块分离；Unity、Gradle、签名和上传均经阶段 4 端口；SDK 使用计划 28 扩展点。

**Tech Stack:** Python 3.10、pytest、Unity batchmode、Gradle、Android build-tools、bundletool。

---

## 1. 状态与共同命令

- 文档状态：**实施计划完成，独立审查通过**。
- 代码状态：共享 PackageRequest/Manifest/ReleaseGate、隔离准备、Unity 导出计划、
  Android 模型、Gradle 工程/配置计划、Manifest 变换、SDK hook、签名计划和受控包体
  上传的**纯 Python 自动化代码已实现并通过目标测试**；真实工具执行与平台验收仍为
  `PENDING`。
- 本文每个“中文文档检查”步骤执行 `python -m pytest tests/quality/test_chinese_documentation.py -q`。
- 未经用户明确要求不创建 commit。

## 2. 共享 package 基础

### Task 1：PackageRequest 与执行状态

**Files:** Create `src/package/__init__.py`, `model.py`; Test `tests/package/test_model.py`。

- [ ] 写失败测试，覆盖唯一 BuildPlatform、固定 revision、ReleaseBundle ID、版本、profile、状态和非法空值。
- [ ] 运行 `python -m pytest tests/package/test_model.py -q`，预期 `package.model` 不存在。
- [x] 实现不可变 PackageRequest、PackageArtifact、PackageStatus 和 PackageExecutionRecord。
- [ ] 重跑同一命令，预期通过。
- [ ] 运行中文文档检查。

### Task 2：确定性 PackageManifest

**Files:** Create `src/package/manifest.py`; Test `tests/package/test_manifest.py`。

- [ ] 写失败测试，覆盖 payload/factory、工具链、release ID、产物摘要、秘密/运行状态排除和陈旧 ID。
- [ ] 运行 `python -m pytest tests/package/test_manifest.py -q`，预期 `PackageManifestFactory` 不存在。
- [x] 实现不可变 manifest 与严格 JSON codec。
- [ ] 重跑同一命令，预期相同 payload 得到相同 ID。
- [ ] 运行中文文档检查。

### Task 3：ReleaseBundle 前置验证

**Files:** Create `src/package/release_gate.py`; Test `tests/package/test_release_gate.py`。

- [ ] 写失败测试，覆盖未验证 bundle、平台/变体不符、陈旧入口和允许的已激活/已验证状态。
- [ ] 运行 `python -m pytest tests/package/test_release_gate.py -q`，预期 `PackageReleaseGate` 不存在。
- [x] 实现只读 gate，不修改 ReleaseBundle 或激活状态。
- [ ] 重跑同一命令，预期通过。
- [ ] 运行中文文档检查。

### Task 4：隔离工程准备

**Files:** Create `src/package/preparation.py`; Test `tests/package/test_preparation.py`。

- [ ] 写失败测试，覆盖 source snapshot、workspace、SDK hook 输入、StreamingAssets 入口和源目录零修改。
- [ ] 运行 `python -m pytest tests/package/test_preparation.py -q`，预期 `PackageWorkspacePreparer` 不存在。
- [x] 实现平台无关准备计划，只在 WorkspaceLease 内复制/变换。
- [ ] 重跑同一命令，预期通过并保持源树哈希。
- [ ] 运行中文文档检查。

### Task 5：Unity Player 导出

**Files:** Create `src/package/unity_export.py`; Test `tests/package/test_unity_export.py`。

- [ ] 写失败测试，覆盖 BuildPlatform、Unity 版本、项目路径、输出根和 build setting 映射。
- [ ] 运行 `python -m pytest tests/package/test_unity_export.py -q`，预期 `UnityPlayerExporter` 不存在。
- [x] 实现类型化 UnityBatchRequest 生成和结果验证。
- [ ] 重跑同一命令，预期假 Unity 通过。
- [ ] 运行中文文档检查。

## 3. Android 配置

### Task 6：Android 模型

**Files:** Create `src/package/platforms/__init__.py`, `android/__init__.py`, `android/model.py`; Test `tests/package/android/test_model.py`。

- [ ] 写失败测试，覆盖 APK/AAB、ABI 去重/排序、build type、application ID 和 versionCode。
- [ ] 运行 `python -m pytest tests/package/android/test_model.py -q`，预期 Android 模型不存在。
- [x] 实现不可变 AndroidPackageOptions、AndroidAbi 和 AndroidOutputKind。
- [ ] 重跑同一命令，预期通过。
- [ ] 运行中文文档检查。

### Task 7：Gradle 工程检查

**Files:** Create `src/package/platforms/android/gradle_project.py`; Test `tests/package/android/test_gradle_project.py`。

- [ ] 写失败测试，覆盖 launcher/unityLibrary、wrapper、settings、build files 和路径逃逸。
- [ ] 运行 `python -m pytest tests/package/android/test_gradle_project.py -q`，预期 `GradleProjectInspector` 不存在。
- [x] 实现只读结构检查和类型化结果。
- [ ] 重跑同一命令，预期通过。
- [ ] 运行中文文档检查。

### Task 8：Gradle 配置计划

**Files:** Create `src/package/platforms/android/gradle_config.py`; Test `tests/package/android/test_gradle_config.py`。

- [ ] 写失败测试，覆盖 application ID、版本、ABI、仓库白名单、离线锁和重复配置。
- [ ] 运行 `python -m pytest tests/package/android/test_gradle_config.py -q`，预期 `GradleConfigurationPlanner` 不存在。
- [x] 实现结构化变换计划；不在业务层字符串插入脚本。
- [ ] 重跑同一命令，预期 plan 确定且幂等。
- [ ] 运行中文文档检查。

### Task 9：应用 Gradle 配置计划

**Files:** Create `src/package/platforms/android/gradle_apply.py`, `tools/gradle/build_config.gradle`; Test `tests/package/android/test_gradle_apply.py`。

- [ ] 写失败测试，覆盖仅限 workspace、原子写、重复应用、冲突、取消和失败回滚工程树。
- [ ] 运行 `python -m pytest tests/package/android/test_gradle_apply.py -q`，预期 `GradleConfigurationApplier` 不存在。
- [ ] 实现 applier：写入结构化 JSON 请求并通过 ProcessRunner 调用固定 `build_config.gradle`；脚本只处理白名单字段，不执行请求中的代码。
- [ ] 重跑同一命令，预期工程树 Golden 稳定且失败不留半配置。
- [ ] 运行中文文档检查。

### Task 10：AndroidManifest 变换

**Files:** Create `src/package/platforms/android/manifest.py`; Test `tests/package/android/test_manifest.py`。

- [ ] 写失败测试，覆盖 XML namespace、package、version、权限去重、meta-data 冲突和可调试标志。
- [ ] 运行 `python -m pytest tests/package/android/test_manifest.py -q`，预期 `AndroidManifestTransformer` 不存在。
- [x] 使用 XML API 实现确定性变换，不做文本替换。
- [ ] 重跑同一命令，预期字节/结构 Golden 通过。
- [ ] 运行中文文档检查。

### Task 11：SDK 扩展接口

**Files:** Create `src/package/sdk_hooks.py`; Test `tests/package/test_sdk_hooks.py`。

- [ ] 写失败测试，覆盖 hook 顺序、声明输入输出、冲突检测和无 SDK 空计划。
- [ ] 运行 `python -m pytest tests/package/test_sdk_hooks.py -q`，预期 `PackageSdkHook` 不存在。
- [x] 实现 Protocol 和纯计划聚合；具体 SDK 留给计划 28。
- [ ] 重跑同一命令，预期通过。
- [ ] 运行中文文档检查。

## 4. 构建、签名和符号

### Task 12：签名请求

**Files:** Create `src/package/platforms/android/signing.py`; Test `tests/package/android/test_signing.py`。

- [ ] 写失败测试，覆盖 SecretRef、证书指纹、APK/AAB 差异、秘密传递方式、参数脱敏和禁止 SHA1/MD5。
- [ ] 运行 `python -m pytest tests/package/android/test_signing.py -q`，预期 `AndroidSigningPlanner` 不存在。
- [x] 实现签名计划；只声明所需 SecretRef 和受控传递方式，不解析秘密。
- [ ] 重跑同一命令，预期日志不含测试 secret。
- [ ] 运行中文文档检查。

### Task 13：未签名 APK 构建

**Files:** Create `src/package/platforms/android/apk.py`; Test `tests/package/android/test_apk.py`。

- [ ] 写失败测试，覆盖 Gradle task、输出发现、重复 APK、非零退出、超时和取消。
- [ ] 运行 `python -m pytest tests/package/android/test_apk.py -q`，预期 `AndroidApkBuilder` 不存在。
- [ ] 实现通过 ProcessRunner 的 assemble 调用，只返回未签名/待验证 APK。
- [ ] 重跑同一命令，预期假 Gradle 通过。
- [ ] 运行中文文档检查。

### Task 14：签名秘密租约与执行

**Files:** Create `src/package/platforms/android/signer.py`; Test `tests/package/android/test_signer.py`。

- [ ] 写失败测试，覆盖 SecretProvider 租约、受限临时 properties、环境传递、apksigner、异常/取消清理和命令日志脱敏。
- [ ] 运行 `python -m pytest tests/package/android/test_signer.py -q`，预期 `AndroidPackageSigner` 不存在。
- [ ] 实现签名执行器：秘密不进入命令行或仓库 Gradle 文件，临时材料在 finally 中销毁。
- [ ] 重跑同一命令，预期假 apksigner/Gradle 通过且工作区无秘密残留。
- [ ] 运行中文文档检查。

### Task 15：AAB 构建

**Files:** Create `src/package/platforms/android/aab.py`; Test `tests/package/android/test_aab.py`。

- [ ] 写失败测试，覆盖 bundle task、输出发现、签名配置、bundletool 检查和取消。
- [ ] 运行 `python -m pytest tests/package/android/test_aab.py -q`，预期 `AndroidAppBundleBuilder` 不存在。
- [ ] 实现通过 ProcessRunner 的 bundle 构建与验证。
- [ ] 重跑同一命令，预期假 Gradle/bundletool 通过。
- [ ] 运行中文文档检查。

### Task 16：native symbols

**Files:** Create `src/package/platforms/android/symbols.py`; Test `tests/package/android/test_symbols.py`。

- [ ] 写失败测试，覆盖 ABI 目录、libil2cpp.so、mapping、缺符号和确定性 archive。
- [ ] 运行 `python -m pytest tests/package/android/test_symbols.py -q`，预期 `AndroidSymbolCollector` 不存在。
- [ ] 实现符号收集和内容寻址提交。
- [ ] 重跑同一命令，预期 archive Golden 通过。
- [ ] 运行中文文档检查。

### Task 17：包体验证器

**Files:** Create `src/package/platforms/android/validator.py`; Test `tests/package/android/test_validator.py`。

- [ ] 写失败测试，覆盖签名、application ID、版本、ABI、资源入口、重复条目和 zip 安全。
- [ ] 运行 `python -m pytest tests/package/android/test_validator.py -q`，预期 `AndroidPackageValidator` 不存在。
- [ ] 实现 APK/AAB 类型化验证报告。
- [ ] 重跑同一命令，预期损坏 fixture 全部拒绝。
- [ ] 运行中文文档检查。

## 5. 组合和验收

### Task 18：受控包体上传

**Files:** Create `src/package/uploader.py`; Test `tests/package/test_uploader.py`。

- [ ] 写失败测试，覆盖 PackageManifest 前置、内容键、同哈希幂等、异哈希冲突、取消和上传回执。
- [ ] 运行 `python -m pytest tests/package/test_uploader.py -q`，预期 `PackageUploader` 不存在。
- [x] 实现通过 ObjectStore 的可选上传；不修改 PackageManifest，不包含商店发布逻辑。
- [ ] 重跑同一命令，预期通过。
- [ ] 运行中文文档检查。

### Task 19：自动化门禁

- [ ] 运行 `python -m pytest tests/package tests/quality/test_chinese_documentation.py -q`。
- [ ] 运行 `python -m pytest --cov=package --cov-report=term-missing --cov-fail-under=90 tests/package`。
- [ ] 运行 `python -m pytest -q`、`python -m ruff format --check .`、`python -m ruff check .`、`python -m pyright` 和 `python -m compileall -q src tests`，全部退出码 0。

### Task 20：真实 Android 平台验收

**Files:** Create `tests/integration/package/test_android_package_probe.py`, `readme/evidence/package/Android包体验证.md`。

- [ ] 运行 `python -m pytest tests/integration/package/test_android_package_probe.py -q --run-external`，生成测试 APK 和 AAB。
- [ ] 探针验证签名指纹、包名、版本、ABI、ReleaseBundle 入口、安装和冷启动。
- [ ] 记录源码 revision、工具链、PackageManifest ID、产物 SHA256、设备和退出码。
- [ ] 失败或环境缺失时证据保持 `PENDING`，不得标记 Android 可用。

## 6. 后续入口

共享 package 基础供 iOS 计划 25 和 Windows 计划 27 复用；具体渠道 SDK、加固和远端 IL2CPP 在计划 28 实现。
