# Windows 客户端打包设计与实施计划

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` 或
> `superpowers:executing-plans` 逐任务执行；每个 Task 独立完成 RED、GREEN 和回归。

**Goal:** 在受控 Windows 节点从固定源码与已验证 ReleaseBundle 生成可审计 Player、便携包、可选安装器、符号和 PackageManifest。

**Architecture:** 复用计划 23 的共享 package 模型、工程准备、Unity export、SDK hook 和上传器；Windows 子包只提供模型、布局、签名、安装器与验证组件。跨阶段组合仍由计划 26 application 完成。

**Tech Stack:** Python 3.10、pytest、Unity Windows Player、Authenticode/signtool、可固定版本的安装器工具、计划 18 ProcessRunner/SecretProvider/ObjectStore。

---

## 1. 状态、目标与只读参考

- 文档状态：**设计与实施计划完成，独立审查通过**。
- 代码状态：**纯 Python 模型、布局、配置、inventory、签名计划和便携归档已实现；真实 Windows
  工具链仍为 PENDING**。
- 只读参考：`package/build_package_win_base_build.py`、`build_package_win_default_build.py`、
  `build_package_win_cmge_build.py`、`build_package_win_zilong_build.py`、`package_utils.py` 和
  `tool/windows_sign_tool/`。
- 已确认旧能力包括资源复制、`appconfig.json`、内部文件清单、运行库、Game/Launcher、安装器、
  PFX/UKey 签名、渠道差异和上传。

旧目录中的 PFX、证书、批处理、密码或硬件令牌配置不得复制；不得执行旧签名入口。新实现不从
Jenkins build number 猜资源版本，不通过文件名选择渠道，不把上传、签名和基础构建混为一个类。

非目标：不生成资源 ReleaseBundle、不定义内部文件列表兼容格式、不实现具体渠道 SDK、不支持
未获得真实 fixture 的旧安装器字节复刻。

## 2. 模块结构与数据流

```text
src/package/platforms/windows/
  model.py             # 架构、输出种类、签名与安装器选项
  layout.py            # Player/资源/运行库布局计划
  app_config.py        # 结构化 appconfig 变换
  inventory.py         # 内部 bin/data 清单模型与策略
  portable.py          # 确定性便携归档
  signing.py           # Authenticode 签名计划与执行
  installer.py         # 固定模板安装器构建
  symbols.py           # PDB/符号索引与归档
  validator.py         # 结构、版本、架构、签名和资源入口验证
tools/windows/setup/
  setup.iss            # 固定、版本化安装器模板
```

```text
PackageRequest + Verified ReleaseBundle + WindowsPackageOptions
  -> isolated package workspace
  -> shared Unity Windows export
  -> layout/appconfig/inventory plans
  -> Authenticode sign Player payload
  -> portable archive and/or installer build
  -> Authenticode sign installer executable
  -> symbols + package validation
  -> shared PackageManifest
  -> optional shared PackageUploader
```

`WindowsPackageOptions` 明确 architecture、portable/installer 输出集合、运行库策略、可判别签名配置、
时间戳策略、安装范围和渠道 hook。`BuildPlatform.WINDOWS` 来自唯一 core 枚举。签名配置只能是：

```python
@dataclass(frozen=True, slots=True)
class PfxSigningOptions:
    pfx_ref: SecretRef
    password_ref: SecretRef
    certificate_thumbprint: str

@dataclass(frozen=True, slots=True)
class HardwareTokenSigningOptions:
    provider_name: str
    device_selector: str
    certificate_thumbprint: str
    pin_ref: SecretRef
