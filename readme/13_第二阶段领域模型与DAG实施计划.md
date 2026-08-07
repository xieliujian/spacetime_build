# 第二阶段领域模型与 DAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Python 3.10+ 上以严格 RED→GREEN 实现不可变领域模型、确定性 BuildManifest、任务 DAG、可校验恢复 Frontier 和独立 Release 聚合，为下一阶段兼容 DTO 提供唯一上游模型。

**Architecture:** `st.build.core` 按异常、产物、构建记录、manifest 编解码、任务协议、图、规划、恢复和执行拆分；`st.build.release` 按条目、协议无关快照、manifest、bundle、激活记录拆分。领域层保持纯 Python，不导入 SVN、Unity、Jenkins、CDN 或 `compatibility`；文件列表 DTO 只能从 `ReleaseEntry` 转换，AB DTO 只能从已验证的 `ReleaseSnapshot` 转换。

**Tech Stack:** Python 3.10+、不可变 dataclasses、enum、typing.Protocol、pathlib、hashlib、json、pytest、pytest-cov、Ruff、Pyright。

**设计文档:** `readme/00_全新构建系统设计.md`

---

## 阶段状态

状态：**代码实现与自动化质量门禁已完成，中文 docstring 详细度人工审查待记录**。

- Python 3.10.11；
- 完整回归 `51 passed`，无 skip；
- 整体覆盖率 93%，`st.build.core` 93%，`st.build.release` 93%；
- Ruff format、Ruff check、Pyright 和 compileall 全部通过；
- 中文 docstring AST 自动门禁通过，详细度人工审查尚未留下完整记录；
- 本阶段只完成纯 Python 领域内核，不代表兼容协议、外部适配器、资源任务或 CLI 可用。

## 执行前硬门禁

- [x] 运行 `python --version` 记录版本，再运行 `python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"`；第二条命令必须以 exit code 0 对 Python 3.10+ 作非零失败断言，非零时立即停止。
- [x] 运行 `python -m pip install -e ".[dev]"`，确认开发依赖安装成功。
- [x] 运行 `python -m pytest tests/test_package_import.py tests/test_distribution.py -q`，预期 `3 passed`，wheel 测试不得 skip。
- [x] 已确认当前机器为 Python 3.10.11，不触发 Python 3.7 环境下的停止条件。

## 文件边界

**Quality:**

- Create: `tests/quality/__init__.py` — 测试包说明。
- Create: `tests/quality/test_chinese_documentation.py` — AST 中文 docstring 自动门禁及其自测。

**Core domain:**

- Create: `src/st/build/core/__init__.py` — 核心领域公共导出，不包含实现逻辑。
- Create: `src/st/build/core/errors.py` — 完整业务异常体系。
- Create: `src/st/build/core/artifacts.py` — `ArtifactKind`、`ArtifactMetadata`、`BlobRef`、`LogicalArtifact`。
- Create: `src/st/build/core/build_records.py` — `BuildManifestPayload`、不可变 `BuildManifest`、`BuildExecutionRecord` 和构建状态。
- Create: `src/st/build/core/manifest_codec.py` — payload 规范化、`BuildManifestFactory`、ID、读写和完整性校验。
- Create: `src/st/build/core/tasks.py` — 任务 Protocol、规格、身份、计划、结果和执行上下文。
- Create: `src/st/build/core/graph.py` — 已验证的不可变 `BuildGraph`。
- Create: `src/st/build/core/planner.py` — `BuildPlanner` 的图构建和确定性分层。
- Create: `src/st/build/core/frontier.py` — 完成节点证据和恢复资格校验。
- Create: `src/st/build/core/executor.py` — 确定性同步 `TaskExecutor`。

**Release domain:**

- Create: `src/st/build/release/__init__.py` — Release 领域公共导出。
- Create: `src/st/build/release/entries.py` — `ResourceVariant`、`ReleaseEntry` 和对象来源。
- Create: `src/st/build/release/snapshots.py` — 协议无关 `ReleaseSnapshotEntry`、`RedirectSlice`、`ReleaseSnapshot`。
- Create: `src/st/build/release/manifests.py` — `ReleaseManifestPayload` 和不可变 `ReleaseManifest`；从 `entries.py` 导入 `ResourceVariant`。
- Create: `src/st/build/release/manifest_codec.py` — `ReleaseManifestFactory`、规范编解码和严格读取。
- Create: `src/st/build/release/bundles.py` — `ReleaseBundlePayload` 和不可变 `ReleaseBundle`；从 `entries.py` 导入 `ResourceVariant`。
- Create: `src/st/build/release/bundle_codec.py` — `ReleaseBundleFactory`、规范编解码和严格读取。
- Create: `src/st/build/release/activation.py` — `ReleaseActivationRecord` 和激活状态。

**Tests:**

- Create: `tests/core/test_errors.py`
- Create: `tests/core/test_artifacts.py`
- Create: `tests/core/test_build_records.py`
- Create: `tests/core/test_manifest_codec.py`
- Create: `tests/core/test_tasks.py`
- Create: `tests/core/test_graph.py`
- Create: `tests/core/test_planner.py`
- Create: `tests/core/test_frontier.py`
- Create: `tests/core/test_executor.py`
- Create: `tests/release/test_entries.py`
- Create: `tests/release/test_snapshots.py`
- Create: `tests/release/test_manifests.py`
- Create: `tests/release/test_manifest_codec.py`
- Create: `tests/release/test_bundles.py`
- Create: `tests/release/test_bundle_codec.py`
- Create: `tests/release/test_activation.py`

