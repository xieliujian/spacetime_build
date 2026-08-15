# Android 客户端打包设计

## 1. 状态与目标

- 文档状态：**设计完成，独立审查通过**。
- 代码状态：**规划中，`package` 尚不存在**。
- 目标：从固定源码、工具链和已验证 ReleaseBundle 生成可审计 APK/AAB、符号与 PackageManifest。
- 非目标：不在本模块构建资源、不生成旧协议、不实现具体渠道 SDK、不直接上传生产商店。

## 2. 只读参考与迁移约束

只读参考 `package/build_package.py`、`ready_build_project.py`、`build_package_gradle_build.py`、`build_package_gradle_aab_build.py` 和 `package_utils.py`。旧流程包含工程准备、Unity Gradle 导出、Manifest/Gradle 修改、SDK 合并、APK/AAB、SO/符号和上传。

以下旧行为不得原样迁移：

- 拼接 shell 命令；
- 从工具目录读取明文 keystore 密码；
- 使用 SHA1/MD5 旧签名算法；
- 把 SDK、加固、上传和基础 Gradle 构建混在一个类中；
- 从可变资源目录推断当前发布版本。

## 3. 模块结构

```text
src/package/
  model.py                 # 公共 PackageRequest/Artifact/状态
  manifest.py              # 确定性 PackageManifest
  release_gate.py          # 已验证 ReleaseBundle 前置校验
  preparation.py           # 隔离 Unity 工程准备
  unity_export.py          # 类型化 Player 导出
  sdk_hooks.py             # 平台 SDK 扩展契约
  uploader.py              # PackageManifest 后的受控上传
  platforms/android/
    model.py               # Android mode/ABI/variant
    gradle_project.py      # Gradle 工程结构检查
    gradle_config.py       # 结构化配置计划
    gradle_apply.py        # 在隔离工程应用配置计划
    manifest.py            # AndroidManifest 变换
    signing.py             # 签名请求与凭据引用
    signer.py              # 安全临时秘密材料与签名执行
    apk.py                 # APK 构建
    aab.py                 # AAB 构建
    symbols.py             # native symbols
    validator.py           # 包体/签名/资源入口校验
```

具体 SDK 位于计划 28 的扩展点，不进入基础 Android builder。

## 4. 输入与产物

```python
@dataclass(frozen=True, slots=True)
class PackageRequest:
    platform: BuildPlatform
    source_revision: str
    release_bundle_id: str
    unity_version: str
    application_id: str
    version_name: str
    version_code: int
    profile: str

@dataclass(frozen=True, slots=True)
class AndroidPackageOptions:
    output_kind: AndroidOutputKind  # APK or AAB
    abis: tuple[AndroidAbi, ...]
    build_type: AndroidBuildType
    signing_key: SecretRef
```

产物至少包括 APK/AAB、native symbols、构建日志和确定性 PackageManifest。Manifest 记录输入 revision、ReleaseBundle ID、Unity/Gradle/JDK/NDK 版本、配置摘要、产物 SHA256/size 和签名证书指纹，不包含秘密、运行耗时或上传 URL。

## 5. 数据流

```text
validated PackageRequest + Verified ReleaseBundle
  -> isolated Unity workspace preparation
  -> Unity Android Gradle export
  -> structured Gradle/Manifest configuration
  -> optional SDK extension plans
  -> APK or AAB build
  -> signing + symbols
  -> package validation
  -> PackageManifest
  -> optional controlled upload
```

包体只引用一个已验证/已激活 ReleaseBundle；资源入口、分支、版本和低清选择必须来自类型化请求，不读取 Jenkins JobName 猜测。

## 6. 模式和不变量

- 基础模式：APK、AAB；project-only 和 native-symbol-only 作为显式请求，不作为环境变量暗门；
- ABI 必须非空、去重并稳定排序；
- application ID、versionCode、versionName 在 Gradle、Manifest 和 PackageManifest 中一致；
- 签名 SecretRef 与证书指纹分离，日志只记录指纹；
- APK 使用当前 Android build-tools 的 `apksigner`，AAB 使用 Gradle signing config；
- 任何后处理都必须重新校验签名和产物 SHA256；
- 上传失败不能改变 PackageManifest，也不能影响已激活资源版本。

## 7. 运行、并发与缓存

- Unity/Gradle/apksigner/bundletool 请求均声明超时和 CancellationToken；工具非零退出不自动重试；
- 对象上传的临时网络失败可由阶段 4 适配器有限重试，签名和构建不自动重试；
- 同一 workspace 只允许一个写租约；Gradle cache 可共享只读内容，但输出目录按 package ID 隔离；
- Unity 导出和 Gradle 中间产物只在 request identity、工具链和目录树哈希一致时复用；最终签名产物不以文件名作为缓存身份；
- 配置 plan 可重复应用并产生相同工程树；发现已有冲突配置时失败，不静默覆盖；
- 取消后终止进程树并清理签名秘密临时文件，诊断日志按策略保留。

## 8. 失败、恢复与排查

| 症状 | 首查证据 |
| --- | --- |
| Unity 导出失败 | UnityBatchRequest、退出码、日志 locator |
| Gradle 依赖错误 | 锁定版本、离线缓存摘要、Gradle log |
| Manifest 冲突 | 变换计划、合并报告、最终 application ID |
| 签名无效 | signer 退出码、证书指纹、APK/AAB validator |
| 包内资源入口错误 | PackageRequest.release_bundle_id、appconfig、包内文件 |
| ABI 缺失 | requested ABI、Gradle outputs、包内 lib 目录 |

自动化恢复可复用已验证的 Unity 导出工程或 Gradle 中间产物，但必须重新核对 request identity 和目录树哈希。签名失败只重做签名/包体阶段，不重跑资源发布。

## 9. 测试、证据和后续入口

- 单元：模型、Gradle/Manifest 变换、签名请求和 validator；
- 集成：假 Unity/Gradle/apksigner/bundletool；
- 平台：受控 Android 节点构建真实 APK/AAB，验证签名、包名、版本、ABI、资源入口、安装和启动；
- 迁移：与旧隔离构建对比功能契约，不要求复现不安全签名字节。

自动化证据写入 `readme/evidence/package/Android自动化门禁.md`，真实包体证据写入 `readme/evidence/package/Android包体验证.md`。计划 26 负责组合 package application 用例；计划 28 实现具体 SDK/加固/IL2CPP 扩展；计划 31 关闭迁移。

真实 APK/AAB 未生成、验证和安装前，只能标记自动化代码完成，不能标记 Android 可用。
