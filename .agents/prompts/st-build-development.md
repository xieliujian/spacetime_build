---
name: st-build-development
description: 按项目设计、测试和中文注释规范实现直接顶级包构建系统功能。
---

# /st-build-development

读取并遵循：

- `AGENTS.md`
- `.agents/skills/st-build-development/SKILL.md`
- `readme/version_build/00_全新构建系统设计.md`
- 当前阶段实施计划

针对用户请求执行测试驱动开发。先确认修改层次和兼容边界，再编写失败测试、最小实现和详细中文文档，最后运行相关测试、格式检查和类型检查。

不得修改或直接执行 `F:\proj_se\develop\client\tools\build` 中可能产生写入、提交或上传副作用的入口。

用户请求：

```text
{{args}}
```
