---
name: pg-propose
description: 生成一个变更提案，一次性产出所有产物（proposal、design、tasks、review-notes）。用户描述需求后，自动生成完整的提案文档与评审文档，供 pg-build 实现。
license: MIT
compatibility: 需要 `.pg/changes/` 目录结构和 `.pg/project.yaml` 统一配置文件。
metadata:
  author: pg
  version: "3.3"
---

# pg-propose

生成变更提案——创建变更目录并一次性产出所有产物：

- `proposal.md`（做什么、为什么做）
- `design.md`（怎么做、验证标准）
- `tasks.md`（按 stages × tracks 划分的实现步骤 + 验证描述）
- `execution-manifest.yaml`（按 tasks.md 结构化生成的 pipeline 编排清单）
- `1-propose-review/review-notes.md`（单文档评审）

产物就绪后，可执行 `/2.1-pg-propose-refine` 进一步评审。

## 文档导航

| 关心的问题 | 看哪里 |
|------------|--------|
| pg-propose 总流程 / 阶段划分 / 黑名单 | 本文件 |
| proposal.md 模板 / proposal_rules 注入 | [references/proposal-templates.md](./references/proposal-templates.md) |
| design.md 模板 / V-* 编号规则 | [references/design-templates.md](./references/design-templates.md) |
| tasks.md 模板 / 章节生成算法 / 各子章节模板 | [references/tasks-templates.md](./references/tasks-templates.md) |
| on_conditions / stages × tracks × modules 三层编排模型 | [references/orchestration-model.md](./references/orchestration-model.md) |
| `.pg/project.yaml` 字段索引 | [references/config-fields.md](./references/config-fields.md) |
| review-notes.md 格式 / 决策符号 | [references/review-notes-format.md](./references/review-notes-format.md) |
| 6 类自审清单（3.5.1-3.5.7） | [references/review-checklist.md](./references/review-checklist.md) |

> **本文件职责**：只承载「流程编排 + 阶段契约 + 黑/白名单」。所有模板字符串、字段定义、规则清单一律下放到 references/ 单一 SSOT。

---

## 输入

- **变更名称**（kebab-case，例如 `add-bucket-s3-info`）
- 来自探索阶段的口头 summary（如有）

> 变更名称不需要以日期开头，archive 目录下的变更以日期开头，是在变更完成时 archive 的日期，新建的变更名字不需要日期开头。

---

## 阶段一：创建目录与配置

### 1a. TodoWrite

立即创建 13 项 TodoWrite：

```
1.  [待开始] 创建变更目录
2.  [待开始] 加载项目上下文（AGENTS.md → context-summary.yaml）
3.  [待开始] 生成 proposal.md
4.  [待开始] 生成 design.md
5.  [待开始] 判定 affected_tracks & **scenario track(s) 启用决策**（核心：影响后续三个产物一致性）
6.  [待开始] 调用 pg-gen-tasks-skeleton.py 生成 tasks.md 骨架 + on-conditions-eval.md
         （必传 --scenario-decisions "track1=true,track2=auto,..." + --scenario-reason）
7.  [待开始] LLM 填充 tasks.md body
8.  [待开始] 调用 pg-gen-manifest.py 生成 execution-manifest.yaml
9.  [待开始] （条件）调用 pg-gen-scenario.py 生成 scenario.yaml
10. [待开始] 调用 pg-validate-proposal.py 三产物一致性校验
11. [待开始] 自审 6 类问题，写入 review-notes.md
12. [待开始] 决策复核 manifest（基于 on-conditions-eval.md 的 scenario_tracks_decision 段）
13. [待开始] 最终确认产物
```

### 1b. 确认变更名称

从用户输入或探索上下文获取变更名称（kebab-case）。如果用户未提供，直接根据语义生成一个（kebab-case）。

### 1c. 创建变更目录

```bash
mkdir -p ".pg/changes/<change-name>/1-propose-review"
```

验证目录已创建。更新 TodoWrite 第 1 项。

### 1d. 加载项目上下文（从 AGENTS.md 提取，经 context-summary.yaml 缓存）

```bash
bash .opencode/skills/pg-propose/scripts/check-review-cache.sh
```

- **`STATUS=HIT`** → 缓存有效，从输出 `---` 后读取 `context` 字段
- **`STATUS=MISS`** → 缓存未命中，末尾 `CURRENT_FINGERPRINTS:` 包含所有 AGENTS.md 指纹；执行 1d.2 重新提取

