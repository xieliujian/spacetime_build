---
name: spacetime-core-dev
description: >-
  编写、修改或审查 com.spacetime.core（命名空间 ST.Core）框架本身，以及调用它的 Unity C# 代码时使用。
  覆盖项目强制的编码规范（m_/s_ 前缀、camelCase 属性、禁止 ?. 与 UnityEngine.Debug、中文 XML 注释、两行式空值守卫），
  以及框架特有陷阱：单例靠构造函数赋值、异步加载没有取消与失败回调、UIManager 的 DoUpdate 是空的需手动驱动、
  Launch 节点全是空壳、AssetBundle 名等于资源相对路径。Use whenever editing C# under Packages/com.spacetime.core,
  or any code touching NetManager / MsgDispatcher / UIManager / ResourceLoad / TableLoader / Logger / LaunchModule,
  including adding a new manager, panel, launch node, or protocol message.
---

# com.spacetime.core 开发规范与陷阱

这个项目有两条独立的规矩来源，改代码前都要过一遍：

- `Packages/com.spacetime.core/CLAUDE.md` —— 权威的风格规范（命名、注释、访问修饰符）。本技能不重复它写清楚的表格，只补它没说的：**框架的真实完成度与行为约束**。
- 本技能 —— 框架里那些"照文档写会出错"的地方。下面每一条都在当前代码里核对过。

## 一、硬性编码规范

### 命名

| 类别 | 规则 | 示例 |
|---|---|---|
| 私有实例字段 | `m_` + PascalCase | `m_SocketClient`、`m_Writer` |
| 私有/内部静态字段 | `s_` + PascalCase | `s_Instance`、`s_Manager` |
| 属性 | camelCase | `onConnectEvent`、`isOpened` |
| 公共方法 | PascalCase | `DoInit`、`SendConnect` |
| 命名空间 | `ST.Core`，子模块追加后缀 | `ST.Core.Network`、`ST.Core.Logging`、`ST.Core.Table` |
| 常量字段 | `s_` + `k_` 语义前缀，PascalCase | `s_ListConst_100`、`k_SortBase_Bottom` |

### 省略 `private`

默认访问级别不写出来：

```csharp
// 正确
static LogManager s_Manager;
FileLogWriter m_Writer;
void DoSomething() { }

// 错误
private static LogManager s_Manager;
private void DoSomething() { }
```

### 空值守卫：两行式 + 后续空行

守卫必须"条件一行、`return` 缩进一行、不加花括号"，并且 **`return` 之后必须留一个空行**再写后续代码：

```csharp
// 正确
if (m_Manager == null)
    return;

m_Manager.DoSomething();
```

`if (x == null) return;`、`{ return; }`、以及 return 后不留空行三种写法都算违规。

### 禁止 `?.`

一律换成显式 `if`。原因很实际：这个项目的 UI 与资源模块里 `?.` 是**静默失败**的主要来源 —— 目标为 null 时什么都不发生，也没有日志，排查时看不出区别。

```csharp
// 正确
if (m_Manager == null)
    return;

m_Manager.Flush();

// 错误
m_Manager?.Flush();
```

例外仅限被调用方本身就是"可选回调"的场景（如 `onComplete?.Invoke()`），此时 null 是正常语义；但项目里既有代码已经用了 10 处裸 `?.`，**不要顺手批量重构**，只要求新代码不引入。

### 日志：只用 `Logger`

业务代码禁止 `UnityEngine.Debug`：

```csharp
// 正确
Logger.Log("info");
Logger.LogWarning("warning");
Logger.LogError("error");
Logger.LogDebugF("value = {0}", value);   // 格式化版本统一加 F 后缀

// 错误
UnityEngine.Debug.LogWarning("...");
```

`Logger` 的完整 API：`Initialize(LogConfig)` / `SetAllIsLog(bool)` / `EnableUnityLogCapture(bool)` / `Flush()` / `Close()` / `Assert(...)` 以及 `Log` `LogInfo` `LogDebug` `LogWarning` `LogError` `LogException` `LogFatal`（每个带 `*F` 格式化版）。

**但要知道 `Logger` 未初始化时是全静默 no-op**：`Logger.Initialize` 目前只有 `Logging/Examples/LoggingExample.cs` 调用过，所以框架自身大量 `Logger.LogError` 实际只进 Console、不落文件。新增日志前先确认初始化链，别以为写了就一定有文件记录。