不得恢复旧计划中的 `core/models.py`、`core/manifest.py`、`core/graph.py` 包揽多种职责，或用单个 `release/models.py` 混合条目、快照、bundle 和可变激活状态。

身份方案固定为：应用层先调用每个 `BuildTask.plan(context)` 取得完整 `TaskPlan`；`BuildPlanner.plan(plans, context)` 只接收 TaskPlan 集合；`TaskIdentity` 只提供 `from_plan`。不实现 `BuildPlanner.plan(specs, ...)` 或 `TaskIdentity.from_spec`。

## 每个 Task 的共同门禁

每个 Task 都按以下顺序执行，不得先批量写完生产代码：

1. 只添加当前行为的测试和必要 fixture；
2. 运行列出的 pytest node，确认与“确定 RED”完全一致；禁止使用含糊失败、依赖实现选择的失败原因或已被前一步 GREEN 覆盖的测试；
3. 只写使该测试通过的最小生产代码和详细中文 docstring；
4. 重跑单个 node，确认 GREEN；
5. 运行当前 Task 的回归命令，其中必须包含 `tests/quality/test_chinese_documentation.py`；
6. 人工审查新增 docstring 是否说明职责、参数、返回值、异常、约束和副作用。

所有测试模块、测试类、fixture 函数、辅助函数和测试函数自身也必须有中文 docstring。配置和 Markdown 可在测试前创建；任何 `src/**/*.py` 生产实现必须坚持 RED→GREEN。

## Task 1：把中文 docstring 检查提前为首个质量门

**Files:**

- Create: `tests/quality/__init__.py`
- Create: `tests/quality/test_chinese_documentation.py`

- [x] **Step 1：在同一次首次 RED 中写 scanner 与全仓门禁测试**

行为 A：

- pytest：`tests/quality/test_chinese_documentation.py::test_scanner_reports_module_class_function_and_method_without_chinese_docstrings`
- 目标 API：测试模块内部 `_find_chinese_documentation_violations(paths: tuple[Path, ...])`
- 断言：临时 Python 文件中缺失中文 docstring 的模块、类、函数和方法均以“文件、符号、行号”报告。
- 确定 RED：先只写两个测试而不定义 helper；本测试在调用 `_find_chinese_documentation_violations` 时以 `NameError` 失败。
- 最小 GREEN：用 `ast.parse`、`ast.get_docstring` 和中文字符正则遍历 `Module`、`ClassDef`、`FunctionDef`、`AsyncFunctionDef`；嵌套方法不能漏检。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py -q`

行为 B：

- pytest：`tests/quality/test_chinese_documentation.py::test_all_source_and_test_symbols_have_chinese_docstrings`
- 目标 API：同一 scanner，扫描 `src/**/*.py` 和 `tests/**/*.py`
- 断言：模块、类、函数、异步函数和方法均有非空且至少包含一个中文字符的 docstring。
- 确定 RED：与行为 A 同时添加且在 helper 实现前一起运行；同样以 `_find_chinese_documentation_violations` 的 `NameError` 失败。不得在行为 A GREEN 后才新增本测试。
- 最小 GREEN：补齐 scanner 自身与现存测试的中文 docstring，只修文档缺口，不顺带实现领域能力。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/test_package_import.py tests/test_distribution.py -q`

- [ ] **Step 2：人工质量门**

自动测试只能证明“存在且含中文”，不能证明详细度。人工逐符号确认职责、参数、返回值、异常、约束和副作用；无参数、无返回或无副作用时也要明确说明。该人工审查在后续每个 Task 重复执行。

## Task 2：实现完整异常体系

**Files:**

- Create: `src/st/build/core/__init__.py`
- Create: `src/st/build/core/errors.py`
- Create: `tests/core/test_errors.py`

- [x] **Step 1：RED→GREEN 完成异常继承契约**

行为：

- pytest：`tests/core/test_errors.py::test_error_hierarchy_matches_public_contract`
- 目标 API：`BuildError`、`ConfigurationError`、`PlanningError`、`SourceError`、`ToolExecutionError`、`ArtifactValidationError`、`PublishError`、`CompatibilityError`
- 断言：七个具体异常都直接或间接继承 `BuildError`，异常消息和 `__cause__` 可由标准异常机制保留。
- 确定 RED：生产包尚无 `st.build.core`，测试收集时以 `ModuleNotFoundError: No module named 'st.build.core'` 失败。
- 最小 GREEN：只定义设计文档中的完整继承树和中文类 docstring，不添加重试、日志或外部系统逻辑。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_errors.py -q`

## Task 3：实现 ArtifactMetadata 与 BlobRef

**Files:**

- Create: `src/st/build/core/artifacts.py`
- Create: `tests/core/test_artifacts.py`

- [x] **Step 1：RED→GREEN 实现类型化 metadata**

行为：

- pytest：`tests/core/test_artifacts.py::test_artifact_metadata_is_typed_immutable_and_canonicalizable`
- 目标 API：`ArtifactMetadata(source_task, source_revision, toolchain_digest, attributes)`
- 断言：对象不可变；`attributes` 只接受排序后可稳定编码的 `tuple[tuple[str, str], ...]`，拒绝任意 `Mapping[str, object]`、重复 key 和非字符串值。
- 确定 RED：`st.build.core.artifacts` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：实现 `frozen=True, slots=True` dataclass 和构造后校验；不实现 manifest JSON。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_artifacts.py -q`

- [x] **Step 2：RED→GREEN 实现持久 Blob 引用**

行为：