缓存未命中处理：读取所有 AGENTS.md → 提取 `context`（tech_stack / package / database_conventions / coding_conventions / design_patterns / domain）和 `rules`（review 检查条目列表）→ 写入 `.pg/context/summary.yaml`，含 `generated_at` / `fingerprints` / `context` / `rules`。

更新 TodoWrite 第 2 项。

### 1e. 获取管线配置（从 config.yaml 读取）

```bash
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-propose
```

从输出 JSON 获取：`rules` / `proposal_rules` / `test_strategy` / `coding_standards` / `tracks` / `stages`。

字段详细含义见 [references/config-fields.md](./references/config-fields.md)。

**⚠️ 命令执行位置规约**：

- 所有命令从**项目根路径**执行
- 需切换目录的命令在配置中显式写 `cd <dir> && <cmd>`（如 `test: cd <module-name> && mvn test`）
- `rebuild_and_restart` / `verify` 脚本应自包含 cwd 处理

### 1f. 加载 proposal_rules（结构化规则注入）

`.pg/project.yaml` 的 `proposal_rules` 段是结构化规则列表，按 `after_section` 字段注入到 `proposal.md` 模板。字段约定与注入算法见 [references/proposal-templates.md](./references/proposal-templates.md)「proposal_rules 注入机制」段。

---

## 阶段二：生成产物

按顺序生成：proposal.md → design.md → 判定类型 → tasks.md → execution-manifest.yaml。每个产物依赖前一个产物的内容。

每生成一个产物后，更新 TodoWrite 对应项。

### 2a. proposal.md

路径：`.pg/changes/<change-name>/proposal.md`

**模板 + proposal_rules 注入**见 [references/proposal-templates.md](./references/proposal-templates.md)。更新 TodoWrite 第 3 项。

### 2b. design.md

路径：`.pg/changes/<change-name>/design.md`

**模板 + V-* 编号规则**见 [references/design-templates.md](./references/design-templates.md)。更新 TodoWrite 第 4 项。

### 2c. 判定变更类型 & affected_tracks & scenario track(s) 启用决策

**affected_tracks 推导算法**见 [references/orchestration-model.md](./references/orchestration-model.md)「affected_tracks 推导」段。

判定流程：

1. 列举各组件改动（backend / agent / frontend / agent-proto / openapi-gen）
2. 生成 affected_tracks（如 `[backend, frontend]`）
3. **判定 scenario track(s) 启用决策**（v3.6 新增，支持多个 type=scenario 的 track，影响三个产物一致性）：
   - 启用决策基于以下问题：
     - 本次变更是否需要跨多个 role / service 协作验证？（如 frontend + backend + agent）
     - 是否引入新 API 端点需要端到端冒烟？
     - 改动是否涉及"跨模块联调场景"（不是单模块单测试可覆盖的）？
   - **启用 (`true`)**：上述任一为是 → 后续 tasks.md / manifest / scenario.yaml 都会包含该 scenario track 的章节
   - **禁用 (`false`)**：纯单模块改动（如纯文档、纯 SQL 迁移、纯单 API 增删）→ 三个产物都不含该 scenario track 的章节
   - `--scenario-decisions` 支持 per-track 决策：`"scenario-e2e=true,scenario-perf=false"`。空字符串 / 未指定时所有 scenario track 默认启用（常驻节点）
4. 把 `affected_tracks` 和 `scenario track(s) 决策 + 依据`写入 design.md 末尾的"变更类型判定"留痕小节

更新 TodoWrite 第 5 项。

**design.md 约束**（来自统一配置 `rules.design`）：