### XML 注释：全覆盖，中文

所有类型、方法、字段、属性、枚举值 —— **包括 private 成员** —— 都要中文 XML 注释。注释要说明**用途或约束**，不是复述变量名。

```csharp
/// <summary>日志写入器，负责将格式化后的日志持久化。</summary>
FileLogWriter m_Writer;

/// <summary>
/// 在运行列表中按 uiID 查找最近打开的面板（列表末尾优先）。
/// </summary>
/// <returns>找到的 <see cref="UIPanelActive"/>；未找到时返回 null。</returns>
UIPanelActive FindRunningByUIID(int uiID) { }
```

## 二、框架陷阱（CLAUDE.md 里没有，照文档写会踩）

### 1. 单例靠构造函数赋值，没有懒加载

`NetManager`、`MsgDispatcher`、`UIManager`、`ResourceLoad`、`AsyncTaskManager`、`MainThreadTask` 全部在构造函数里写静态字段（`s_Instance = this` / 基类 `IMainThreadTask()` 里赋值）。

推论：

- **谁最后 `new` 谁生效**，没有幂等保护。永远不要为了"拿个实例"而 `new` 一个管理器。
- **漏 `new` 就是 null**，不会自动创建。所以不要假设 `X.S` / `X.instance` 非空，用前判空。
- `IMainThreadTask.S` 在当前工程里**恒为 null** —— 全项目没有任何一处 `new MainThreadTask()`。`SocketClient` 连接成功时会 `if (MainThreadTask.S != null)` 才投递事件，所以 `NetManager.onConnectEvent` 是永远不会触发的空事件。要接入网络，必须先在某处创建并每帧驱动它。
- `IResourceConfig` / `IResourceConfig` 类配置要走 `SetConfig()`，不是构造函数注入。

### 2. 框架不驱动 `DoUpdate`，谁拥有 MonoBehaviour 谁驱动

`IManager` 只有 4 个抽象方法，**没有任何模块容器或注册表**。`GameApplication` 硬编码持有唯一的 `LaunchModule`，对网络、资源、UI、表、日志零引用。

具体后果：

- `UIManager.DoInit/DoUpdate/DoLateUpdate` 是**空的**，`ResourceLoad.DoUpdate` 也是空的。写了 `DoUpdate` 逻辑的模块（`NetManager`、`AsyncTaskManager`、`MainThreadTask`）**必须由外部每帧调用**，否则队列只进不出。
- 新增一个需要逐帧逻辑的管理器，必须同时想清楚谁驱动它 —— 框架不会替你调。
- 唯一可参考的装配范例是 `Runtime/Scripts/Test/TestUIBoot.cs`（注意它是演示脚本，不是生产代码）。它自己注明了一条硬依赖：**`AsyncTaskManager` 必须在 `ResourceLoad` 之前创建**，因为 `EditorResourceLoad` 会读 `BaseAsyncTaskManager.instance` 且不判空。

### 3. 异步加载没有取消 / 超时 / 失败回调

`BaseResourceLoad.LoadResourceAsync(...)` 返回 `void`，没有句柄。失败时回调收到 `null`，没有 errorCode、没有异常、没有超时。

推论：

- **回调必须判空**：`asset` 为 `null` 是正常失败路径，不是异常。
- **不要销毁可能还被回调引用的对象**。典型错误是在 `LoadResourceAsync` 之后立刻 `Close`/`Destroy`，回调到达时目标已不存在。
- 资源侧的状态机会因一次失败**永久卡死**：`Bundle.LoadBundleSync` 先置 `State.Loaded` 再调 `AssetBundle.LoadFromFile`，失败后状态是"已加载但句柄为 null"，此后不再重试；`AsyncBundleRequest`/`AsyncAssetRequest` 拿到 null 时永远返回 `Running`，任务永久驻留。所以不要写"依赖重试成功"的逻辑。
- 编辑器模式下"异步"其实是同步 —— `EditorAsyncAssetRequest` 在构造函数里就 `AssetDatabase.LoadAssetAtPath`，只是回调被延后到下一帧。所以编辑器里进度条是假的，且大资源会卡主线程。

### 4. UI：关闭时不会取消在飞的加载

