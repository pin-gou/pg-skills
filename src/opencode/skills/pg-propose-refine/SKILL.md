---
name: pg-propose-refine
description: 读取 review-notes.md（v4 单文档）或 review-decisions.yaml（v3 兼容）的决策，按 scope 精准修改 proposal.md / design.md / tasks.md。
license: MIT
metadata:
  author: pg
  version: "4.0"
---

# pg-propose-refine（决策应用器）

> **v4 起**，本 skill 从"双文档（review-decisions.yaml + review-notes.md）"架构简化为**单文档（review-notes.md）**架构。review-notes.md 同时承担"自审诊断"和"用户决策表"两个职责，markdown checkbox 作为状态指示。
>
> **v3 兼容**：仍能读取 v3 双文档格式（review-decisions.yaml + review-notes.md），旧变更可平滑迁移。

---

## 输入

- 变更名称：`<change-name>`（必填）
- 决策文件：`.pg/changes/<change-name>/1-propose-review/review-notes.md`（v4 必填，必须已存在）

---

## 工作流程

### 第一步：环境与产物加载

1. 验证 `.pg/changes/<change-name>/` 目录存在
2. **自动探测文档版本**：
   - 若 `1-propose-review/review-notes.md` 存在 → v4 单文档模式（默认）
   - 若 `1-propose-review/review-decisions.yaml` 存在但 review-notes.md 不存在 → v3 兼容模式（按 v3 流程处理）
   - 两者都存在 → v4 模式（review-decisions.yaml 视为遗留，将被归档）
   - 两者都不存在 → 报错退出，提示"未找到评审文档，请先执行 `/3-pg-propose <change-name>` 生成产物与评审文档"
3. 加载三个产物文件：
   - `proposal.md`
   - `design.md`
   - `tasks.md`

### 第二步：解析评审文档（v4 主路径）

解析 `review-notes.md` 为结构化对象，提取两类决策：

| 决策节 | 来源 | 用途 |
|--------|------|------|
| `common_decisions` | markdown 表格 "## 通用决策（5 项骨架）" | 5 个通用骨架决策（错误响应/鉴权 scope/数据迁移/事务边界/前端交互） |
| `issue_decisions` | markdown bullet 列表 "## 自审发现的问题" 三段 | 自审问题转决策项（阻塞/重要/建议） |

#### 2.1 解析通用决策表格

**位置**：`## 通用决策（5 项骨架）` 章节下的 markdown 表格。

**表头约定**：

```
| 决策项 | 选项 | 当前 | 推荐 | 备注 |
```

**解析规则**：

1. 提取所有 `<决策项> | <选项文本> | <当前> | <推荐> | <备注>` 行
2. `决策项` 列必须匹配 5 个固定 ID：`error_response_strategy` / `auth_scope` / `data_migration_strategy` / `transaction_boundary` / `frontend_interaction_style`
3. `当前` 列即 `current` 字段
4. `推荐` 列即 `recommended` 字段（用于全推荐场景判断）
5. `选项` 列文本（"A 全局统一 / B 按模块"）按 `/` 拆分为 options 列表
6. `备注` 列即 `rationale` 字段

**scope 字段来源**：硬编码映射（与 v3 5.1 节相同）：

| 决策项 | scope |
|--------|-------|
| `error_response_strategy` | `[design]` |
| `auth_scope` | `[design, tasks]` |
| `data_migration_strategy` | `[design, tasks]` |
| `transaction_boundary` | `[design, tasks]` |
| `frontend_interaction_style` | `[design, tasks]` |

#### 2.2 解析问题清单

**位置**：`## 自审发现的问题` 章节下的三个子段（阻塞/重要/建议）。

**条目状态**：

- `[ ]` 未勾选 → 待处理（进入 pending_decisions）
- `[x]` 已勾选 → 已处理（**默认跳过**，除非用户显式说"重做"）
- `[~]` 部分勾选 → 警告日志（不计入决策）

**条目格式**：

```markdown
- [ ] **tasks.md task 3.5/3.6 缺登录 token 获取**
  - 目标：tasks.md backend:verify
  - 推荐动作：在 3.4 与 3.5 之间插入 3.4.1 admin token 获取子任务
  - SKIP 允许：是
```

**字段映射**：

| markdown 字段 | 决策项字段 |
|---------------|-----------|
| 加粗文本（`**...**`） | `title` |
| `- 目标：{filename} {chapter}` | `target_file` + `scope` |
| `- 推荐动作：{text}` | `recommended_action` |
| `- SKIP 允许：是/否` | `skip_allowed` |

