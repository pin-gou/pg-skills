---
name: 1-pg-define
description: 1. 进入探索/设计/定界模式——思考想法、调查问题、澄清需求、确定范围
trigger: slash
model: {{pg:model.master}}
---

**约束：当前会话忽略所有 superpowers SKILL。**

使用 Skill 工具加载 `pg-define` skill，并遵循其工作流。

如需触发**重新定界协议**（pg-propose 阶段 1.8 define-summary 校验失败后回退），用 `/1-pg-define --redefine <change-id>` 或自然语言"重新定界 `<change-id>`"。