- pytest：`tests/core/test_artifacts.py::test_blob_ref_validates_locator_sha256_and_size`
- 目标 API：`BlobRef(locator, sha256, size)`
- 断言：接受内容寻址 locator、64 位小写十六进制 SHA256 和非负大小；拒绝空 locator、临时工作目录标记、错误哈希和负数。
- 确定 RED：前一步最小 GREEN 只定义 `ArtifactMetadata`，导入 `BlobRef` 时以 `ImportError` 失败。
- 最小 GREEN：增加不可变 dataclass 和字段校验，失败抛 `ArtifactValidationError`。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_artifacts.py -q`

## Task 4：实现 LogicalArtifact 与路径不变量

**Files:**

- Modify: `src/st/build/core/artifacts.py`
- Modify: `tests/core/test_artifacts.py`

- [x] **Step 1：在同一次首次 RED 中实现逻辑路径和集合语义**

行为：

- pytest：`tests/core/test_artifacts.py::test_logical_artifact_validates_paths_and_preserves_collection_semantics`
- 目标 API：`ArtifactKind`、`LogicalArtifact(logical_path, kind, blob, dependencies, subpackage_ids, metadata)`
- 断言：接受 `scene/a.assetbundle`；拒绝绝对路径、`\`、`.`/`..` 段、空段和尾斜杠；输入依赖 `("b", "a", "b")` 原样保留；无序分包集合规范为 `frozenset({1, 2})`。
- 确定 RED：前一步最小 GREEN 只定义 `ArtifactMetadata` 和 `BlobRef`，导入 `ArtifactKind`/`LogicalArtifact` 时以 `ImportError` 失败。
- 最小 GREEN：实现枚举、不可变 dataclass 和 `/` 逻辑路径校验；依赖使用 `tuple[str, ...]` 并只逐项校验，只有语义为无序集合的 `subpackage_ids` 使用 `frozenset`。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_artifacts.py -q`

## Task 5：实现 BuildManifestPayload 与 BuildExecutionRecord

**Files:**

- Create: `src/st/build/core/build_records.py`
- Create: `tests/core/test_build_records.py`

- [x] **Step 1：RED→GREEN 定义独立可复现 payload**

行为：

- pytest：`tests/core/test_build_records.py::test_build_manifest_payload_contains_only_reproducible_content`
- 目标 API：`BuildManifestPayload(schema_version, request_digest, revision, toolchain_digest, baseline_id, artifacts, task_identities)`
- 断言：payload 可表达固定请求、revision、工具链、可选基线、产物和任务身份；类型签名中不存在 `manifest_id`、`build_id`、状态、时间、耗时或日志；对象不可变。
- 确定 RED：`st.build.core.build_records` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：只实现不可变 `BuildManifestPayload` 和字段校验，不定义 `BuildManifest`，不计算 ID。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_build_records.py -q`

- [x] **Step 2：RED→GREEN 分离运行状态**

行为：

- pytest：`tests/core/test_build_records.py::test_build_execution_record_owns_runtime_state_and_rejects_unknown_schema`
- 目标 API：`BuildStatus`、`BuildExecutionRecord(schema_version, build_id, manifest_id, status, started_at, finished_at, log_locator)`
- 断言：运行状态只存在于 execution record；schema 必须等于当前受支持常量，未知版本抛 `ArtifactValidationError`；结束时间不得早于开始时间；进行中记录允许没有 `manifest_id` 和 `finished_at`。
- 确定 RED：前一步最小 GREEN 只定义 payload，导入 `BuildStatus`/`BuildExecutionRecord` 时以 `ImportError` 失败。
- 最小 GREEN：实现独立不可变状态记录及时间关系校验，不实现持久化。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_build_records.py -q`

## Task 6：用工厂实现不可变 BuildManifest 和严格编解码

**Files:**

- Modify: `src/st/build/core/build_records.py`
- Create: `src/st/build/core/manifest_codec.py`
- Create: `tests/core/test_manifest_codec.py`

- [x] **Step 1：RED→GREEN 定义规范 payload**

行为：

- pytest：`tests/core/test_manifest_codec.py::test_payload_codec_normalizes_only_unordered_collections`
- 目标 API：`build_manifest_payload_dict(payload) -> dict[str, object]`、`canonical_json_bytes(value) -> bytes`
- 断言：交换 artifacts、metadata attributes、subpackage IDs 等无序集合的输入顺序不改变字节；依赖 `("b", "a", "b")` 在 JSON 中仍为同序且保留重复；UTF-8、无 BOM、`sort_keys=True`、紧凑分隔符。
- 确定 RED：`st.build.core.manifest_codec` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：只在领域语义声明为无序的边界按 UTF-8 字节键排序；禁止递归地把所有 list/tuple 排序或去重。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_manifest_codec.py -q`

- [x] **Step 2：在同一次首次 RED 中实现工厂和完整 ID 契约**

行为：

- pytest：`tests/core/test_manifest_codec.py::test_manifest_factory_computes_immutable_id_from_payload_only`
- 目标 API：`BuildManifest`、`BuildManifestFactory.create(payload) -> BuildManifest`
- 断言：工厂返回的 manifest 同时持有不可变 payload 和 64 位 ID；ID 精确等于 payload 规范字节 SHA256，因此结构上排除 `manifest_id` 自身；修改 request、revision、toolchain、baseline、schema、task identity、artifact hash 或 ordered dependencies 任一项都会改变 ID；直接调用 `BuildManifest(...)`（包括传空 ID 或陈旧 ID）以 `TypeError` 失败。
- 确定 RED：前一步最小 GREEN 尚未定义 `BuildManifest` 和 `BuildManifestFactory`，测试导入时以 `ImportError` 失败。
- 最小 GREEN：`BuildManifest` 构造要求模块私有工厂 token，公开创建只经过 `BuildManifestFactory.create(payload)`；工厂完整编码 payload 后自动计算 ID，调用方不能传入 ID。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_manifest_codec.py -q`