- 使用中文撰写
- UI 布局：使用 ASCII box 可视化界面结构
- 代码示例：使用标准 markdown 代码块（```），禁止用 ASCII 框包裹
- 前端列表页必须包含 ID 列
- design.md 必须包含"关键约束与契约"章节与"变更类型判定"留痕章节
- design.md 的每条 V-* 必须能验证 proposal.md"风险和注意事项"中的至少一条风险

### 2d. 生成 tasks.md（脚本外化）

> **核心变化（v3.2）**：tasks.md 的章节标题骨架、章节编号 N、simple/standard 分流、
> environment block quote、final-gate 章节、`on_conditions` 评估记录模板——
> **全部由 `pg-gen-tasks-skeleton.py` 机械生成**，LLM 只负责按骨架填充 body 内容。

**完整生成算法 + 各子章节模板**见 [references/tasks-templates.md](./references/tasks-templates.md)。

更新 TodoWrite 第 5.5 + 第 6 项。

#### 骨架脚本调用

```bash
python3 .opencode/skills/pg-propose/scripts/pg-gen-tasks-skeleton.py \
  --change <change-name> \
  --proposal-md .pg/changes/<change>/proposal.md \
  --affected-tracks "<track1>,<track2>,..." \
  --environment "<stage1>→<env1>,<stage2>→<env2>,..." \
  --selected-stages "<stage1>,<stage2>,..." \
  --scenario-decisions "track1=true,track2=auto" \
  --scenario-reason "<决策依据，1-2 句>"
