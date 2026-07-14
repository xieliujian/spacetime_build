# 项目 Agent 资源

此目录存放可被 GPT、Cursor、Codex 及其他 Agent 工具发现的项目级资源。

## 目录

- `skills/st-build-development/`：开发和重构 `st.build` 的项目技能；
- `prompts/`：可手动调用的复用提示词；
- `superpowers/specs/`：设计规格索引；
- `superpowers/plans/`：实施计划索引。

## 规则来源

Agent 开始工作前应依次读取：

1. 根目录 `AGENTS.md`；
2. `readme/00_全新构建系统设计.md`；
3. 当前阶段对应的实施计划；
4. 与任务相关的 `.agents/skills/*/SKILL.md`。

原始目录 `F:\proj_se\develop\client\tools\build` 只允许读取，不允许修改或直接运行可能产生外部副作用的入口。
