---
name: 1-pg-grill
description: 1. 进入压力测试/拷问模式——用设计树方法系统性拷问想法、暴露假设、收敛决策
trigger: slash
model: {{pg:model.master}}
---

**约束：当前会话忽略所有 superpowers SKILL。**

使用 Skill 工具加载 `pg-define` skill，并启用其中的 **grill 模式**（`§设计树拷问方法` 与 `§grill 模式额外要求`）。

Grill 模式与默认探索模式的区别：
- 探索姿态（`/1-pg-define`）：自由跟随对话、灵活转向
- 拷问姿态（`/1-pg-grill`）：**设计树 + 前沿**方法强制逐分支访问，每个决策必须显式拷问，事实查证必须委派子 agent，绝不向用户询问可自查的事实

定界后环境验证环节、define-summary.yaml 落盘、三态契约校验、重新定界协议在两种姿态下完全相同。

如需触发**重新定界协议**（pg-propose 阶段 1.8 define-summary 校验失败后回退），用 `/1-pg-grill --redefine <change-id>` 或自然语言"重新定界 `<change-id>`"（同样适用，skill 内协议与 `/1-pg-define --redefine` 一致）。