```

unsigned 是显式测试模式，不允许 production profile 使用。payload 与 installer 使用同一 signer 配置，
但属于两个签名阶段并分别验证。

当前已实现 Task 1-8 的纯 Python 范围（Windows options、Unity export operation、布局/appconfig/
inventory、SHA-256 分阶段签名计划和确定性 portable ZIP）。Task 9-12（固定模板安装器、签名
执行、符号收集、PE/签名/包体验证）以及 Task 15 的真实签名、安装、启动、上传验收仍未实现。

## 3. 不变量、安全、恢复与缓存

- 包体只能引用一个通过 shared release gate 的 ReleaseBundle；appconfig 中的入口必须与请求一致。
- 所有复制均由规范逻辑路径计划驱动，拒绝 `..`、绝对路径、保留设备名、大小写冲突和链接逃逸。
- inventory 是 Windows package 契约，不冒充六字段客户端协议；格式必须先由隔离 fixture Golden 固定。
- Authenticode 仅允许 SHA-256；PFX 文件/密码或 UKey PIN 分别通过 SecretRef 的短期租约进入受控工具，不进入 argv、日志、模板或 PackageManifest。
- PFX 与硬件令牌是两个显式 signer 实现；未配置 signer 时只能产出明确标记的 unsigned 测试包。
- 时间戳服务器使用 allowlist；时间戳临时网络错误可有限重试，签名校验错误不重试。
- 安装器模板固定在仓库中，请求只提供白名单数据；禁止把请求内容当脚本执行。
- workspace 按 package ID 隔离；签名后任何字节变化都必须重新签名和验证。
- 可缓存未签名 Unity Player 与确定性布局；签名产物、时间戳和上传回执不按文件名复用。
- 取消终止 Unity/安装器/签名进程树并清理秘密租约；已验证不可变产物可保留。

## 4. 实施任务

每个“中文文档检查”执行 `python -m pytest tests/quality/test_chinese_documentation.py -q`。

### Task 1：Windows 模型

**Files:** Create `src/package/platforms/windows/__init__.py`, `model.py`; Test `tests/package/windows/test_model.py`。

- [ ] 写失败测试，覆盖 x86_64、输出集合、安装范围、运行库、PFX material/password refs、UKey provider/device/PIN ref、公开 thumbprint、unsigned 测试限制和非法组合。
- [ ] 运行 `python -m pytest tests/package/windows/test_model.py -q`，预期 Windows 模型不存在。
- [ ] 实现不可变 `WindowsPackageOptions`、`PfxSigningOptions`、`HardwareTokenSigningOptions` 及枚举，复用 shared PackageRequest/BuildPlatform。
- [ ] 重跑同一命令，预期退出码 0；运行中文文档检查。

### Task 2：Unity Windows 导出

**Files:** Modify `src/package/unity_export.py`; Test `tests/package/windows/test_unity_export.py`, `tests/package/test_unity_export.py`。

- [ ] 写失败测试，覆盖目标架构、Player 输出、Development 标志、ReleaseBundle 入口、超时和取消。
- [ ] 运行 `python -m pytest tests/package/windows/test_unity_export.py -q`，预期 Windows operation 不受支持。
- [ ] 增加 Windows 类型化 operation，不改变 Android/iOS 映射。
- [ ] 运行上述两个测试文件，预期共享回归与 Windows 均通过；运行中文文档检查。

### Task 3：布局计划与安全应用

**Files:** Create `src/package/platforms/windows/layout.py`; Test `tests/package/windows/test_layout.py`。

- [ ] 写失败测试，覆盖 exe/Data、运行库、资源、重复目标、大小写冲突、链接和路径逃逸。
- [ ] 运行 `python -m pytest tests/package/windows/test_layout.py -q`，预期 `WindowsLayoutPlanner` 不存在。
- [ ] 实现不可变 copy/write/delete plan 与 workspace 内原子应用；不读取可变发布目录。
- [ ] 重跑同一命令，预期源树不变且布局 Golden 稳定；运行中文文档检查。

### Task 4：appconfig 结构化变换

**Files:** Create `src/package/platforms/windows/app_config.py`; Test `tests/package/windows/test_app_config.py`。

- [ ] 写失败测试，覆盖 ReleaseBundle ID/入口、branch、版本、未知键、重复键和确定性 JSON。
- [ ] 运行 `python -m pytest tests/package/windows/test_app_config.py -q`，预期 transformer 不存在。
- [ ] 使用 JSON parser 实现白名单变换和原子写，不做文本替换。
- [ ] 重跑同一命令，预期字节 Golden 通过；运行中文文档检查。

### Task 5：内部 inventory 策略

**Files:** Create `src/package/platforms/windows/inventory.py`; Test `tests/package/windows/test_inventory.py`, `tests/fixtures/windows/inventory/`。

- [ ] 写失败测试，固定隔离旧 fixture 的字段、排序、哈希、大小、版本和目录排除规则，并拒绝重复路径。
- [ ] 运行 `python -m pytest tests/package/windows/test_inventory.py -q`，预期 inventory codec 不存在。
- [ ] 实现版本化 inventory model/writer/parser；没有 fixture 证明的字段保持拒绝，不复用 compatibility writer。
- [ ] 重跑同一命令，预期 Golden 与 round-trip 通过；运行中文文档检查。

### Task 6：可判别签名模型与分阶段计划

**Files:** Create `src/package/platforms/windows/signing.py`; Test `tests/package/windows/test_signing.py`。

- [ ] 写失败测试，覆盖 PFX material/password refs、UKey provider/device/PIN ref、公开 thumbprint、unsigned 测试限制、SHA-256、时间戳 allowlist 和 payload/installer 两阶段顺序。
- [ ] 运行 `python -m pytest tests/package/windows/test_signing.py -q`，预期阶段化签名 planner 不存在。
- [ ] 实现消费 model 中可判别 options 的阶段化计划；只声明租约需求和稳定待签文件顺序。
- [ ] 重跑同一命令，预期非法/不完整组合和 production unsigned 全部拒绝；运行中文文档检查。

### Task 7：Player payload 签名执行与验证

**Files:** Modify `src/package/platforms/windows/signing.py`; Test `tests/package/windows/test_payload_signer.py`。

- [ ] 写失败测试，覆盖 Game/Launcher/DLL 稳定顺序、两种 signer 租约、非零退出、超时、取消、时间戳临时失败、异常清理和逐文件验证。
- [ ] 运行 `python -m pytest tests/package/windows/test_payload_signer.py -q`，预期 payload executor 不存在。
- [ ] 通过 SecretProvider/ProcessRunner 执行 PAYLOAD 阶段并在封装前验证每个签名；秘密不进入命令行。
- [ ] 重跑同一命令，预期假工具通过且 workspace 无秘密残留；运行中文文档检查。

### Task 8：便携归档

**Files:** Create `src/package/platforms/windows/portable.py`; Test `tests/package/windows/test_portable.py`。

- [ ] 写失败测试，要求输入 payload 已全部签名，并覆盖稳定路径顺序、时间戳、权限、空目录策略、重复项和 zip-slip 防护。
- [ ] 运行 `python -m pytest tests/package/windows/test_portable.py -q`，预期 `WindowsPortableBuilder` 不存在。
- [ ] 实现确定性归档并返回 BlobRef/产物摘要；拒绝未签名 production payload。
- [ ] 重跑同一命令，预期相同输入 SHA256 相同；运行中文文档检查。

### Task 9：安装器计划与固定模板

**Files:** Create `src/package/platforms/windows/installer.py`, `tools/windows/setup/setup.iss`; Test `tests/package/windows/test_installer.py`。

- [ ] 写失败测试，要求输入 payload 已签名，并覆盖产品名/版本/publisher、安装范围、文件集合、非法脚本字符、输出发现和重复 exe。
- [ ] 运行 `python -m pytest tests/package/windows/test_installer.py -q`，预期 builder/template 不存在。
- [ ] 实现结构化配置到固定模板参数的映射，并经 ProcessRunner 调用固定版本工具。
- [ ] 重跑同一命令，预期产生唯一未签名 installer，命令 Golden 和恶意输入拒绝用例通过；运行中文文档检查。

### Task 10：installer 签名执行与验证

**Files:** Modify `src/package/platforms/windows/signing.py`; Test `tests/package/windows/test_installer_signer.py`。

- [ ] 写失败测试，覆盖 INSTALLER 阶段、唯一 installer 输入、PFX/UKey 租约、时间戳、取消、异常清理和签名指纹。
- [ ] 运行 `python -m pytest tests/package/windows/test_installer_signer.py -q`，预期 installer executor 不存在。
- [ ] 复用同一阶段化 signer 执行安装器签名并立即验证；禁止重建或修改已签 installer。
- [ ] 重跑同一命令，预期测试 secret 不出现在 argv/repr/log 且异常无残留；运行中文文档检查。

### Task 11：符号收集

**Files:** Create `src/package/platforms/windows/symbols.py`; Test `tests/package/windows/test_symbols.py`。

- [ ] 写失败测试，覆盖 PDB/PE 对应关系、缺失/重复符号、相对路径和确定性 archive。
- [ ] 运行 `python -m pytest tests/package/windows/test_symbols.py -q`，预期 collector 不存在。
- [ ] 实现符号索引、归档和 Blob 提交；符号上传仍由 shared uploader/后续受控系统处理。
- [ ] 重跑同一命令，预期索引与 archive Golden 通过；运行中文文档检查。

### Task 12：包体验证

**Files:** Create `src/package/platforms/windows/validator.py`; Test `tests/package/windows/test_validator.py`。

- [ ] 写失败测试，覆盖 PE 架构、版本资源、payload 签名、installer 签名、portable 内签名保持、Data 目录、appconfig、inventory、运行库和归档安全。
- [ ] 运行 `python -m pytest tests/package/windows/test_validator.py -q`，预期 validator 不存在。
- [ ] 实现类型化验证报告；签名/PE 外部检查只经 ProcessRunner。
- [ ] 重跑同一命令，预期所有损坏 fixture 被拒绝；运行中文文档检查。

### Task 13：SDK hook 与渠道差异契约

**Files:** Modify `src/package/sdk_hooks.py`; Test `tests/package/windows/test_sdk_hooks.py`, `tests/package/test_sdk_hooks.py`。

- [ ] 写失败测试，覆盖 default/cmge/zilong 等 fixture 的声明式 hook 输入输出、冲突和 Launcher 删除计划。
- [ ] 运行 `python -m pytest tests/package/windows/test_sdk_hooks.py -q`，预期 Windows hook target 不受支持。
- [ ] 只增加 Windows hook target 与纯计划聚合；具体 SDK 数据和实现归计划 28。
- [ ] 运行 `python -m pytest tests/package/windows/test_sdk_hooks.py tests/package/test_sdk_hooks.py -q`，预期无 SDK 空计划、冲突用例和共享回归通过；运行中文文档检查。

### Task 14：自动化门禁

- [ ] 运行 `python -m pytest tests/package/windows tests/package tests/quality/test_chinese_documentation.py -q`。
- [ ] 运行 `python -m pytest --cov=package.platforms.windows --cov-report=term-missing --cov-fail-under=90 tests/package/windows`。
- [ ] 运行全量 pytest、Ruff、Pyright 和 compileall，预期全部退出码 0。

### Task 15：真实 Windows 平台验收

**Files:** Create `tests/integration/package/test_windows_package_probe.py`, `readme/evidence/package/Windows包体验证.md`。

- [ ] 在受控 Windows 节点运行
  `python -m pytest tests/integration/package/test_windows_package_probe.py -q --run-external`，生成带已签 payload 的便携包和已签测试安装器。
- [ ] 验证 PE 架构、版本、payload/installer 签名指纹、ReleaseBundle 入口、inventory、安装/卸载和冷启动。
- [ ] 通过 shared PackageUploader 上传隔离前缀并验证 UploadReceipt，记录工具链、revision、PackageManifest ID、SHA256、节点和退出码。
- [ ] 环境缺失或失败时证据保持 `PENDING`，不得标记 Windows 可用。

## 5. 失败排查与完成标准

| 症状 | 首查证据 |
| --- | --- |
| Player 缺文件 | Unity result、layout plan、workspace tree hash |
| appconfig 入口错误 | PackageRequest、Release gate、最终 JSON 摘要 |
| inventory diff | 策略版本、fixture、路径排序和哈希算法 |
| 签名无效 | signer 模式、证书指纹、时间戳、验证报告 |
| 安装器缺运行库 | installer plan、固定模板版本、layout inventory |
| 渠道包差异异常 | SDK hook plan、声明输入输出、冲突报告 |

自动化完成不等于平台可用。真实签名、安装、启动、上传和迁移证据全部关闭后，才能在计划 31
把 Windows 能力标记为迁移验收通过。