- [x] **Step 3：RED→GREEN 实现原子读写和完整性校验**

行为：

- pytest：`tests/core/test_manifest_codec.py::test_write_and_read_manifest_round_trip_and_verify_id`
- 目标 API：`write_build_manifest(manifest, path)`、`read_build_manifest(path)`
- 断言：临时文件加 `Path.replace()` 原子写；round-trip 等价；读取时先解析 payload，再用工厂重算 ID，并要求文件中的 ID 非空且与重算值严格相等；空 ID、陈旧 ID、错误 schema 或错误结构均抛 `ArtifactValidationError`，不返回半合法对象。
- 确定 RED：前一步最小 GREEN 只实现工厂，`write_build_manifest`/`read_build_manifest` 尚未定义，测试导入时以 `ImportError` 失败。
- 最小 GREEN：实现纯本地 JSON 编解码、schema 检查和 ID 重算；禁止用文件中的 ID 直接构造 manifest。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_build_records.py tests/core/test_manifest_codec.py -q`

## Task 7：实现任务协议与身份

**Files:**

- Create: `src/st/build/core/tasks.py`
- Create: `tests/core/test_tasks.py`

- [x] **Step 1：在同一次首次 RED 中定义唯一任务规划契约**

行为：

- pytest：`tests/core/test_tasks.py::test_task_spec_plan_and_build_task_share_single_planning_contract`
- 目标 API：`TaskSpec`、`TaskPlan(spec, resolved_input_digest, config_digest)`、`BuildTask` Protocol、`BuildContext`、`ArtifactCollection`、`TaskResult`
- 断言：`TaskSpec` 保存 name、ordered dependencies、无序 outputs、实现版本和执行属性；`BuildTask.plan(context)` 返回引用同一 spec 的完整不可变 `TaskPlan`，`execute(context, inputs)` 返回 tuple outputs；协议没有 SVN、Unity、Jenkins 或上传方法。
- 确定 RED：`st.build.core.tasks` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：实现 `TaskSpec`、完整 `TaskPlan`、`@runtime_checkable BuildTask` 和输入/输出模型；`TaskResult.outputs` 使用 tuple 以便检测重复路径。本步不定义 `TaskIdentity`。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_tasks.py -q`

- [x] **Step 2：RED→GREEN 只从 TaskPlan 生成身份**

行为：

- pytest：`tests/core/test_tasks.py::test_task_identity_covers_request_revision_toolchain_baseline_schema_and_upstream`
- 目标 API：`TaskIdentity.from_plan(plan, context, upstream_identities)`
- 断言：身份摘要覆盖 `TaskPlan` 的 spec、resolved input/config digest，以及 context 的请求、固定 revision、工具链、基线、schema 和有序 upstream identities；公共 API 不提供 `TaskIdentity.from_spec`。
- 确定 RED：前一步最小 GREEN 明确未定义 `TaskIdentity`，测试导入时以 `ImportError` 失败。
- 最小 GREEN：使用规范 JSON 和 SHA256 生成不可变 `TaskIdentity`，不引入缓存。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_tasks.py -q`

## Task 8：实现 BuildGraph 不变量

**Files:**

- Create: `src/st/build/core/graph.py`
- Create: `tests/core/test_graph.py`

- [x] **Step 1：RED→GREEN 实现不可变图查询**

行为：

- pytest：`tests/core/test_graph.py::test_build_graph_exposes_dependencies_dependents_and_roots`
- 目标 API：`BuildGraph.from_plans(plans)`、`plan_of(name)`、`dependencies_of(name)`、`dependents_of(name)`、`roots`
- 断言：图节点保存完整 TaskPlan，查询结果正确且不可变；图内计划不随输入容器后续修改而变化。
- 确定 RED：`st.build.core.graph` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：复制并冻结规格和边集合，只实现合法输入的查询；本步明确不做重复名、缺失依赖和自边校验。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_graph.py -q`

- [x] **Step 2：RED→GREEN 实现局部结构校验**

行为：

- pytest：`tests/core/test_graph.py::test_build_graph_rejects_duplicate_names_missing_dependencies_and_self_edges`
- 目标 API：`BuildGraph.from_plans`
- 断言：重复任务名、缺失依赖和自依赖均抛 `PlanningError`，错误消息包含任务名。
- 确定 RED：前一步最小 GREEN 明确不校验三类无效结构，参数化测试的每个 `pytest.raises(PlanningError)` 都因“未抛异常”失败。
- 最小 GREEN：构图时做局部校验；循环和输出冲突由 planner 的独立测试驱动。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_graph.py -q`

## Task 9：实现 BuildPlanner

**Files:**

- Create: `src/st/build/core/planner.py`
- Create: `tests/core/test_planner.py`

- [x] **Step 1：RED→GREEN 检测循环**

行为：

- pytest：`tests/core/test_planner.py::test_planner_rejects_cycle_with_stable_cycle_path`
- 目标 API：`BuildPlanner.plan(plans: tuple[TaskPlan, ...], context) -> PlannedBuild`
- 断言：planner 只接收已完成 TaskPlan；循环抛 `PlanningError`，循环路径按稳定任务键报告，输入排列不改变消息。
- 确定 RED：`st.build.core.planner` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：从 `BuildGraph.from_plans` 实现循环检测和稳定路径报告；本步不建立输出 owner 索引，也不提供 `PlannedBuild.layers`。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_graph.py tests/core/test_planner.py -q`

