---
name: 2b-pg-quick-build
description: 2b. 跳过 pg-propse，不生成 proposal.md/design.md/tasks.md，直接构建代码
trigger: slash
---

# Pg Quick Build

> **⚠️ 必须先加载技能**
>
> 在开始任何工作之前，**必须**使用 `skill` 工具加载 `pg-quick-build` skill，然后严格按照该 skill 的工作流（Phase 0 定界 → Phase 1 派遣 worker → Phase 2 收尾）执行。
>
> 不加载 skill 就进入实现阶段属于违规流程，所有未提交的代码必须撤销。

**核心架构**：

- 主 agent（pg-quick-build SKILL）：Phase 0 在内存里构造 design + tasks，做强停判断，切分支，**一次性** Task tool 派遣 worker
- Worker sub-agent（pg-quick-build/worker）：自己写测试、自己实现、自己验证、自己修 bug，自带 self_check

**零产物落盘**：不建 `.pg/changes/<name>/`，不写 design.md / tasks.md / proposal.md。

> **注意**：如果需求已在当前对话中讨论明确，直接基于对话上下文执行 Phase 0 的步骤 0.2 构造 design 和 0.3 构造 tasks 即可，但 **步骤 0.0 自检、0.4 强停判断、0.5 question 确认不可跳过**。