`UIPanelActive.Close()` 在 `m_Panel == null`（正在加载）时直接 `return`，请求仍在飞。回调到达后照常 `Instantiate` 出一个活面板，而该 active 已被移出运行列表 —— 结果是**不进任何列表、拿不到 `sortingOrder`、`ClosePanelByPanelID` 找不到、`DoClose()` 也清不掉的幽灵面板**。

同理 `Open()` 只判断 `m_Panel != null`、不判断"正在加载"，所以"打开 → 关闭进缓存 → 加载完成前再打开"会产生**两个实例**。

所以在 UI 代码里：

- 不要写 `OpenPanel(id)` 紧跟 `ClosePanel(id)` 然后假定它已被销毁。
- 改 `UIPanelActive` 时应补"加载中/已关闭"状态判定或代际 token，而不是继续加 `m_Panel != null` 这类判断。
- 面板 Prefab 加载失败、`UIData` 未注册、缺 `AbstractPanel` 组件三种情况都**不会**把 active 移出运行列表，`IsOpened` 会永远返回 true。

### 5. UI：几个"机制"只有半边，需要业务自己补

- **`HideMask` 不产生任何渲染效果。** `ApplyVisible` 只回调 `OnVisibleChanged`，基类是空实现，框架从不改 `Canvas.enabled` / `gameObject.SetActive` / `CanvasGroup.alpha`。想做遮挡优化必须自己在面板子类里实现 `OnVisibleChanged`。
- **`CanvasGroup` 是必需组件但没写进文档。** `UnInteractive` 靠 `m_Panel.GetComponent<CanvasGroup>()`，没有就静默失效（不 `AddComponent`、不告警）。
- **嵌套 Canvas 的 `sortingOrder` 需要 `Canvas.overrideSorting = true` 才生效**，而全仓库只有注释提到它，代码从不设置。做面板 Prefab 时要自己加上，否则三层排序可能退化成实例化顺序。
- **排序分层的每层硬上限是 100 个面板**（`k_SortBase_Bottom/Auto/Top` = 0/100/200），超出会跨层撞号，代码不校验。
- `UIData.path` 的注释写"末尾不含斜杠"，但拼接是裸串接 `path + filename`，**实际必须带尾斜杠**（现有注册全部写 `"ui/uiprefab/"`）。注释是错的。
- `UIData.type` 是死字段，从未被读取；运行时类型靠 `GetComponent<AbstractPanel>()` 反查。
- `AbstractPanel.PlayOpenAnimation` 从不被调用（只有 Page 会走）；`PanelCloseTween.Scale/Fade` 有枚举值无实现。
- `UIDataTable` 是静态类，`UIManager.DoClose()` **不会**清它；`UIPanelActive.s_PanelIDCounter` 也永不重置，所以 `DoClose()` + `DoInit()` 无法回到初始状态。

### 6. AssetBundle 模式的约定与坑

- **bundle 名 == 资源相对路径去掉扩展名**，例如 `Assets/Package/prefabs/character/lady.prefab` → `prefabs/character/lady`，落盘 `prefabs/character/lady.unity3d`。运行时的 key 由调用方拼 `path + filename + bundleSuffix`。
- **AB 模式下 `suffix` 参数被完全忽略**，调用方必须保证 path/filename 与 bundle 名一致。
- **`appName` 必须全小写**：打包侧统一 `ToLower()`，运行时用原样字符串。写 `"MyGame"` 会导致打包产物在 `BuildRes/mygame/` 而运行时去 `BuildRes/MyGame/` 找。
- **`bundleSuffix`（配置）与 Packager 里的 variant 字面量 `"unity3d"` 是两处独立硬编码**，改一处不改另一处会静默失配。
- **索引文件 `assetbundledb.txt` 带 UTF-8 BOM**（`Packager` 用 `Encoding.UTF8`），而 `AssetBundleDBMgr` 读取时不剥 BOM —— 索引 0 的 bundle 名带上了 `\uFEFF`，运行时查不到且**静默返回 null 无任何日志**。改打包或读取任一侧时注意。
- **Android 上整个资源模块不可用**：`AssetBundleDBMgr` 用 `File.Exists`/`File.ReadAllBytes` 读 `streamingAssetsPath`，`Bundle` 用 `AssetBundle.LoadFromFile`，从不构造 `jar:file://...!/assets` 前缀，APK 内路径 `File.Exists` 直接 false → `m_BundleDict` 为空 → 之后所有加载静默 null。
- **卸载只有"全局清空"一档**：`Unload(false)` 硬编码、无引用计数、无 `Resources.UnloadUnusedAssets()`。prefab 一旦加载就常驻内存。
- 资源加载无去重：同一 `(bundle, resname)` 并发 N 次会产生 N 个请求和 N 次回调。编辑器下 `ResourceLoad.useAssetBundle` 是唯一的模式开关。