**决策项 ID 生成**：

```
fix-{severity}-{sequence}-{slug}
```

例：`fix-blocking-1-null-check`、`fix-important-3-rg-scope`、`fix-suggestion-2-ascii`。

**严重度 → 默认 current 映射**：

| 严重度 | 默认 current | SKIP 允许 |
|--------|--------------|-----------|
| 阻塞 | `FIX` | ❌ 不允许 SKIP |
| 重要 | `FIX` | ✅ 允许 SKIP（需在条目下加 `> SKIP：{理由}` 注释） |
| 建议 | `SKIP` | ✅ 允许 FIX（需在条目下加 `> FIX：{说明}` 注释覆盖默认） |

**用户编辑方式**：

| 想做什么 | 编辑方式 |
|---------|---------|
| 接受推荐修复 | 保留 `[ ]`，refine 时自动 FIX |
| 跳过修复 | 把 `[ ]` 改为 `[~]`，并在条目下加 `> SKIP：{理由}` |
| 反向（建议 → 修）| 在条目下加 `> FIX：{说明}` |
| 标记已修复 | 把 `[ ]` 改为 `[x]`，并在条目下加 `- 修复：{摘要}` 与 `- 修复时间：{timestamp}` |

### 第二步 B：v3 兼容模式（旧 YAML）

> **仅在 v3 双文档模式下执行**。本步骤在 v4 单文档模式下被跳过。

若 `review-decisions.yaml` 存在但 `review-notes.md` 不存在，按 v3 SKILL 流程读取 YAML 的 `common_decisions` / `issue_driven_decisions` / `applied` 三个区域。

读取后，refine 流程正常推进（与 v4 一致）。在第六步"冻结"时，**额外提示**：

```
检测到 v3 双文档格式，建议执行 `/3-pg-archive-legacy-review <change-name>` 把 review-decisions.yaml 合并到 review-notes.md。
```

不强制阻断——refine 完成后产物修改仍然有效。

### 第三步：校验决策完整性

**前置检查**（任意一项不通过则终止 refine）：

1. **所有 pending 决策的 `current` 必须合法**：
   - common_decisions：`current` 必须在 `options` 列出的合法值中（如 error_response_strategy 的 `current` 必须是 `A` 或 `B`）
   - issue_decisions：`current` 必须是 `FIX` / `SKIP`
2. **阻塞项必须为 `FIX`**：若 `[ ]` 阻塞项被改为 `SKIP`（用户加 `> SKIP：` 注释），**报错退出**，提示"阻塞项不可 SKIP"
3. **`[x]` 已勾选项必须填写修复摘要**：若勾选 `[x]` 但无 `- 修复：` 行，警告日志（不阻断）

### 第四步：diff 预览与确认

按以下规则决定是否需要 diff 预览：

**A. 全推荐场景（所有 common_decisions `current == recommended`，所有 issue_decisions `current == 默认值`）**：
- 无需 diff 预览，直接进入第五步机械应用
- 展示"将应用 N 项决策（全部为推荐值）"

**B. 有修改场景**：
- 按决策项逐条输出 diff 预览
- 使用 `question` 工具批量确认：
  > "将应用以下 N 项决策（其中 M 项与推荐值不同），请确认：
  > - 直接按用户选择应用（推荐）
  > - 调整后再确认
  > - 取消某项决策（不应用）"
- 用户确认后才进入第五步

### 第五步：应用决策到产物

按 scope 分类执行修改。决策分两类来源：

#### 5.1 common_decisions 映射规则（机械执行）

| 决策项 | 选项 | 产物修改动作 |
|--------|------|-------------|
| `error_response_strategy` | A | design.md → "## API 设计" 章节：错误响应格式统一为 `{code, message, requestId}` |
| `error_response_strategy` | B | design.md → "## API 设计" 章节：错误响应标记为"按模块自定义" |
| `auth_scope` | platform/tenant/project | design.md → "## API 设计" 章节权限行；tasks.md → 对应任务权限步骤 |
| `data_migration_strategy` | A | design.md → "## 关键约束与契约 > 前置条件" 添加 Flyway 任务；tasks.md → backend:dev 添加 Flyway 任务 |
| `data_migration_strategy` | B | design.md → "## 关键约束与契约 > 影响面" 添加应用层兼容；tasks.md → backend:dev 添加兼容代码 |
| `data_migration_strategy` | C | 无修改 |
| `transaction_boundary` | A | design.md → "## 性能契约" 添加 `@Transactional` 方法列表 |
| `transaction_boundary` | B | design.md → "## 性能契约" 添加分布式事务方案 |
| `transaction_boundary` | C | design.md → "## 性能契约" 标注最终一致 |
| `frontend_interaction_style` | A/B/C/D | design.md → "## 组件设计" + tasks.md → frontend:dev 任务描述统一 |

