---
name: 6-pg-archive
description: 6. 手动归档变更到 .pg/changes/archive/（pg-build 失败后的清理）
trigger: slash
agent: pg-manager
---

# /6-pg-archive <change-name>

change-name: $1

此命令被触发时，系统调度 pg-manager agent 执行。

执行步骤：
1. 使用 Skill tool 加载 `pg-archive` skill
2. 解析 $1 为 change-name
3. 按 SKILL 定义的归档流程执行移动 + 命名冲突处理
4. 输出归档报告

**注意**：归档是纯文件系统操作，不做 git commit/push，不修改 context-chain.md。

**示例**：
```
/6-pg-archive add-user-api
/6-pg-archive fix-login-bug
```