```

参数来源：

| 参数 | 来源 |
|------|------|
| `--change` | 阶段 1b 确认的变更名 |
| `--proposal-md` | 阶段 2a 产物 |
| `--affected-tracks` | 阶段 2c 判定结果 |
| `--environment` | LLM 按 `config.stages[i].environment.selection_rules` 选择 |
| `--selected-stages` | LLM 根据 on_conditions 推导 |
| `--scenario-decisions` | **必填**：per-track scenario 启用决策，`"track1=true,track2=auto"`（空=全部 auto） |
| `--scenario-reason` | **必填**：决策依据（1-2 句，写入 eval.md） |

脚本输出：

- `.pg/changes/<change>/tasks.md`：完整骨架（所有 scenario track disabled 时不含 scenario 章节）
- `.pg/changes/<change>/1-propose-review/on-conditions-eval.md`：`on_conditions` 评估记录 + **scenario_tracks_decision 段（SSOT，per-track）**
- stdout JSON：sections 数组（章节清单 + 元数据 + `scenario_tracks` 字段）

LLM 读取 sections JSON 后，按 `references/tasks-templates.md`「各子章节模板」段填充 body。

#### 填充 body 的硬约束（简版）

- **禁止**修改任何 heading 文本、章节编号 N、stage/track/sub 前缀、标签
- **禁止**调整章节顺序或跳过任何章节
- **禁止**删除任何章节（包括 on_conditions 未命中的章节，heading 也保留）
- **禁止**在 verify 章节的命令步骤后追加具体 shell 命令

### 2e. execution-manifest.yaml

更新 TodoWrite 第 8 项。

LLM **不直接写** execution-manifest.yaml，通过 CLI 工具基于 tasks.md 自动生成。

**步骤**：

1. 生成 manifest：
   ```bash
   python3 .opencode/skills/pg-propose/scripts/pg-gen-manifest.py CHANGE_NAME
   ```
2. 校验 manifest ↔ tasks.md 一致性：
   ```bash
   python3 .opencode/skills/pg-propose/scripts/pg-validate-proposal.py manifest CHANGE_NAME
   ```
3. 失败处理（最多 2 轮）：
   - `manifest_section_missing` → 修正 tasks.md 章节 heading
   - `manifest_track_no_phases` → 补充 standard track 缺少的 phase 章节
   - `manifest_track_type_mismatch` → 确认 project.yaml 中 track type 正确
   - `manifest_environment_invalid` → 确认环境名在 project.yaml environments 中
   - `scenario_yaml_missing` → 跑 pg-gen-scenario.py 生成（scenario track 启用时）
   - `scenario_yaml_should_not_exist` → 删除 scenario-<track>.yaml（scenario track 禁用时）
   - `scenario_yaml_orphan` → 删除 scenario-<track>.yaml 或重新跑 2d-2e
   - 修正后回到步骤 1
4. 第 3 轮仍失败 → 将残留问题记录到 review-notes.md 的「阻塞」段
5. 成功 → 产物 `.pg/changes/CHANGE_NAME/execution-manifest.yaml` 自动生成

**产物依赖关系**：
- manifest 依赖 tasks.md（heading 格式 + 章节完整性），在 2e 完成后方可调用
- manifest 的 `scenario-<track>.enabled` 由 `on-conditions-eval.md` 的 `scenario_tracks_decision` 段决定（SSOT，禁用时不进入 manifest）

### 2f. 条件生成 scenario.yaml

更新 TodoWrite 第 9 项。

**触发条件**：仅当 `on-conditions-eval.md` 中 `scenario_tracks_decision` 段有至少一个 track 的 `enabled = true` 时执行。

**步骤**：

1. 调用脚本：
   ```bash
   python3 .opencode/skills/pg-propose/scripts/pg-gen-scenario.py CHANGE_NAME
   ```
   脚本自动：
- 读 `on-conditions-eval.md` 的 `scenario_tracks_decision` 段（SSOT）
    - 遍历每个 enabled=true 的 track，写 `scenario-<track-id>.yaml` skeleton（LLM 必填 Scenario 内容）
    - 无 enabled track → no-op（不写文件）
2. LLM 填充 scenario.yaml：将 skeleton 中的 S-example 替换为真实 Scenario
3. 校验：
   ```bash
   python3 .opencode/skills/pg-propose/scripts/pg-validate-proposal.py manifest CHANGE_NAME
   ```
4. 失败处理：
   - `scenario_yaml_missing` → 跑 pg-gen-scenario.py
   - `scenario_yaml_should_not_exist` → 删除 scenario.yaml
5. 成功 → 产物 `.pg/changes/CHANGE_NAME/scenario.yaml` 完成

### 2g. 三产物一致性校验

更新 TodoWrite 第 10 项。

```bash
python3 .opencode/skills/pg-propose/scripts/pg-validate-proposal.py manifest CHANGE_NAME
```

校验三产物（tasks.md / manifest / scenario-<track>.yaml）与 `on-conditions-eval.md` 的 `scenario_tracks_decision` SSOT 一致。

---

## 阶段三：自审（内联自 pg-propose-refine）

**本阶段不修改 proposal/design/tasks 本身**，只读产物 + AGENTS.md 规则 + context-summary.yaml，对以下 6 类问题做系统化检查，把发现写入 `.pg/changes/<change-name>/1-propose-review/review-notes.md`（新文件）。

更新 TodoWrite 第 11 项。

**6 类自审清单**（详见 [references/review-checklist.md](./references/review-checklist.md)）：

| 编号 | 检查类别 |
|------|---------|
| 3.5.1 | 范围一致性（proposal "包含/不包含" vs tasks 实际工作） |
| 3.5.2 | API 完整性（请求体/响应/状态码/权限/边界） |
| 3.5.3 | 设计缺陷（数据模型/异常处理/安全/性能/幂等） |
| 3.5.4 | 任务歧义（动作/上下文/验收/依赖/文件路径） |
| 3.5.5 | 验证流程（覆盖率/可测试性/负面场景/跨 stage 依赖） |
| 3.5.6 | 测试案例影响（受影响测试/新增测试/测试数据/测试隔离） |

**on_conditions 评估复核**（3.5.7）：见 [references/orchestration-model.md](./references/orchestration-model.md)「on_conditions & 机械评估」段。

**manifest 决策复核**（v3 新增，3.5.8）：见本节末尾"manifest 决策复核"段。
pg-gen-manifest.py 已在 manifest.tracks[].enabled / reason / on_conditions_eval
字段中写入机械评估结果；阶段三 LLM 复核时把决策表同步到 review-notes.md。

**review-notes.md 格式 + 决策符号 + 5 项通用决策默认值**：见 [references/review-notes-format.md](./references/review-notes-format.md)。

review-notes.md 必含段：

- 5 项通用决策表（error_response_strategy / auth_scope / data_migration_strategy / transaction_boundary / frontend_interaction_style）
- on_conditions 评估记录段（从 `on-conditions-eval.md` 合并；含 `scenario_tracks_decision` 段）
- **manifest 决策复核段**（v3 新增，从 `execution-manifest.yaml` 的 tracks[].enabled / reason / on_conditions_eval 同步）
- 6 类自审发现的问题清单（按 阻塞 / 重要 / 建议 三档）
- 一致性检查结果（✅/⚠️/❌）
- 评审说明段（编辑指引）

### manifest 决策复核（v3 新增）

`pg-gen-manifest.py` 在生成 `execution-manifest.yaml` 时，已为每个 track 填入：

- `enabled: bool` — 是否启用（pg-build 派发唯一依据）
- `reason: str` — 决策理由（on_conditions 命中项 + LLM 决策依据）
- `on_conditions_eval: {matched_rules, unmatched_rules, path_hit_count, semantic_hit_count}` — 机械评估结果
- `target_module: str`（e2e track 必填）— 限定修复模块
- `scenario_yaml: str`（scenario track 必填）— 指向 `scenario-<track-id>.yaml`

阶段三 LLM 复核时，需要把以下表格同步写入 review-notes.md：

| track | 机械评估 | manifest.enabled | 理由 | 一致 | 最终 |
|-------|---------|-----------------|------|------|------|
| backend-e2e | on_conditions 全部未命中 | false | LLM 未列入 affected_tracks | ✅ | [ ] |
| frontend-e2e | on_conditions 命中 2 条 | true | 命中 + LLM 决策启用 | ✅ | [ ] |
| agent-e2e | on_conditions 命中 1 条 | true | 命中 + LLM 决策启用 | ✅ | [ ] |
| scenario-<track> | （来自 `scenario_tracks_decision` SSOT） | true / false | LLM 阶段二 2c 决策 + 依据 | ✅ | [ ] |

**复核动作**：

1. 对每行"最终"勾选 `[x]`（同意 manifest 决策）或 `[~]` + 写依据（覆盖）
2. 不一致项（如 on_conditions 未命中但 enabled=true）必须在 review-notes.md 标注"建议禁用"或"建议人工介入"
3. e2e track 必须确认 target_module 填写正确
4. **scenario track 行的 manifest.enabled 必须与 `on-conditions-eval.md` 的 `scenario_tracks_decision` 对应 track 的 enabled 完全一致**（SSOT）
5. manifest 缺 enabled 字段的旧 change 走 pg-propose-refine 重新生成

### scenario track 一致性约束

scenario track 是常驻 track，但 LLM 仍可在阶段二 2c 决策为某个 track 设置 `enabled = false`（纯单模块改动）。**但三个产物（tasks.md / manifest / scenario-<track>.yaml）必须一致**：

| `scenario_tracks_decision` | tasks.md scenario 章节 | manifest scenario track | scenario-<track>.yaml |
|---------------------------|------------------------|------------------------|----------------------|
| track-A: `enabled=true` | ✅ 存在 | ✅ 存在 + `enabled=true` + `scenario_yaml` 字段 | ✅ `scenario-track-A.yaml` 存在 |
| track-B: `enabled=false` | ❌ 不存在 | ❌ 不存在 | ❌ 不存在 |

违反时 `pg-validate-proposal.py` 会报 `scenario_yaml_missing` / `scenario_yaml_should_not_exist` / `scenario_yaml_orphan` 错误，必须修复。

### 阶段三行为契约

- **禁止**使用 `question` tool 中断流程
- **禁止**自动修改 proposal/design/tasks 主体内容
- **禁止**手工修改 `execution-manifest.yaml` 的 `enabled` / `reason` / `on_conditions_eval` 字段
  - 如需变更，**必须重跑** `pg-gen-tasks-skeleton.py` + `pg-gen-manifest.py` + `pg-gen-scenario.yaml`，让 SSOT 自动同步
- **禁止**在 scenario track 启用时手工编辑 `tasks.md` 删除 scenario 章节
   - 必须改 `--scenario-decisions "track=false"` 重跑 2d
- **唯一允许的产物修改**：纯格式问题（markdown 标题层级错乱、代码块语言标记缺失、明显笔误），且修改后必须在 review-notes.md 中留痕记录"格式修正: X→Y"
- 自审完成后更新 TodoWrite 第 11 项为完成

---

## 阶段四：最终确认

更新 TodoWrite 第 13 项。

产物生成完成且单文档评审（review-notes.md）已写入后，更新 TodoWrite 全部标记为完成。直接向用户展示产物摘要：

- 变更名称、产物位置、已创建文件（5+ 个产物）：
  - `.pg/changes/<change>/proposal.md`（必填）
  - `.pg/changes/<change>/design.md`（必填）
  - `.pg/changes/<change>/tasks.md`（必填）
  - `.pg/changes/<change>/execution-manifest.yaml`（必填）
  - `.pg/changes/<change>/scenario-<track>.yaml`（**每个启用**的 scenario track 一个）
  - `.pg/changes/<change>/1-propose-review/on-conditions-eval.md`（必填）
  - `.pg/changes/<change>/1-propose-review/review-notes.md`（必填）
- 报告 `scenario_tracks_decision` 状态（从 on-conditions-eval.md 读取）：
  - 每个 scenario track 的 `enabled` 状态：`{track_id}: {enabled/disabled}`
  - enabled track → tasks.md / manifest / scenario-<track>.yaml 三产物均含对应章节
  - disabled track → 上述三产物均不含该 track（避免冗余）
- review-notes.md 内容摘要：
  - 通用决策：`5 项已预填推荐值`
  - 问题清单：`阻塞 X / 重要 Y / 建议 Z`（每条以 checkbox `[ ]` 起始）

告知用户：

- 如希望调整决策项，直接编辑 `.pg/changes/<change-name>/1-propose-review/review-notes.md`：
  - 通用决策：修改表格的"当前"列
  - 问题清单：把 `[ ]` 改为 `[x]`（已修复）或 `[~]` + 加 `> SKIP：理由`（豁免）
  - **scenario track(s) 决策**：修改 `on-conditions-eval.md` 的 `scenario_tracks_decision` 段（不建议，需重跑三个生成脚本）
- 编辑后调用 `/2.1-pg-propose-refine {change-name}` 应用决策
- 下一步可执行 `/3-pg-build {change-name}` 开始实现
- 如希望修复 review-notes.md 中的"阻塞/重要"问题后再 build，回复"修复 review-notes 中的问题"，由本会话继续处理

---

## 产物生成指导原则

- `context`（来自 AGENTS.md，经 context-summary.yaml 缓存）和 `rules`（来自 config.yaml）是给你的约束，不可复制到产物中
- 每个产物文件写入后验证文件存在
- 如果变更名称已存在，询问用户是继续还是新建

---

## 产物清单（硬约束）

每个 change 在 `.pg/changes/<change>/` 下生成 5+ 个产物文件（前 4 个 + 1 评审 + N 个条件性 scenario-<track>.yaml，N=启用 scenario track 数）：

| 产物 | 写入位置 | 何时生成 | 必填 |
|------|---------|---------|------|
| `proposal.md` | `.pg/changes/<change>/proposal.md` | 阶段 2a | ✅ 必填 |
| `design.md` | `.pg/changes/<change>/design.md` | 阶段 2b | ✅ 必填 |
| `tasks.md` | `.pg/changes/<change>/tasks.md` | 阶段 2d（pg-gen-tasks-skeleton.py 生成，含 scenario 章节当且仅当至少一个 scenario track 启用） | ✅ 必填 |
| `execution-manifest.yaml` | `.pg/changes/<change>/execution-manifest.yaml` | 阶段 2e（pg-gen-manifest.py 生成，含 scenario track 当且仅当对应 track 启用） | ✅ 必填 |
| `on-conditions-eval.md` | `.pg/changes/<change>/1-propose-review/on-conditions-eval.md` | 阶段 2d（pg-gen-tasks-skeleton.py 生成，含 `scenario_tracks_decision` SSOT 段） | ✅ 必填 |
| `review-notes.md` | `.pg/changes/<change>/1-propose-review/review-notes.md` | 阶段 3（LLM 自审） | ✅ 必填 |
| `scenario-<track>.yaml` | `.pg/changes/<change>/scenario-<track>.yaml` | 阶段 2f（pg-gen-scenario.py 生成，**每个启用**的 scenario track 一个文件） | ⚠️ 条件必填 |

### 三产物一致性约束（v3.6）

`tasks.md` / `execution-manifest.yaml` / `scenario-<track>.yaml` 三个产物严格一致，无冗余无回退：

- `on-conditions-eval.md` 的 `scenario_tracks_decision` 段是 SSOT（per-track）
- `pg-gen-tasks-skeleton.py` / `pg-gen-manifest.py` / `pg-gen-scenario.py` 三个脚本都从 SSOT 派生
- `pg-validate-proposal.py` 校验三产物与 SSOT 一致

### scenario.yaml 生成指引（v3.6+，仅当 scenario track 启用）

**SSOT**：scenario-<track>.yaml 是 scenario-execute agent 的唯一输入，**禁止** scenario-execute agent 重写或修改。
修改走 `pg-propose-refine` 流程回到 propose 阶段。

**生成路径**：阶段 2f 调用 `pg-gen-scenario.py` 自动写盘 `.pg/changes/<change>/scenario-<track>.yaml` skeleton（LLM 必填 Scenario 内容）。每个启用的 scenario track 生成一个独立文件。

**schema**（YAML）：

```yaml
scenarios:
  - scenario_id: S-<unique-name>          # 全局唯一，命名风格 S-<动词>-<对象>-<结果>
    critical: true                        # true=禁止 SKIP；false=可记录 SKIPPED 后继续
    description: <一句话描述验证目标>
    given:
      - <前置条件 1>
      - <前置条件 2>
    when:
      - name: <动作名>
        method: <HTTP method | db query>
        url: <endpoint 或 SQL>
        body: <payload>                    # 可选
        expect_status: <int>               # 期望响应码
    then:
      - status_code == <int>
      - response.<field> matches <regex>
      - response.<field> == <literal>
    and:                                   # cleanup，可选
      - name: <cleanup 名>
        action: <HTTP DELETE | db DELETE>
    evidence:
      - <curl 输出文件路径>
      - <journalctl 片段路径>
