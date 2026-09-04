---
name: 0-pg-auto-pilot
description: 0. 自动驾驶模式——不限定 LLM 如何规划与执行，只要求计划含"启动实例并验证结果"步骤、执行前让用户选定环境并确认环境准备方式
trigger: slash
model: {{pg:model.master}}
---

**约束：本命令不限定你的规划与执行方式。**

使用 {{pg:action.skill_loader}} 加载 `pg-auto-pilot` skill，并遵循其两条要求：

1. **实施计划必须包含**"启动实例并验证编码结果是否达到预期"的步骤
2. **执行计划前**，让用户选定环境，并确认需要准备环境，还是环境已就绪（跳过准备）

流程编排、手段选择由你自主决定。

**触发词**:
```
/0-pg-auto-pilot
```