- [x] **Step 2：RED→GREEN 检测输出冲突和隐式 fan-in**

行为：

- pytest：`tests/core/test_planner.py::test_planner_rejects_duplicate_output_owners`
- 目标 API：`BuildPlanner.plan`
- 断言：两个任务声明同一逻辑输出时抛 `PlanningError` 并列出两个 owner；fan-in 必须由显式聚合任务拥有新输出。
- 确定 RED：前一步最小 GREEN 明确未建立输出 owner 索引，重复输出输入未抛 `PlanningError`，使 `pytest.raises` 失败。
- 最小 GREEN：按输出路径建立唯一 owner 映射。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_planner.py -q`

- [x] **Step 3：RED→GREEN 生成确定性执行层**

行为：

- pytest：`tests/core/test_planner.py::test_planner_builds_deterministic_layers_and_expected_identity_map`
- 目标 API：`PlannedBuild.layers`、`PlannedBuild.expected_identities`
- 断言：独立 TaskPlan 在同一层；层内按任务名 UTF-8 字节序排列；不同输入排列得到相同层；依赖只出现在更早层；planner 对每个 plan 调用 `TaskIdentity.from_plan(plan, context, upstream_identities)`，expected identity 映射完整且不可变；planner 不再调用 task.plan，也不接受 TaskSpec 集合。
- 确定 RED：前两步最小 GREEN 明确未定义 `PlannedBuild.layers`/`expected_identities`，读取属性时以 `AttributeError` 失败。
- 最小 GREEN：使用稳定优先队列执行 Kahn 分层，并按层生成不可变 expected TaskIdentity 映射，不执行任务。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_graph.py tests/core/test_planner.py -q`

## Task 10：实现可验证恢复 Frontier

**Files:**

- Create: `src/st/build/core/frontier.py`
- Create: `tests/core/test_frontier.py`

- [x] **Step 1：RED→GREEN 定义完成证据**

行为：

- pytest：`tests/core/test_frontier.py::test_completed_task_record_captures_all_resume_identity_fields`
- 目标 API：`ResumeContext`、`CompletedTaskRecord(task_name, task_identity, outputs, request_digest, revision, toolchain_digest, baseline_id, schema_version, upstream_identities)`
- 断言：`outputs` 是完整不可变 `tuple[LogicalArtifact, ...]`，保留逻辑路径、kind、Blob、依赖、分包和 metadata；记录同时包含 task identity、request、固定 revision、toolchain、baseline、schema 和 upstream identities，禁止退化为 BlobRef 集合。
- 确定 RED：`st.build.core.frontier` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：只实现不可变记录；不得只保存“最后任务名”或布尔 completed，本步不实现验证 API。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_frontier.py -q`

- [x] **Step 2：RED→GREEN 比较当前规划 expected identities 和恢复上下文**

行为：

- pytest：`tests/core/test_frontier.py::test_frontier_requires_expected_identity_for_every_node_and_rejects_all_identity_mismatches`
- 目标 API：`ExecutionFrontier.verify(graph, records, expected_identities, context, verifier) -> VerifiedFrontier`
- 断言：调用方显式传入 `PlannedBuild.expected_identities`；缺少任一 expected identity、记录 identity 与 expected identity 不同，以及 request、revision、toolchain、baseline、schema、upstream 任一不同时，该节点均不进入结果。
- 确定 RED：前一步最小 GREEN 明确未定义 `ExecutionFrontier`、`VerifiedFrontier` 和 `verify`，测试导入时以 `ImportError` 失败。
- 最小 GREEN：逐节点先比较 `record.task_identity == expected_identities[task_name]`，再逐字段比较恢复上下文；返回 `VerifiedFrontier(task_names=frozenset(...), outputs=任务名到完整 LogicalArtifact tuple 的不可变映射)`。本步 verifier 只接受全部输出，路径与真实哈希拒绝行为留给下一 RED。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_tasks.py tests/core/test_frontier.py -q`

- [x] **Step 3：RED→GREEN 校验输出真实哈希**

行为：

- pytest：`tests/core/test_frontier.py::test_frontier_rejects_output_path_or_blob_integrity_mismatch`
- 目标 API：`BlobHashVerifier` Protocol、`ExecutionFrontier.verify`
- 断言：先从当前 `BuildGraph.plan_of(task).spec.outputs` 取得 expected paths；CompletedTaskRecord 的实际路径出现缺失、未声明或重复时节点不可复用；路径严格相等后，再对每个完整 LogicalArtifact 校验 locator 存在、实际 SHA256 与 BlobRef.sha256 相同、实际大小相同。
- 确定 RED：前一步最小 GREEN 使用固定接受 verifier 结果且未比较 TaskPlan outputs；路径或哈希不匹配的节点仍进入集合，参数化断言确定失败。
- 最小 GREEN：先拒绝重复路径并比较实际/expected 路径集合，再逐 LogicalArtifact 调用 verifier；只把完整已验证 artifacts 放入 `VerifiedFrontier.outputs` 不可变映射。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_frontier.py -q`

- [x] **Step 4：RED→GREEN 返回 DAG frontier 而非最后节点**

行为：

- pytest：`tests/core/test_frontier.py::test_frontier_keeps_all_verified_completed_nodes_and_invalidates_descendants`
- 目标 API：`ExecutionFrontier.verify`
- 断言：返回值是不可变 `VerifiedFrontier`；多个独立已验证节点均可复用；任一上游无效会使其所有下游不可复用，但不影响独立分支。
- 确定 RED：前一步最小 GREEN 逐节点独立验证但尚未沿 DAG 传播无效性，因此“上游坏、下游记录自身正确”的输入仍错误保留下游，断言确定失败。
- 最小 GREEN：按拓扑层传播有效性，不递归重启完整流水线。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_graph.py tests/core/test_frontier.py -q`