#### 5.2 issue_decisions 通用规则

| 选项 | 产物修改动作 |
|------|-------------|
| `FIX` | 按 `recommended_action` 修改对应文件（修复动作由 LLM 自由推导） |
| `SKIP` | review-notes.md 对应条目后追加 `> **SKIP**：{理由}` 注释，不修改任何产物文件 |

**FIX 应用规则**：

1. 读取 `target_file` + `recommended_action`
2. LLM 自由推导修复方案（不再依赖机械映射）：
   - "插入 3.4.1" → 在指定位置插入新子任务
   - "扩展 rg 扫描范围" → 修改 task 命令字符串
   - "加注释段" → 在指定章节追加段落
3. 保持风格一致（复用产物中已有的格式、命名、Markdown 风格）
4. 修复后立即在 review-notes.md 标记：把 `[ ]` 改为 `[x]`，在条目下加 `- 修复：{摘要}` 与 `- 修复时间：{timestamp}`

**SKIP 应用规则**：

1. 在 review-notes.md 对应条目后追加 `> **SKIP**：{理由}` 注释
2. 把 `[ ]` 改为 `[~]`（部分勾选标识）
3. 不修改任何产物文件

#### 5.3 通用应用步骤

对每个被应用决策：
1. 读取 scope 列出的产物文件
2. 定位要修改的章节（按 5.1 机械映射，或 5.2 LLM 推导）
3. 应用文本修改（**保持现有格式、风格、其他章节不动**）
4. 写入文件
5. 验证写入成功（读回确认）
6. **issue_decisions**：同步更新 review-notes.md 对应条目（FIX 改 `[x]`，SKIP 改 `[~]`）

**禁止行为**：
- 禁止重写整个文件
- 禁止删除未明确标记的章节
- 禁止跨决策项相互影响（每个决策项独立修改独立段落）
- 禁止 LLM 自由修复与机械映射混合（一个决策项只用一种方式）

### 第六步：冻结已决策项

v4 起，**不再有独立的 applied 区**——所有"已应用"状态直接记录在 review-notes.md：

1. **common_decisions 已应用**：
   - 把 markdown 表格中的"当前"列添加 `✅` 标记（保留原值），如 `A ✅`
   - 在表格下方追加 `**已应用时间**：{timestamp}`

2. **issue_decisions 已应用**：
   - FIX → `[x]` + 加 `- 修复：{摘要}` + `- 修复时间：{timestamp}`
   - SKIP → `[~]` + `> **SKIP**：{理由}`

3. **更新 review-notes.md 顶部追加"决策应用记录"段**：

   ```markdown
   ## 决策应用记录

   **应用时间**：{timestamp}
   **本轮应用项**：N（M 项 common + K 项 issue）

   - error_response_strategy: A
   - auth_scope: platform
   - fix-important-3-rg-scope: FIX（自动修复）
   - fix-suggestion-2-ascii: SKIP（用户豁免）

   **剩余未决项**：{数量}
   ```

4. **不再写回 `review-decisions.yaml`**：
   - v4 单文档模式下不创建/更新该文件
   - v3 兼容模式下不修改该文件（保持只读，避免歧义）

### 第七步：迭代与收尾

#### 7.1 多次迭代支持

- `review-notes.md` 始终是当前最新状态
- `[x]` 标记的项在后续 refine 中跳过
- `[ ]` 标记的项继续参与下一轮
- **幂等性**：相同 review-notes.md 多次 refine 结果相同（已勾选项不重复修复）

#### 7.2 收尾展示

向用户展示：
- 本轮应用了 N 项决策（M 项 common + K 项 issue）
- 剩余待决项数量
- 哪些产物文件被修改（文件路径 + 章节定位）
- 下一步建议：
  - 仍有未决项 → 继续编辑 review-notes.md 后再次 `/2.1-pg-propose-refine`
  - 全部决策已应用 → 建议在新的会话执行 `/3-pg-build {change-name}` 开始构建

---

## 异常处理

