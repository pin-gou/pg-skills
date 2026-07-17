# Review Notes Format

本文档定义 `.pg/changes/<change-name>/1-propose-review/review-notes.md` 的单文档格式规范。

`review-notes.md` 是**唯一**评审文档，同时承担"自审诊断"与"用户决策表"两个职责。

> **6 类自审清单来源**：见 [./review-checklist.md](./review-checklist.md)

---

## 模板

路径：`.pg/changes/<change-name>/1-propose-review/review-notes.md`

```markdown
# {change-name} 评审

**生成时间**：{当前时间戳}
**自审依据**：.pg/context/summary.yaml 的 rules + 6 类检查清单

## 通用决策（5 项骨架）

| 决策项 | 选项 | 当前 | 推荐 | 备注 |
|--------|------|------|------|------|
| error_response_strategy | A 全局统一 / B 按模块 | A | A | 沿用 <module-name> 全局 ErrorResponse |
| auth_scope | platform / tenant / project | platform | platform | 现有路径无 scope 前缀 |
| data_migration_strategy | A Flyway / B 应用层兼容 / C 无需迁移 | C | C | 纯 SELECT 投影扩列，无 schema 变更 |
| transaction_boundary | A 单 service @Transactional / B 分布式 / C 最终一致 | C | C | 纯查询 |
| frontend_interaction_style | A 弹窗 / B 抽屉 / C 独立页 / D 行内编辑 | B | B | 沿用 HostEditDrawer |

## 自审发现的问题

### 阻塞（必须修复后再 build）
- [ ] （无）

### 重要（建议修复后再 build）
- [ ] **{文件名或章节} {简述}**
  - 目标：{目标文件与章节}
  - 推荐动作：{具体修改建议}
  - SKIP 允许：是

### 建议（可选优化）
- [ ] **{文件名或章节} {简述}**
  - 目标：{目标文件与章节}
  - 推荐动作：{具体修改建议}
  - SKIP 允许：是

## 一致性检查结果

- 范围一致性：✅
- API 完整性：✅
- 设计缺陷：⚠️（JOIN 类型依据未明示）
- 任务歧义：⚠️（token 获取未明示）
- 验证流程：✅
- 测试案例影响：✅

## 评审说明

> 编辑方式：
> - 接受推荐修复：保留 `[ ]`，调用 `/2.1-pg-propose-refine {change-name}` 自动 FIX
> - 跳过修复：把 `[ ]` 改为 `[~]`，并在条目下加 `> SKIP：{理由}`
> - 反向（建议 → 修）：把 `[ ]` 改为 `[ ]`，加 `> FIX：{说明}`
> - 标记已修复：把 `[ ]` 改为 `[x]`，加 `- 修复：{摘要}` 与 `- 修复时间：{timestamp}`
```

---

## 5 项通用决策默认值来源

| 决策项 | 推荐值如何确定 |
|--------|---------------|
| `error_response_strategy` | 固定为 A（项目硬编码规范） |
| `auth_scope` | LLM 根据 design.md "影响面"章节推导（platform/tenant/project） |
| `data_migration_strategy` | bugfix→C / 新增表→A / 字段变更或加索引→B |
| `transaction_boundary` | 纯查询→C / 单 service 写→A / 跨服务→B |
| `frontend_interaction_style` | LLM 根据 design.md "组件设计"章节推导 |

### 默认值覆盖优先级

1. 项目硬编码（context-summary.yaml 的 rules 中的硬性规范）
2. design.md "影响面"章节中的明确声明
3. LLM 自由推导（无明确依据时的最佳猜测）

---

## 写入规则

- **5 项通用决策**：必须全部预填，`当前` 列初始化为推荐值
- **每条问题格式**：
  - 标题行：`- [ ] **{文件名或章节} {简述}**`（标题加粗）
  - 子行：`- 目标：` / `- 推荐动作：` / `- SKIP 允许：`
- **严重度三档**：`阻塞` / `重要` / `建议`
- **三个"无问题"类别也要保留标题**，写 `- [ ] （无）`
- **一致性检查结果**：⚠️ 表示有"重要"问题，❌ 表示有"阻塞"问题，✅ 表示无问题
- **末尾"评审说明"段**：只放编辑指引与决策应用流程，不放具体决策内容
- **如果 review-notes.md 已存在**（在多次 propose 场景），**追加**而非覆盖，并在文件顶部标注追加时间