## Task 11：实现确定性同步 Executor

**Files:**

- Create: `src/st/build/core/executor.py`
- Create: `tests/core/test_executor.py`

- [x] **Step 1：RED→GREEN 按计划执行并收集输出**

行为：

- pytest：`tests/core/test_executor.py::test_executor_runs_layers_in_stable_order_and_collects_artifacts`
- 目标 API：`TaskExecutor.execute(planned_build, tasks, context, verified_frontier=None) -> ExecutionResult`
- 断言：按 planner 层和层内稳定顺序调用任务；只把显式上游产物传给节点；结果包含所有 `LogicalArtifact`。
- 确定 RED：`st.build.core.executor` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：实现 `verified_frontier=None` 的单线程同步参考执行器并收集 tuple 输出；本步明确不接受非空 frontier、不校验输出契约、不包装任务异常。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_planner.py tests/core/test_executor.py -q`

- [x] **Step 2：RED→GREEN 只跳过经验证节点**

行为：

- pytest：`tests/core/test_executor.py::test_executor_accepts_only_verified_frontier_and_skips_exact_verified_set`
- 目标 API：`TaskExecutor.execute(..., verified_frontier=VerifiedFrontier)`
- 断言：verified set 中的节点不调用 execute 且已验证输出注入 registry；其他节点执行；类型签名和运行时都不接受原始 `ExecutionFrontier`/`CompletedTaskRecord`。
- 确定 RED：前一步最小 GREEN 对任何非空 `verified_frontier` 抛 `NotImplementedError`，测试在传入 `VerifiedFrontier` 时确定失败。
- 最小 GREEN：执行器只消费 Task 10 已冻结的 `VerifiedFrontier`，不复制身份或哈希判定。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_frontier.py tests/core/test_executor.py -q`

- [x] **Step 3：RED→GREEN 严格验证 TaskResult 输出契约**

行为：

- pytest：`tests/core/test_executor.py::test_executor_rejects_missing_undeclared_and_duplicate_task_outputs`
- 目标 API：`TaskExecutor.execute`、`TaskResult.outputs`、`TaskSpec.outputs`
- 断言：参数化构造缺失声明路径、额外未声明路径、重复逻辑路径三种结果，均在写入 registry 或调度下游前抛 `ArtifactValidationError`；完全相等时才登记。
- 确定 RED：前两步最小 GREEN 直接登记 `TaskResult.outputs`，三种无效结果均不会抛 `ArtifactValidationError`，每个 `pytest.raises` 确定失败。
- 最小 GREEN：先从 tuple 输出提取逻辑路径并拒绝重复，再比较 `frozenset(actual_paths) == TaskSpec.outputs`；错误消息列出 missing、undeclared、duplicates。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_tasks.py tests/core/test_executor.py -q`

- [x] **Step 4：RED→GREEN 失败即停止新调度**

行为：

- pytest：`tests/core/test_executor.py::test_executor_stops_after_task_failure_without_recursive_restart`
- 目标 API：`TaskExecutor.execute`
- 断言：任务异常包装为 `ToolExecutionError` 并保留 cause；后续节点不执行；执行器不会递归调用自身或重跑已成功节点。
- 确定 RED：前三步最小 GREEN 让原始 `RuntimeError` 直接逸出，测试期待 `ToolExecutionError` 时确定失败。
- 最小 GREEN：捕获单节点异常、记录失败并退出当前执行；重试策略留给后续适配器计划。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/core/test_executor.py -q`

## Task 12：实现 ReleaseEntry 和低清对象来源语义

**Files:**

- Create: `src/st/build/release/__init__.py`
- Create: `src/st/build/release/entries.py`
- Create: `tests/release/test_entries.py`

- [x] **Step 1：在同一次首次 RED 中实现发布条目和新旧对象规则**

行为：

