# iOS 客户端打包设计

## 1. 状态与目标

- 文档状态：**设计完成，独立审查通过**。
- 代码状态：**规划中**。
- 目标：在受控 macOS 节点从固定输入生成 Xcode archive、IPA、dSYM 和 PackageManifest。
- 非目标：不在 Windows 模拟真实签名，不迁移旧脚本中的明文 API key，不把加固/AOC 混入基础 builder。

## 2. 只读参考

只读参考 `package/build_package.py`、`ready_build_project.py`、`build_package_xcode_build.py`、`merge_info_plist.py`、Ruby xcodeproj 工具和 `tool/xcode_build.sh`。旧流程包含 Unity Xcode 导出、Info.plist/工程修改、多种 provisioning profile、archive/export、IPA 重签、dSYM 和上传。

## 3. 模块结构

```text
src/package/platforms/ios/
  model.py            # configuration/export target
  plist.py            # 结构化 plist 变换
  provisioning.py     # profile 元数据与 SecretRef
  xcode_project.py    # 工程变换计划
  xcode_apply.py      # 在隔离工程应用变换计划
  keychain.py         # 临时 keychain 租约和清理
  signing.py          # identity/profile/entitlements 计划
  archive.py          # xcodebuild archive
  export.py           # exportArchive -> IPA
  symbols.py          # dSYM 收集
  validator.py        # IPA/签名/bundle/version/resource 校验
```

## 4. 输入、输出和凭据

`PackageRequest` 复用计划 23 的共享模型。iOS 选项包含 bundle ID、configuration、export method、目标 profile SecretRef、team reference、entitlements profile 和是否只导出 Xcode 工程。

输出包括 Xcode archive（按策略保留）、IPA、dSYM archive、验证报告和 PackageManifest。Manifest 只记录 profile UUID/证书指纹等公开摘要，不记录 profile 原文、私钥、API key 或密码。

```python
@dataclass(frozen=True, slots=True)
class IosPackageOptions:
    bundle_id: str
    configuration: str
    export_targets: tuple[IosExportTarget, ...]
    team_reference: str
    profile_refs: tuple[tuple[IosExportTarget, SecretRef], ...]
    certificate_refs: tuple[tuple[IosExportTarget, SecretRef], ...]
    private_key_refs: tuple[tuple[IosExportTarget, SecretRef], ...]
    project_only: bool
```

profile、签名证书和私钥分别通过 SecretRef 接入；证书指纹可写入验证报告，证书/私钥原文不可写入日志或 PackageManifest。模型校验抛 `ConfigurationError`，外部工具失败抛 `ToolExecutionError`，IPA/签名/资源入口错误抛 `ArtifactValidationError`。所有错误包含目标和日志 locator，但不含凭据。

## 5. 数据流

```text
PackageRequest + Verified ReleaseBundle
  -> isolated Unity iOS export
  -> plist/xcode project/entitlements plans
  -> profile and signing identity validation
  -> xcodebuild archive
  -> xcodebuild -exportArchive
  -> dSYM collection
  -> IPA/signature/resource validation
  -> PackageManifest
  -> optional controlled upload
```

Dev、Distribution、In-House 等目标是明确的 `IosExportTarget` 集合；每个目标独立产出和验证，不能通过文件名隐式推断。基础流水线先支持标准 export method；加固和再次签名由计划 28 扩展。

## 6. 不变量和错误

- 真实执行必须是受控 macOS、固定 Xcode/Unity 版本；
- bundle ID、team、profile application identifier 和 entitlements 必须一致；
- plist 使用结构化解析，不做任意字符串替换；
- archive 和 export 的命令参数序列化并脱敏；
- 任一目标失败不能覆盖其他目标的已验证产物；
- 上传/商店验证是 PackageManifest 后的独立副作用；
- IPA 失败不改变 ReleaseBundle 激活状态。

| 症状 | 首查证据 |
| --- | --- |
| profile 不匹配 | UUID、application identifier、team、bundle ID |
| archive 失败 | xcodebuild 退出码、result bundle、日志 locator |
| export 失败 | ExportOptions 摘要、profile mapping、archive identity |
| dSYM 缺失 | archive products、UUID 映射、符号收集报告 |
| IPA 资源入口错误 | PackageRequest.release_bundle_id、包内 appconfig |

## 7. 幂等、超时、取消、并发与缓存

- Unity/xcodebuild/security/codesign/plutil 均有明确超时并响应 CancellationToken；工具失败不自动重试；
- 每个 package ID 使用隔离 workspace，每个 export target 使用独立输出目录；签名 keychain 采用独占临时租约；
- plist/Xcode plan 必须可重复应用且工程树哈希稳定；冲突设置失败；
- request identity、Unity/Xcode 版本和工程树一致时可复用已验证 archive；IPA export 和签名仍重新验证；
- 取消或异常必须锁定并删除临时 keychain/解密材料；日志和 result bundle 可按策略保留；
- 受控上传按内容键幂等，临时网络错误有限重试，商店发布不属于基础流程。

## 8. 测试、证据和后续入口

测试分为模型/plan 单元测试、假 xcodebuild/security 集成测试、受控 macOS 构建探针和真实 iOS 安装/启动验收。Linux/Windows 不能替代真实签名。

自动化证据写入 `readme/evidence/package/iOS自动化门禁.md`，平台证据写入 `readme/evidence/package/iOS包体验证.md`。计划 26 负责 application 用例；计划 28 负责 SDK、加固和重签扩展；计划 31 负责迁移。真实证据未关闭前保持 iOS 平台验收 `PENDING`。