```

**Scenario 编排规则**：

1. **顺序写**：所有 `critical: true` Scenario 排在 `critical: false` 之前
2. **每个 Scenario 含 6 段**（given / when / then / and / evidence / critical）
3. **`and` cleanup 段必备**：每个 Scenario 都含 `and`，避免失败时脏数据污染
4. **Scenario 数量**：1-5 个；超出后提示用户拆分（避免单次 Phase 不可控超时）

**严禁生成**以下文件（v1 遗留物，pg-build 不再读取）：

- ❌ `environment.yaml` —— per-change 的环境选择已写入 `execution-manifest.yaml` 的 `stages[i].environment` 字段，由 `pg-build` 直接读取

任何 stage 缺少必填产物文件 → workflow_failed 终止。多生成产物文件 → 后续 pg-build 会忽略，但污染产物目录。

---

## ⛔ 禁令

下列操作在**整个提案阶段**均被禁止：

- ❌ 严禁修改任何业务代码文件
- ❌ 严禁执行 lint、typecheck、test 等验证命令
- ❌ 严禁启动任何服务（backend/frontend）

---

## 文档变更记录

- **v3.4（2026-07-12）**：适配 pg-build verify / gate 按 track 关闭。
  - `pg-gen-tasks-skeleton.py` 的 `build_sections` 按 `verify_enabled` / `gate_enabled` / `code_review_enabled` 联合过滤 STANDARD_SUBS，允许 2-5 sub。
  - `manifest.schema.json`：`minProperties=2`、`required=["test","dev"]`。
  - `pg-validate-proposal.py`：必填逻辑改为 test+dev 强必填 + verify/gate 至少一项（防止绕过所有运行时质量门）；返回新错误码 `_no_quality_gate`。
  - `references/tasks-templates.md`：track:verify / track:gate 章节末尾补"何时本章节不出现"小节。
  - `references/review-checklist.md`：新增 §3.5.8 Verify / Gate 一致性。
  - 协调：pg-build `TrackState` 增加 `verify_enabled` / `gate_enabled` 字段（与 v3.x `code_review_enabled` 对齐，默认 True）。

- **v3.3（2026-07-08）**：适配 pg-build v2.6 code-review 阶段。
  - `pg-gen-tasks-skeleton.py` 的 `STANDARD_SUBS` 增加 `review`；`build_sections` 按 `tracks.<id>.code_review_enabled` 决定 4/5 sub。
  - `pg-gen-manifest.py` / `manifest.schema.json` / `pg-validate-proposal.py` 适配：phase_prompts 4 必填 + review optional，minProperties=4/maxProperties=5。
  - `references/tasks-templates.md` 新增 `track:review` 章节模板与不变量说明。

  - 协调：pg-build 内部 `TrackState.code_review_*` 字段：`code_review_enabled` / `code_review_profiles` / `code_review_profile` / `code_review_languages`。

- **v3.2（前置版本）**：tasks.md 章节标题骨架 + 章节编号 N + simple/standard 分流 + on_conditions 评估注释全部由 `pg-gen-tasks-skeleton.py` 机械生成。

- **v3.1（2026-07-08）**：重构 SKILL.md 与 references 的内容分工。SKILL.md 仅保留流程编排、阶段契约、黑/白名单；模板字符串、字段定义、规则清单全部下放到 references/ 单一 SSOT；顶部新增「文档导航」routing table。本变更由用户对 add-user-reset-password 提案执行 `pg-propose` 后自审暴露的问题驱动，详见 `.pg/changes/add-user-reset-password/`。
- **v3.0**：初始当前形态。