- pytest：`tests/release/test_entries.py::test_release_entry_separates_transfer_identity_and_enforces_object_origin_rules`
- 目标 API：`ResourceVariant`、`ReleaseObjectOrigin`、`ReleaseEntry(logical_path, variant, source_blob, source_md5, original_size, transfer_blob, transfer_size, list_version, object_version, file_url, subpackage_flag, object_origin)`
- 断言：原始 MD5/大小和传输 SHA256/大小独立；正 Int32 list version、非负 Int32 大小/flag、object version 与逻辑路径合法；main 本次新上传对象必须用 `{current}`，low 本次新上传对象必须用 `{current}_low`；`HISTORICAL` 条目允许保留合法历史 object_version/URL。
- 确定 RED：`st.build.release.entries` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：实现不可变领域模型和对象来源校验；不出现六字段文本列名，不导入 compatibility。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/release/test_entries.py -q`

## Task 13：实现协议无关 ReleaseSnapshot 和 ReleaseManifest

**Files:**

- Create: `src/st/build/release/snapshots.py`
- Create: `src/st/build/release/manifests.py`
- Create: `tests/release/test_snapshots.py`
- Create: `tests/release/test_manifests.py`

- [x] **Step 1：RED→GREEN 表达 AB 依赖和 Redirect slice**

行为：

- pytest：`tests/release/test_snapshots.py::test_release_snapshot_locks_variant_and_classifies_publication_membership`
- 目标 API：`ReleaseArtifactClass`、`ReleaseMembership`、`RedirectSlice(container_logical_path, container, offset, length)`、`ReleaseSnapshotEntry(release_entry, artifact_class, memberships, assetbundle_dependencies, redirect_slice)`、`ReleaseSnapshot.create(variant, entries)`
- 断言：snapshot 锁定单一 `ResourceVariant` 并拒绝混入另一 variant；依赖 `("b", "a", "b")` 原样保留；Redirect 保存容器路径/Blob/offset/length；被替代原 AB 分类为 `REDIRECT_SLICE` 且不具 `ReleaseMembership.FILE_LIST`；Redirect 容器分类为 `REDIRECT_CONTAINER`，在 snapshot 中路径唯一且同时具 `FILE_LIST`/`ASSET_BUNDLE_DATABASE`；普通文件没有 `ASSET_BUNDLE_DATABASE`；依赖目标/容器缺失、Blob 不匹配、越界或非法分类-membership 组合均失败。
- 确定 RED：`st.build.release.snapshots` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：从 `entries.py` 导入唯一 `ResourceVariant`，实现协议无关分类、membership、单 variant 与交叉引用校验；不包含 AB 数据库索引、`Depend:`、`Redirect:` 或换行规则。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/release/test_entries.py tests/release/test_snapshots.py -q`

- [x] **Step 2：在同一次首次 RED 中实现 ReleaseManifestPayload、工厂和严格读取**

行为：

- pytest：
  - `tests/release/test_manifests.py::test_release_manifest_payload_locks_variant_and_current_object_versions`
  - `tests/release/test_manifest_codec.py::test_release_manifest_factory_stabilizes_unordered_inputs_and_hashes_all_identity_fields`
  - `tests/release/test_manifest_codec.py::test_read_release_manifest_rejects_empty_stale_or_unknown_schema`
- 目标 API：`ReleaseManifestPayload(schema_version, variant, file_list_no, snapshot, source_manifest_ids)`、`ReleaseManifestFactory.create(payload)`、`write_release_manifest`、`read_release_manifest`
- 断言：payload 不含 ID；snapshot.variant 必须等于 payload.variant；每个 entry variant 必须一致；main/low 的 `CURRENT_UPLOAD` 分别严格使用 `{FileListNo}`/`{FileListNo}_low`，`HISTORICAL` 保留历史值；交换无序 snapshot entries/source IDs 不改变 ID，而 schema、variant、FileListNo、snapshot 内容或 source ID 任一变化都会改变 ID；依赖 tuple 顺序重复保留；直接构造 manifest 失败；读取重算并拒绝空/陈旧 ID、未知 schema 和 variant 不一致。
- 确定 RED：三个测试在 `manifests.py` 与 `manifest_codec.py` 创建前同时添加，测试收集时以 `ModuleNotFoundError: st.build.release.manifests` 失败。
- 最小 GREEN：从 `entries.py` 导入唯一 `ResourceVariant`；实现不可变 payload、私有 token 构造的 manifest、自动 ID 工厂、无序字段规范化、原子写和严格反序列化。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/release/test_entries.py tests/release/test_snapshots.py tests/release/test_manifests.py tests/release/test_manifest_codec.py -q`

## Task 14：实现 ReleaseBundle

**Files:**

- Create: `src/st/build/release/bundles.py`
- Create: `src/st/build/release/bundle_codec.py`
- Create: `tests/release/test_bundles.py`
- Create: `tests/release/test_bundle_codec.py`

- [x] **Step 1：在同一次首次 RED 中实现 ReleaseBundlePayload、工厂和严格读取**

行为：

- pytest：
  - `tests/release/test_bundles.py::test_release_bundle_payload_accepts_one_main_and_optional_low_with_shared_file_list_no`
  - `tests/release/test_bundle_codec.py::test_release_bundle_factory_stabilizes_unordered_manifests_and_hashes_all_identity_fields`
  - `tests/release/test_bundle_codec.py::test_read_release_bundle_rejects_empty_stale_or_unknown_schema`
- 目标 API：`ReleaseBundlePayload(schema_version, manifests, baseline_bundle_id)`、`ReleaseBundleFactory.create(payload)`、`write_release_bundle`、`read_release_bundle`
- 断言：payload 不含 ID；无序 manifest 集合必须恰有一个 main、至多一个 low，并共享 FileListNo；历史低清 object_version 不被 bundle 二次拒绝；交换 manifest 输入顺序不改变 ID，而 schema、main ID、low 存在/ID、baseline 任一变化改变 ID；直接构造 bundle 失败；读取重算并拒绝空/陈旧 ID、未知 schema、重复 variant 和 FileListNo 不一致。
- 确定 RED：三个测试在 `bundles.py` 与 `bundle_codec.py` 创建前同时添加，测试收集时以 `ModuleNotFoundError: st.build.release.bundles` 失败。
- 最小 GREEN：从 `entries.py` 导入唯一 `ResourceVariant`；实现不可变 payload、私有 token 构造的 bundle、自动 ID 工厂、稳定 manifest 排序、原子写和严格反序列化，不实现发布器。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/release/test_manifests.py tests/release/test_manifest_codec.py tests/release/test_bundles.py tests/release/test_bundle_codec.py -q`

## Task 15：实现独立 ReleaseActivationRecord

**Files:**