| 场景 | 行为 |
|------|------|
| 变更目录不存在 | 报错退出，提示先执行 `/3-pg-propose` |
| review-notes.md 和 review-decisions.yaml 都不存在 | 报错退出，提示先执行 `/3-pg-propose` |
| review-notes.md 存在但缺 "## 通用决策（5 项骨架）" 表格 | 报错退出，提示"review-notes.md 格式不合法，请用 /3-pg-propose 重新生成" |
| 表格行无法解析（如 `当前` 列为空）| 报错退出，列出非法行号 |
| 阻塞项被改为 SKIP | 报错退出，提示"阻塞项不可 SKIP" |
| 写入失败 | 报错退出并保留 review-notes.md 原状（不修改 checkbox 状态） |

---

## 原则

- **单一真相源**：v4 起 `review-notes.md` 是唯一评审文档，同时承担"诊断 + 决策表"两个职责
- **状态可视化**：checkbox (`[ ]` / `[x]` / `[~]`) 直观表达每条问题的处理状态
- **机械执行 + LLM 自由分层**：
  - `common_decisions` 选项固定 → **机械映射**（5.1 预定义）
  - `issue_decisions` 内容千变万化 → **LLM 自由修复**（5.2 读 recommended_action 推导）
- **幂等性**：相同 review-notes.md + 相同 checkbox 状态 → 相同产物
- **可回滚**：写入失败时 review-notes.md 状态不变（checkbox 未改），用户可修复后重试
- **向后兼容**：仍能读取 v3 双文档格式（review-decisions.yaml），但**不写回** YAML
- **不改业务代码**：只修改 pg-spec 产物文件 + review-notes.md，不碰业务代码
- **不重新跑 6 类自审**：本 skill 不重跑 pg-propose 3.5 检查，只读取其产物（review-notes.md）

---

## 扩展命令：`environment_override`

> **不在原 v4 refine 工作流中**。这是一个独立的便捷命令，让用户能在 review 阶段调整 per-change environment 选择（详见 [./pg-propose/references/orchestration-model.md](../pg-propose/references/orchestration-model.md)「per-change environment 选择」）。

### 触发方式

```
/4-pg-propose-refine environment_override <change> <stage> <new-env>
```

或交互式：

```
> 调整 <change> 的 <stage> 环境为 <new-env>
```

### 工作流程

1. **校验输入**：
   - `<change>` 目录存在
   - `.pg/changes/<change>/environment.yaml` 存在（否则报错："environment.yaml 不存在，请先跑 /3-pg-propose"）
   - `<stage>` 必须在 `.pg/project.yaml.stages[*].name` 中存在
   - `<new-env>` 必须是 `dev-local` / `dev-3tier` / `skip` 之一，或项目自定义的 environment 名称（必须存在于 `config.yaml.environments`）

2. **修改 environment.yaml**：
   - 读取 `.pg/changes/<change>/environment.yaml`
   - 修改 `<stage>` 字段值为 `<new-env>`
   - 写回（保持原有 YAML 格式与注释）

3. **同步 tasks.md 副本**（如有 `## Deployments` 段）：
   - 这是**可选同步**：旧 archive 下的 change 可能有 `## Deployments` 段作为可读副本
   - 找到 `## Deployments` 段中匹配 `<stage>` 的行，更新为 `<new-env>`
   - 找不到对应行 → 跳过（不报错，因为新生成的 change 不再含该段）

4. **输出 diff**：
   - 显示 environment.yaml 修改前后的 diff
   - 显示 tasks.md 修改前后的 diff（如有同步）
   - 等待用户确认后再写回（如未传 `--yes` 参数）

### 错误处理

| 场景 | 行为 |
|------|------|
| `<change>` 目录不存在 | 报错退出 |
| environment.yaml 缺失 | 报错退出，提示"必须先跑 /3-pg-propose 生成 environment.yaml" |
| `<stage>` 不在 config.yaml.stages 中 | 报错退出，列出合法 stage 名称 |
| `<new-env>` 不在 config.yaml.environments 中且不是 `skip` | 报错退出，列出合法 environment 名称 |

### 示例

```
# 把 snapshot-to-s3-backup-1 的 real-integration 改成 dev-3tier
/4-pg-propose-refine environment_override snapshot-to-s3-backup-1 real-integration dev-3tier

# 把 dev-mock-integration 标记为 skip
/4-pg-propose-refine environment_override snapshot-to-s3-backup-1 dev-mock-integration skip
```

### 与 review-notes.md 的关系

`environment_override` **不修改** review-notes.md（这是 review-decisions 的工件，不是环境选择）。

如需在 review 阶段记录此次 environment 调整的原因，请在 review-notes.md 的「自审发现的问题」或备注中**手工添加**说明。本 skill 不会自动写 review-notes.md。