### 7. 网络：默认 Protobuf，未注册的消息被静默丢弃

- `MsgDispatcher.m_MsgType` 默认 `Protobuf`，`RegisterMsgType` 在整个工程**零调用点**，所以 `RegisterFBMsg` 注册的处理器永远不会触发。
- **未注册的 msgid 被静默丢弃，没有 "unhandled" 日志。** 新协议消息必须 `RegisterPBMsg<T>(handler)`，否则表现为"服务端发了、客户端毫无反应"。
- 注册靠反射取生成类的 `HashID` 常量字段，类型上没有该字段会直接 NRE。
- 帧格式 `[uint16 长度][uint64 msgid][payload]`，小端，长度字段是 `8 + payload.Length`，UInt16，**payload ≥ 65528 字节会静默回绕**成错误长度并让整条流错位。
- 主线程队列 `NetManager.m_EventQueue` **只在生产端加锁、消费端 `Count`/`Dequeue` 无锁**，`Queue<T>` 并发下会丢消息或抛 `IndexOutOfRangeException`。改网络代码时不要假设它线程安全。
- `ProtobufProcFun.Invoke` 的 `ParseFrom` 在 try **之外**，畸形包会抛穿 `NetManager.DoUpdate` 打断整帧消息泵（业务回调反而有 try/catch，保护优先级搞反了）。改这里时应把解析纳入 try。
- 无重连、无心跳、无超时。`SocketClient` 设的 `SendTimeout/ReceiveTimeout` 对 `BeginRead/BeginWrite` 无效（只作用于同步 API），所以连一个不回 ACK 的服务器可以无限挂起。`OnConnect` 不调用 `EndConnect`，因此**连接失败永远拿不到异常，且失败时照样投递"连接成功"事件**。
- `NetManager.DoClose` 先 `RemoveAllListeners()` 再 `Invoke()`，意图是通知关闭但写反了，该 Invoke 是空操作。

### 8. Launch 节点树全是空壳，且跑完就没有帧驱动

- `LaunchModule` 构建 21 个节点的标准启动流程，但 **20 个具体节点全部是 15 行的 `OnStart() { Complete(); }` 占位**。不要依赖任何节点的行为，要加真实逻辑就在 `Runtime/Scripts/Launch/Nodes/` 对应文件里实现。
- **`LaunchModule` 进入终态（`Succeeded`/`Failed`/`Stopped`）后，`GameApplication.DoUpdate` 立即 return，整个游戏不再有任何帧驱动。** 只在 Launch 树里放启动逻辑是够的，但业务循环必须另有人驱动。
- 调度器本身（`LaunchNode`/`SequenceNode`/`ParallelNode`）写得干净、纯 C#、有 841 行单测覆盖，是包内质量最高的部分 —— 改它们时保持"异常边界只在 `Start`/`DoUpdate`/`Stop` 三处"的约束，并同步补 `Tests/Editor/` 下的测试。
- `ParallelNode` 的子节点若被 Stop，`OnUpdate` 会把它当成失败处理（`child.error` 为空则用默认信息）。

### 9. 数据表是只读的

- STBL 格式**只有 reader，没有 writer**，全工程无导出侧代码。改格式要同步两边（目前不存在另一边）。
- 只能**顺序解析**：`StblReader` 算出首行后丢弃行偏移表，无法随机访问/懒加载。
- 头部的 `version` / `flags` / `fieldCount` 三个字段**声明了但从不校验**；schema 段（字段类型信息）完全没被 C# 读取。
- 行内编码：int 走 zigzag varint，float/double 是**裸小端 float32**（不是 varint），string 是 `u16 长度 + UTF-8`。
- `StblReader` 硬编码假设"偏移表长度 = rowCount × 4 且 offsets[0] = rowCount × 4"，不读 `offsets[0]`。导出器加对齐 padding 就会静默错位。
- 全链路零边界检查，损坏表给的是 `IndexOutOfRangeException` 之类的框架报错，不指向具体行列。
- 生成代码的 txt 解析路径有区域性 Bug：`TD_*.ParseFromTxt` 用 `float.Parse` / `int.Parse` **不指定 `CultureInfo.InvariantCulture`**，逗号小数点区域下会误解析。