- Create: `src/st/build/release/activation.py`
- Create: `tests/release/test_activation.py`

- [x] **Step 1：RED→GREEN 分离可变激活记录**

行为：

- pytest：`tests/release/test_activation.py::test_activation_record_tracks_bundle_state_and_rejects_unknown_schema`
- 目标 API：`ReleaseActivationStatus`、`ReleaseActivationRecord(schema_version, activation_id, bundle_id, target, expected_generation, status, required_objects_digest, verified_objects_digest, error)`、`VerifiedReleaseBundle`
- 断言：记录可表达 preparing/uploading/verifying/activating/active/failed/conflicted；只引用 bundle ID；schema 必须等于当前受支持常量，未知版本抛 `PublishError`；`VerifiedReleaseBundle` 只能由验证器在 Bundle 全部必要对象的远端哈希匹配后创建；创建新记录推进状态不改变 ReleaseBundle ID。
- 确定 RED：`st.build.release.activation` 尚未创建，测试收集时以 `ModuleNotFoundError` 失败。
- 最小 GREEN：实现不可变状态快照和合法状态枚举，不实现状态迁移函数、CDN、CAS 或回滚。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/release/test_bundles.py tests/release/test_activation.py -q`

- [x] **Step 2：RED→GREEN 限制激活状态迁移**

行为：

- pytest：`tests/release/test_activation.py::test_activation_record_allows_only_declared_state_transitions`
- 目标 API：`verify_release_bundle(bundle, remote_objects) -> VerifiedReleaseBundle`、`advance_activation(record, next_status, *, verification=None, error=None)`
- 断言：只允许 `PREPARING→UPLOADING→VERIFYING→ACTIVATING→ACTIVE`，以及声明阶段到 `FAILED`、`ACTIVATING→CONFLICTED`；终态不能继续推进；失败必须有 error；`VERIFYING→ACTIVATING` 必须提供与当前 bundle ID、必要对象摘要和远端哈希一致的 `VerifiedReleaseBundle`，调用方不能用普通集合伪造验证完成。
- 确定 RED：前一步最小 GREEN 明确未定义 `advance_activation`，测试导入时以 `ImportError` 失败。
- 最小 GREEN：实现纯函数返回新的不可变记录并校验迁移，不修改旧记录。
- 回归：`python -m pytest tests/quality/test_chinese_documentation.py tests/release/test_activation.py -q`

## Task 16：阶段回归、覆盖率和人工审查

**Files:**

- Modify only if needed: `readme/13_第二阶段领域模型与DAG实施计划.md`
- Modify only after implementation exists: `README.md`

- [x] **Step 1：运行完整功能回归**

Run: `python -m pytest -q`

Expected: 全部 PASS，且不存在 Python 版本 skip。

- [x] **Step 2：强制整体覆盖率 80%**

Run: `python -m pytest --cov=st.build --cov-report=term-missing --cov-fail-under=80`

Expected: exit code 0；低于 80% 时 pytest 必须失败，不能只在文字中查看百分比。

- [x] **Step 3：分别强制 core 与 release 覆盖率 90%**

Run:

```text
python -m pytest tests/core --cov=st.build.core --cov-report=term-missing --cov-fail-under=90
python -m pytest tests/release --cov=st.build.release --cov-report=term-missing --cov-fail-under=90
```

Expected: 两条命令分别 exit code 0；不得用合并覆盖率掩盖任一包低于 90%。

- [x] **Step 4：运行格式、静态和字节码检查**

Run:

```text
python -m ruff format --check .
python -m ruff check .
python -m pyright
python -m compileall -q src tests
```

Expected: 全部 exit code 0。

- [ ] **Step 5：人工审查中文文档**

逐个检查 `src/` 和 `tests/` 的模块、类、异常、函数和方法。自动门禁通过不等于详细度通过；确认职责、参数、返回值、异常、约束、副作用，以及确定性、恢复、异常分支等复杂逻辑的中文行内注释。

- [x] **Step 6：确认架构边界**

用 `python -m pytest tests/quality/test_chinese_documentation.py -q` 再次确认门禁，并人工确认：

- core/release 未导入 SVN、Unity、Jenkins、CDN 或 compatibility；
- BuildManifest 未混入运行状态或发布信息；
- BuildManifest 只能由 payload 工厂生成，读取拒绝空/陈旧 ID；
- `ResourceVariant` 只在 `release/entries.py` 声明；
- BuildExecutionRecord 与 ReleaseActivationRecord 都含 schema_version 并拒绝未知版本；
- ReleaseManifest/ReleaseBundle 均由独立 payload 工厂生成，反序列化拒绝空/陈旧 ID；
- ReleaseSnapshot 锁定单 variant，并在 compatibility 之前表达分类、发布 membership、有序重复 AB 依赖和 Redirect slice；
- 应用层先产出完整 TaskPlan，BuildPlanner 只消费 TaskPlan，TaskIdentity 只提供 from_plan；
- ordered dependencies 保序保重复；
- 只有无序集合被规范排序；
- manifest/bundle ID 排除自身字段；
- CompletedTaskRecord 保存完整 LogicalArtifact tuple；Frontier 显式比较当前规划 expected TaskIdentity 映射、当前 outputs 路径、输出哈希、request、revision、toolchain、baseline、schema 和 upstream；
- Executor 只消费不可变 VerifiedFrontier，并拒绝缺失、未声明或重复 TaskResult 输出。

## 提交说明

执行计划时也不得自动创建 Git commit。只有用户明确要求提交时，才按已通过回归的 Task 分批提交。
