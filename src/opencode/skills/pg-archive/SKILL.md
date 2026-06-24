---
name: pg-archive
description: 手动归档变更到 .pg/changes/archive/。pg-build 成功时自动归档，失败时不会归档——此 SKILL 专门处理失败后或主动放弃时的手动归档场景。纯被动：只移动目录并输出报告，不做 git 操作、不调其他工作流。
license: MIT
compatibility: 需要存在 .pg/changes/<change-name>/ 目录
metadata:
  author: pg-spec
  version: "2.0"
---

# pg-archive

手动归档一个变更目录到 `.pg/changes/archive/`，命名格式为 `YYYY-MM-DD-<change-name>`，冲突时自动追加 `.N` 后缀。

> **角色边界**：此 SKILL 由 `pg-manager` agent 在收到 `/6-pg-archive` 命令后加载执行。**用户面 agent 不应直接加载此 SKILL**——如需归档，应使用 Task 工具派遣 `pg-manager` agent。

---

## 适用场景

- `pg-build` 失败后，希望清理工作区但保留变更产物
- 测试/调试时主动放弃一个 change
- 业务上确定不再推进的旧 change 清理
- `pg-build` 自动归档失败后的人工补做

## 不适用场景

- `pg-build` 成功完成时不需要此 SKILL（runner 会自动调用共享脚本 `pg-archive.py move` 完成归档 + 自动 commit）
- 需要从 git 历史完全删除 change（应使用 git filter-branch / BFG 等工具，本 SKILL 不涉及）

---

## 前置条件

| 项 | 要求 | 校验失败行为 |
|----|------|------------|
| `.pg/changes/<change-name>/` 存在 | 必需 | 终止并报告 |
| `.pg/changes/archive/` 存在 | 必需（不存在则创建） | 自动创建 |
| 解析 change-name | 必需（kebab-case 字符串） | 终止并报告 |

不检查 `.pg-spec.yaml`、tasks.md 完成度、context-chain.md 状态——这些是 `pg-build` 的职责，本 SKILL 只做移动。

---

## 核心流程

### 步骤 1：解析与校验

```bash
change_name="$1"
if [[ -z "$change_name" ]]; then
  echo "ERROR: 必须提供 change-name 参数"
  exit 1
fi

src=".pg/changes/${change_name}"
if [[ ! -d "$src" ]]; then
  echo "ERROR: ${src} 不存在"
  exit 1
fi
```

### 步骤 2：调用共享脚本完成移动

核心移动逻辑由 `.opencode/skills/pg-archive/scripts/pg-archive.py` 实现，
与 `pg-build` runner 共用同一份代码，避免逻辑分叉。

```bash
python3 .opencode/skills/pg-archive/scripts/pg-archive.py move "${change_name}"
```

脚本输出 JSON 到 stdout，例如：

```json
{"ok": true, "target_name": "2026-06-15-my-change", "src": ".pg/changes/my-change", "target": ".pg/changes/archive/2026-06-15-my-change"}
```

或失败时：

```json
{"ok": false, "reason": "源目录不存在: .pg/changes/my-change", "src": ".pg/changes/my-change"}
```

SKILL 调用脚本后解析 JSON，按下方「报告格式」渲染结果。**不直接操作文件系统。**

### 步骤 3：报告输出

按下方「报告格式」输出 markdown 报告。归档是文件系统操作，**不做 git commit / push**——提交由调用方按需进行。

---

## 命名冲突示例

| 已有归档 | 新归档目标 | 说明 |
|---------|----------|------|
| 无 | `archive/2026-06-15-add-user-api/` | 无冲突 |
| `archive/2026-06-15-add-user-api/` | `archive/2026-06-15-add-user-api.1/` | 同日重复归档 |
| `archive/2026-06-15-add-user-api/` + `.1` | `archive/2026-06-15-add-user-api.2/` | 三次同日归档 |

后缀 `.N` 从 1 开始递增。

---

## 明确不做的事

- **不做 git commit / push**——归档是文件系统操作，提交由调用方按需进行
- **不调 `pg-build` / `pg-fix-issue`**——本 SKILL 不具备修复或重跑能力
- **不修改 context-chain.md**——目录移动后原文件保留，但不再追加任何记录
- **不读 tasks.md / design.md**——不做完成度检查
- **不删除任何文件**——mv 整个目录，所有 proposal/design/tasks/context-chain 原样保留
- **不复刻移动逻辑**——直接调用 `pg-archive.py move`，与自动归档走同一份代码

---

## 报告格式

成功时输出：

```
## 变更归档完成

**变更：** {{change-name}}
**工作流：** pg-archive
**状态：** SUCCESS

### 归档位置

- **源路径：** .pg/changes/{{change-name}}/
- **目标路径：** .pg/changes/archive/{{target-name}}/
- **归档时间：** {{ISO timestamp}}

### 命名处理

- 基础名：{{YYYY-MM-DD}}-{{change-name}}
- 冲突检测：{{无 / 有，追加 .N}}
- 最终名：{{target-name}}

### 归档内容

| 文件 | 大小 | 备注 |
|------|------|------|
| {{file}} | {{size}} | |
| ... | | |

### 下一步

- 归档是文件系统操作，未做 git 提交
- 如需提交归档，使用 `git add -A .pg/changes/archive/ && git rm -r --cached .pg/changes/{{change-name}} && git commit -m "archive change {{change-name}}"`
- 如需从 git 历史移除，使用 `git rm -r .pg/changes/{{change-name}}` 后提交
- 如需重新启动此 change 的实现，将目录移回 `.pg/changes/` 后执行 `/3-pg-build {{change-name}}`
```

失败时输出：

```
## 变更归档失败

**变更：** {{change-name}}
**工作流：** pg-archive
**状态：** FAILED

### 失败原因

- **失败步骤：** {{步骤名}}
- **失败详情：** {{描述}}
```

---

## 安全规则

- 目标路径仅写入 `.pg/changes/archive/` 下，不允许 archive 之外的位置
- 移动后必须验证原目录消失、目标目录存在
- 不删除任何文件，包括归档目录中的 context-chain.md
- 不调 git 命令——提交由调用方控制