---

## 推荐动作规范（v4.1 新增）

> **目的**：防止 refine 阶段误改业务代码（详见 [pg-propose-refine SKILL.md](../pg-propose-refine/SKILL.md) 的 Scope Boundary Contract 章节）。

### "目标"字段约束

| 类型 | 示例 | 是否允许 |
|------|------|---------|
| 产物文件名 | `proposal.md` / `design.md` / `tasks.md` / `review-notes.md` | ✅ |
| 产物内的章节定位 | `tasks.md` 第 11.1 章节 / `design.md` V-backend-3 验证项 / `review-notes.md` 第 1 条 | ✅ |
| 业务代码文件路径 | `.pg/hooks/env-dev-local-clean.sh` / `webvirt-backend/.../InstanceService.java` | ❌ 禁止 |
| 配置文件路径 | `application.yml` / `.env` / `vite.config.ts` | ❌ 禁止 |

### "推荐动作"字段约束

✅ **正确**：

```markdown
- 目标：`tasks.md` 第 11.1 章节
- 推荐动作：在 `dev.agent:test` 中补充 mock Exec.Command 的 vxlan-unicast 测试用例
```

✅ **正确**（需要改业务代码时，必须翻译为产物内的实施任务）：

```markdown
- 目标：`tasks.md` 第 2.1 章节
- 推荐动作：细化 step [3/3] 为 3.1/3.2/3.3 子步骤，新增 3.3 iptables MASQUERADE 删除逻辑（具体代码改动由 pg-build 阶段执行）
```

❌ **错误**（会导致 refine 越界）：

```markdown
- 目标：`.pg/hooks/env-dev-local-clean.sh`
- 推荐动作：在 step [3/3] 新增子步骤 3.3
```

### 翻译规则

如果 `recommended_action` 字面指向业务代码，LLM **必须**改写为以下两种之一：

1. **指向 `tasks.md` 的实施任务**：把 "改 X 文件" 翻译为 "在 `tasks.md` 第 N 章 [stage].[track]:dev 加一条子任务，描述改 X 文件的步骤"
2. **指向 `design.md` 的验证项**：把 "X 应具备 Y 能力" 翻译为 "在 `design.md` V-{track}-N 加一条验证项，描述如何验证 Y 能力"

### 机械检查

`pg-auto-refine-check.py <change>` 会在主入口解析 `review-notes.md` 中所有 "目标" 字段，**绝对路径必须在 `.pg/changes/<change>/` 之下**。越界 → exit code 3，提示修正后重跑。

---

## 编辑决策符号表

| 符号 | 含义 | 后续动作 |
|------|------|----------|
| `[ ]` | 待定，保留推荐修复 | `/2.1-pg-propose-refine {change-name}` 自动 FIX |
| `[~]` | SKIP，跳过此项 | 在条目下加 `> SKIP：{理由}`，直接进入 build |
| `[ ]` + `> FIX：` | 反向决策（建议 → 修） | `/2.1-pg-propose-refine {change-name}` 强制修复 |
| `[x]` | 已修复 | 加 `- 修复：{摘要}` + `- 修复时间：{timestamp}` |

---

## 应用流程

1. **生成 review-notes.md**：pg-propose 阶段三自动生成 5 项决策 + 6 类问题清单
2. **用户审阅**：用户阅读 review-notes.md，决定每个问题的处理（修复 / 跳过）
3. **应用决策**：
   - 接受推荐修复 → 调用 `/2.1-pg-propose-refine {change-name}`，skill 自动按决策 FIX
   - 全部 SKIP → 直接调用 `/3-pg-build {change-name}`
   - 部分修复 → 调用 `/2.1-pg-propose-refine {change-name}` 处理剩余推荐修复项
4. **进入 build**：build 阶段读取 review-notes.md（已被 refine 修改过）作为最终决策依据

---

## 相关文档

- 6 类自审清单：[./review-checklist.md](./review-checklist.md)
- 编排模型：[./orchestration-model.md](./orchestration-model.md)