### 10. 几个跨模块的通用约定

- `ByteBuffer` 有**两个同名类**（`ST.Core.ByteBuffer` 与 `Google.FlatBuffers.ByteBuffer`）。`MsgDispatcher` 用裸名 `new ByteBuffer()`，靠命名空间优先级解析 —— 在该文件里加 `using FlatBuffers;` 会静默换掉实现。
- `ST.Core.ByteBuffer` 字节序是混的：`WriteInt/WriteShort/WriteLong` 走 `BinaryWriter`（小端），而 `WriteFloat/WriteDouble` 先 `Array.Reverse`（大端）。类注释承认了但没说清，别想当然。
- 命名语义错误的历史包袱：`IMsgType` 是**枚举**却带 `I` 前缀；`IProtobufProcFun` / `IFlatBufferProcFun` 是**类**却用接口命名（能被 `new`）。别照着它们猜"这是个接口"。
- `Logger` 未初始化时 `EnableUnityLogCapture` / `Flush` / `Close` 全部静默 no-op，不报错。
- `Logger.Initialize` **重复调用会泄漏文件句柄并使后续写入永久失败**（旧 `FileLogWriter` 不 Close，而 `File.Open` 是 `FileShare.None`）。别在反复进入 Play 模式的路径里调它。
- 日志从**子线程调用不会进文件**：`LogManager` 订阅的是 `Application.logMessageReceived`（仅主线程），而网络层全部在线程池线程打日志。所以网络相关日志只进 Console。

## 三、动手之前

改这个框架前，先确认你要改的模块**真实完成度**，而不是它文档里宣称的完成度：

```text
Launch 调度器   ✅ 完整，有单测      Launch 具体节点  ❌ 20/20 空壳
Table 读取端    ✅ 可用              STBL 写入端      ❌ 不存在
Logging 门面    ✅ 可用              轮转/回调/线程安全 ❌ 有缺陷
Network         ⚠️ 帧格式对，无重连/心跳/超时，handler 注册零调用
ResourceLoad    ⚠️ 编辑器模式可跑，失败路径全缺，Android 不可用
UI              ⚠️ 骨架完整，HideMask/排序/取消机制半边未实现
Diagnostics     ✅ 可用              未接入任何场景
```

对应的文档落差（**不要照着这些文档写代码**）：

- `readme/Table/Table.md` 只有 20 行，完全没记录 STBL 二进制格式。唯一准确描述在 `StblReader.cs` 的注释里。
- `readme/Logging/Logging.md` 教用户实现一个**仓库里不存在**的 `ILogConfig` 接口，示例无法编译。
- `readme/Diagnostics/Diagnostics.md` 说真机需要 `ENABLE_PROFILER` 宏，代码判断的是 `DEVELOPMENT_BUILD`。
- `readme/UI/UI.md` 宣称 `HideMask` 能减少 Overdraw、三层排序可用 —— 见上面第 5 条，需要业务补实现。
- `package.json` 声明 `unity: 2020.3`，实际 README 要求 2022.3.35f1c1。

## 四、参考

改具体模块前读 `references/framework-traps.md` —— 里面有每条陷阱的 `文件:行号` 证据和可复现路径，用于确认为什么不能按直觉写。

改完自查：

- [ ] 命名符合 `m_` / `s_` / camelCase 属性规则，没写多余的 `private`
- [ ] 没有新增 `?.`；空值守卫是两行式且 return 后有空行
- [ ] 日志走 `Logger`，没有新增 `UnityEngine.Debug`
- [ ] 新增/修改的类型、方法、字段、枚举值都有中文 XML 注释
- [ ] 如果新增了需要逐帧驱动的模块，想清楚谁调它的 `DoUpdate`
- [ ] 如果改了 `Launch` 节点或调度器，同步更新 `Tests/Editor/` 下对应测试
- [ ] 新代码不依赖"框架会替我取消异步 / 处理失败 / 驱动更新"
