---
name: pg-propose
description: 生成一个变更提案，一次性产出所有产物（proposal、design、tasks、review-notes）。用户描述需求后，自动生成完整的提案文档与评审文档，供 pg-build 实现。
license: MIT
compatibility: 需要 `.pg/changes/` 目录结构和 `.pg/project.yaml` 统一配置文件。
metadata:
  author: pg
  version: "3.0"
---

# pg-propose

生成变更提案——创建变更目录并一次性产出所有产物：
- `proposal.md`（做什么、为什么做）
- `design.md`（怎么做、验证标准）
- `tasks.md`（按 stages × tracks 划分的实现步骤 + 验证描述）
- `execution-manifest.yaml`（按 tasks.md 结构化生成的 pipeline 编排清单）
- `1-propose-review/review-notes.md`（单文档评审）

产物就绪后，可执行 `/2.1-pg-propose-refine` 进一步评审。

> **编排模型**：stages × tracks × modules 三层模型——见 [./references/orchestration-model.md](./references/orchestration-model.md)
> **config.yaml 字段索引**——见 [./references/config-fields.md](./references/config-fields.md)
> **产物模板**：
> - proposal → [./references/proposal-templates.md](./references/proposal-templates.md)
> - design → [./references/design-templates.md](./references/design-templates.md)
> - tasks → [./references/tasks-templates.md](./references/tasks-templates.md)
> **评审**：
> - 自审清单 → [./references/review-checklist.md](./references/review-checklist.md)
> - review-notes 格式 → [./references/review-notes-format.md](./references/review-notes-format.md)

---

## 输入

- **变更名称**（kebab-case，例如 `add-bucket-s3-info`）
- 来自探索阶段的口头 summary（如有）

> 变更名称不需要以日期开头，archive 目录下的变更以日期开头，是在变更完成时 archive 的日期，新建的变更名字不需要日期开头。

---

## 阶段一：创建目录与配置

### 1a. TodoWrite

立即创建 8 项 TodoWrite：

```
1. [待开始] 创建变更目录
2. [待开始] 加载项目上下文（AGENTS.md → context-summary.yaml）
3. [待开始] 生成 proposal.md
4. [待开始] 生成 design.md
5. [待开始] 判定变更类型 & affected_tracks
6. [待开始] 调用 pg-gen-tasks-skeleton.py 生成 tasks.md 骨架 + on_conditions 评估模板
7. [待开始] LLM 填充 tasks.md body + 生成 execution-manifest.yaml + 校验
8. [待开始] 自审产物 + 复核 on_conditions 评估，写入单文档 review-notes.md
```

### 1b. 确认变更名称

从用户输入或探索上下文获取变更名称（kebab-case）。如果用户未提供，直接根据语义生成一个（kebab-case）。

### 1c. 创建变更目录

```bash
mkdir -p ".pg/changes/<change-name>/1-propose-review"
```

验证目录已创建。更新 TodoWrite 第 1 项。

### 1d. 加载项目上下文（从 AGENTS.md 提取，经 context-summary.yaml 缓存）

#### 1d.1 检查缓存

```bash
bash .opencode/skills/pg-propose/scripts/check-review-cache.sh
```

脚本输出：
- **`STATUS=HIT`** → 缓存有效，`---` 后即为 `.pg/context/summary.yaml` 完整内容。从输出中读取 `context` 字段
- **`STATUS=MISS`** → 缓存未命中，末尾 `CURRENT_FINGERPRINTS:` 包含所有 AGENTS.md 指纹信息。执行 1d.2 重新提取

#### 1d.2 缓存未命中：从 AGENTS.md 提取并缓存

1. 读取所有 AGENTS.md 文件（根目录 + 各模块）
2. 提取 `context` 字段：tech_stack / package / database_conventions / coding_conventions / design_patterns / domain
3. 提取 `rules`：review 检查条目列表
4. 将结果写入 `.pg/context/summary.yaml`，包含 `generated_at` / `fingerprints` / `context` / `rules`

#### 1d.3 读取项目上下文

从 context-summary.yaml 读取 `context` 字段，作为项目技术栈、编码约定、设计模式的输入。更新 TodoWrite 第 2 项。

### 1e. 获取管线配置（从 config.yaml 读取）

```bash
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-propose
# ↑ stdout 输出配置 JSON；内建脚本存在性校验，exit code ≠ 0 → 修复 .pg/project.yaml 再继续
# ⚠️ stderr 会输出"命令执行位置规约"提示，所有命令从项目根路径执行
```

从输出 JSON 获取（注意：`context` 已从 config.yaml 移除，改由 AGENTS.md 提供）：
- `rules`：各产物的生成规则（proposal/design/tasks 规则），扁平 list 形式，仅作 review 提醒，不会真的注入到产物
- `proposal_rules`：结构化规则列表，按 `after_section` 字段注入到 `proposal.md` 模板
- `test_strategy`：测试策略（TDD、覆盖率目标）
- `coding_standards`：编码规范
- `tracks`：各 track 配置（modules / max_fix_retries / fix_routing）
- `stages`：阶段编排（顺序执行的 stage 列表，每 stage 包含 track 列表 + requires_deployment + test_key + gate）

**字段详细含义**：见 [./references/config-fields.md](./references/config-fields.md)。

**⚠️ 命令执行位置规约**：
- 所有命令从**项目根路径**执行
- 需切换目录的命令在配置中显式写 `cd <dir> && <cmd>`（如 `test: cd <module-name> && mvn test`）
- `rebuild_and_restart` / `verify` 脚本应自包含 cwd 处理（如 `rebuild_and_restart: bash scripts/start-backend.sh`，脚本内部自己 cd）

### 1f. 加载 proposal_rules（结构化规则注入）

`.pg/project.yaml` 的 `proposal_rules` 段是一组结构化规则，用于在生成 `proposal.md` 模板时**自动注入**必填章节，避免在 SKILL.md 硬编码"必填章节"导致 SKILL 变得项目特定化。

**字段约定**与**注入算法**详见 [./references/proposal-templates.md](./references/proposal-templates.md)「proposal_rules 注入机制」段。

---

## 阶段二：生成产物

按顺序生成：proposal.md → design.md → 判定类型 → tasks.md → execution-manifest.yaml。每个产物依赖前一个产物的内容。
每生成一个产物后，更新 TodoWrite 对应项。

### 2a. proposal.md

路径：`.pg/changes/<change-name>/proposal.md`

更新 TodoWrite 第 3 项。

**模板**：见 [./references/proposal-templates.md](./references/proposal-templates.md)。

### 2b. design.md

路径：`.pg/changes/<change-name>/design.md`

更新 TodoWrite 第 4 项。

**模板与 V-* 编号规则**：见 [./references/design-templates.md](./references/design-templates.md)。

V-* 编号规则摘要：
- 编号格式：`V-{track_id}-N`（如 `V-backend-1`）
- 跨 stage 全局递增：dev-isolated 的 V-backend-1、dev-mock-integration 的 V-backend-2、real-integration 的 V-backend-3
- 章节标题：`### {stage.name} {track_id} Verification Criteria`

### 2c. 判定变更类型

更新 TodoWrite 第 5 项。

**affected_tracks 推导算法**：见 [./references/orchestration-model.md](./references/orchestration-model.md)「affected_tracks 推导」段。

判定流程：
1. 列举各组件改动（backend / agent / frontend）
2. 生成 affected_tracks（如 `[backend, frontend]`）
3. 记录判定结果，供生成 tasks.md 时引用

**约束**（来自统一配置 `rules.design`）：
- 使用中文撰写
- UI 布局：使用 ASCII box 可视化界面结构
- 代码示例：使用标准 markdown 代码块（```），禁止用 ASCII 框包裹
- 前端列表页必须包含 ID 列
- design.md 必须包含"关键约束与契约"章节与"变更类型判定"留痕章节
- design.md 的每条 V-* 必须能验证 proposal.md"风险和注意事项"中的至少一条风险

### 2d. 生成 tasks.md 骨架（脚本外化）

更新 TodoWrite 第 5.5 + 第 6 项。

> **核心变化（v3.2）**：tasks.md 的章节标题骨架、章节编号 N、simple/standard 分流、
> environment block quote、final-gate 章节、`on_conditions` 评估记录模板——
> **全部由 `pg-gen-tasks-skeleton.py` 机械生成**，LLM 只负责按骨架填充 body 内容。

#### 2d.1 调用骨架生成脚本

LLM 准备以下 4 个参数：

| 参数 | 来源 | 示例 |
|------|------|------|
| `--change` | 阶段 1b 确认的变更名 | `add-bucket-s3-info` |
| `--proposal-md` | 阶段 2a 产物 | `.pg/changes/<change>/proposal.md` |
| `--affected-tracks` | 阶段 2c 判定结果 | `backend,frontend` |
| `--environment` | 阶段 2c LLM 按 selection_rules 选择 | `dev→dev-local` |

```bash
python3 .opencode/skills/pg-propose/scripts/pg-gen-tasks-skeleton.py \
  --change <change-name> \
  --proposal-md .pg/changes/<change>/proposal.md \
  --affected-tracks "<track1>,<track2>,..." \
  --environment "<stage1>→<env1>,<stage2>→<env2>,..."
```

脚本输出两个文件：

- **`.pg/changes/<change>/tasks.md`** —— 完整骨架（含所有 heading + 默认 body + HTML 注释 + final-gate）
- **`.pg/changes/<change>/1-propose-review/on-conditions-eval.md`** —— `on_conditions` 评估记录模板（供阶段三 review 复核）

脚本 stdout 输出 JSON，含 `sections` 数组（每个章节的 `n`/`stage`/`track`/`sub`/`is_affected`/`is_simple` 标记），
LLM 据此按顺序填充 body。

#### 2d.2 脚本生成的骨架结构

对每个 stage × track × sub，脚本**全量展开**生成章节（不再按 on_conditions 跳过 heading）：

| Track 类型 | 章节形态 |
|------------|---------|
| standard (TDVG) | 4 个章节：`test` / `dev` / `verify` / `gate` |
| simple (e.g. `openapi-gen`) | 1 个章节（dispatch to pg-build/simple） |
| final-gate | 1 个章节（追加在末尾） |

每个章节默认 body：

- `is_affected == true` 的 standard track 章节 → 占位 `- [ ] N.M 待 LLM 填充`
- `is_affected == false` 或 `simple` → `- 无` 或 simple track 占位任务
- 每个 verify 章节末尾自动插入 `**Evidence 要求**` 占位块

每个章节 heading 下挂 HTML 注释块，记录：

- stage 级 `on_conditions` 规则的机械评估（path-glob / keyword 双维度）
- track 级 `on_conditions` 规则的机械评估
- 评估结果标记（命中/未命中 + 依据）

**示例**（tasks.md 局部）：

```markdown
> - **environment 选择**：dev → dev-local

## 1. prepare-env-scripts.env-scripts:test - prepare-env-scripts 测试先行（unit）

<!-- on_conditions_eval:
     stage=prepare-env-scripts
     规则: 本变更 affected_paths 命中 .pg/hooks/** 任一路径
       → 机械评估: 命中 (path hit)
     规则: 本变更 proposal.md 包含对环境层脚本或 fixtures 的修改描述
       → 机械评估: 未命中 (no hit)
-->
- 无

## 2. dev.backend:test - dev 测试先行（unit）

<!-- on_conditions_eval:
     stage=dev (常驻, 无 on_conditions)
     track=backend (常驻, 无 on_conditions)
-->
- [ ] 2.1 编写 dev 测试：待 LLM 填充

## 3. dev.backend:dev - 实现开发

- [ ] 3.1 实现功能：待 LLM 填充
```

#### 2d.3 LLM 填充 body

按 `sections` JSON 数组顺序，对**所有非占位 body** 填充真实任务：

- **affected standard track 的 test/dev/verify/gate 章节**：替换 `- [ ] N.M 待 LLM 填充` 为真实任务（test/dev/verify/gate 模板见 [./references/tasks-templates.md](./references/tasks-templates.md)「各子章节模板」段）
- **affected standard track 的 verify 章节**：保留 `**Evidence 要求**` 占位块（脚本已生成）
- **affected standard track 的 gate 章节**：保持 `- 无`（编排器自动派遣 gate agent）
- **unaffected standard track 的章节**：保持 `- 无`（不删除，heading 也不动）
- **simple track 章节**：保持 `执行 tracks.<id>.commands` 占位（runner 派遣 pg-build/simple agent）
- **final-gate 章节**：保持脚本生成的 3 条标准任务（收集 Gate Assessment / 检查跨 stage 依赖 / 输出 Final Gate Assessment）

**硬约束**：

- **禁止**修改任何 heading 文本、章节编号 N、stage/track/sub 前缀、标签
- **禁止**调整章节顺序或跳过任何章节
- **禁止**删除任何章节（即使 on_conditions 机械评估为"未命中"，heading 也保留；body 保持 `- 无`）
- **禁止**在 verify 章节的命令步骤后追加具体 shell 命令（脚本已生成 runner 注入占位）

#### 2d.4 与原生成算法差异

旧版（v3.1）要求 LLM 手动维护章节编号 N、按 simple/standard 分流生成 heading、处理 on_conditions 跳过对编号的影响。
新版（v3.2）通过脚本保证这些机械行为的一致性：

- 章节编号 N 由脚本按 `enabled_stages × stage.tracks × track.type` 顺序机械递增
- on_conditions 未命中 → 章节保留为 `- 无`，**不占/不释放编号**（即所有章节始终生成）
- LLM 反悔启用某个 stage/track → 只需编辑 body，heading 不动
- reviewer 调整决策 → 改 `on-conditions-eval.md` 的「最终决策」列 + 同步到 `review-notes.md`

**stage 章节顺序**：脚本按 `config.stages` 数组原序生成（不再做 enabled_stages 过滤），
因为新方案下所有 stage 都生成章节。

#### 2d.5 受影响路径 & on_conditions 评估的来源

骨架脚本自动从 proposal.md 提取 glob 路径（`affected_paths`）并机械评估 on_conditions 规则：

- **path 维度**：从 proposal.md "### 包含"段 + "## 方案概述"段提取 glob 列表，与规则中的 glob 匹配
- **semantic 维度**：从 proposal.md 全文做关键词包含匹配（去停用词）

机械评估结果同时写入：

1. tasks.md 每个章节 heading 下的 HTML 注释
2. `1-propose-review/on-conditions-eval.md` 的评估表格（供阶段三 review 复核）

LLM 不必在阶段二手动评估 on_conditions，**仅在阶段三 review 时复核机械评估即可**（见阶段三指引）。


### 2e. execution-manifest.yaml

更新 TodoWrite 第 7 项。
路径：

**说明**：LLM **不直接写** execution-manifest.yaml，而是通过 CLI 工具基于 tasks.md 自动生成。

**步骤**：
1. 调用生成 CLI：
   ```bash
   python3 .opencode/skills/pg-propose/scripts/pg-gen-manifest.py CHANGE_NAME
   ```
2. 调用校验 CLI（校验 manifest ↔ tasks.md 一致性）：
   ```bash
   python3 .opencode/skills/pg-propose/scripts/pg-validate-proposal.py manifest CHANGE_NAME
   ```
3. 失败处理（最多 2 轮）：
   - 若校验不通过，根据错误类型修正 tasks.md：
     - manifest_section_missing → 修正 tasks.md 章节 heading，使 manifest 引用的 section 存在
     - manifest_track_no_phases → 补充 tasks.md 中 standard track 缺少的 phase 章节
     - manifest_track_type_mismatch → 确认 project.yaml 中 track type 正确
     - manifest_environment_invalid → 确认环境名称存在于 project.yaml environments 中
   - 修正后回到步骤 1 重跑（最多 2 轮）
   - 第 3 轮仍失败 → 将残留问题记录到 review-notes.md 的「阻塞」段
4. 成功：产物 `.pg/changes/CHANGE_NAME/execution-manifest.yaml` 自动生成

**产物依赖关系**：manifest 依赖 tasks.md（heading 格式 + 章节完整性），在 2e 完成后方可调用。


---

## 阶段三：自审（内联自 pg-propose-refine）

**本阶段不修改 proposal/design/tasks 本身**，只读产物 + AGENTS.md 规则 + context-summary.yaml，对以下 6 类问题做系统化检查，把发现写入 `.pg/changes/<change-name>/1-propose-review/review-notes.md`（新文件）。

更新 TodoWrite 第 8 项。

| 编号 | 检查类别 | 关注点 |
|------|---------|--------|
| 3.5.1 | 范围一致性 | proposal "包含/不包含" vs tasks 实际工作 |
| 3.5.2 | API 完整性 | 请求体/响应/状态码/权限/边界 |
| 3.5.3 | 设计缺陷 | 数据模型/异常处理/安全/性能/幂等 |
| 3.5.4 | 任务歧义 | 动作/上下文/验收/依赖/文件路径 |
| 3.5.5 | 验证流程 | 覆盖率/可测试性/负面场景/跨 stage 依赖 |
| 3.5.6 | 测试案例影响 | 受影响测试/新增测试/测试数据/测试隔离 |
| 3.5.7 | on_conditions 评估复核 | 阶段二脚本的机械评估是否准确 |

**详细清单**：见 [./references/review-checklist.md](./references/review-checklist.md)。

### 复核 on_conditions 机械评估

阶段二脚本已自动生成 `.pg/changes/<change-name>/1-propose-review/on-conditions-eval.md`，
含每条 stage 级 / track 级 on_conditions 规则的机械评估（path / semantic 双维度）。

LLM 在自审阶段需复核：

1. **逐条勾选「最终决策」列**：
   - 同意机械评估 → 填 `[x]`
   - 不同意 → 填 `[~]` 并在「依据」栏写明理由（如"关键词字典未覆盖'修改'→'调整'的近义词"）
2. **合并到 review-notes.md**：把 `on-conditions-eval.md` 的表格内容**复制**到 review-notes.md 的「on_conditions 评估记录」段
3. **同步触发行为**：若某 stage/track 的最终决策从"未命中"翻转为"命中"，review 阶段需在 review-notes 中标注"建议启用"，由用户在 review 阶段决定是否启用

### 写入 review-notes.md

**格式模板与编辑决策符号**：见 [./references/review-notes-format.md](./references/review-notes-format.md)。

review-notes.md 包含：
- **5 项通用决策表**（error_response_strategy / auth_scope / data_migration_strategy / transaction_boundary / frontend_interaction_style）
- **on_conditions 评估记录段**（从 `on-conditions-eval.md` 合并，含每条规则的最终决策 + 依据）
- **6 类自审发现的问题清单**（按 阻塞 / 重要 / 建议 三档分类，每条带目标、推荐动作、SKIP 允许标记）
- **一致性检查结果**（✅/⚠️/❌）
- **评审说明段**（编辑指引）

### 阶段三行为契约

- **禁止**使用 `question` tool 中断流程
- **禁止**自动修改 proposal/design/tasks 主体内容
- **唯一允许的产物修改**：纯格式问题（如 markdown 标题层级错乱、代码块语言标记缺失、明显笔误），且修改后必须在 review-notes.md 中留痕记录"格式修正: X→Y"
- 自审完成后更新 TodoWrite 第 7 项为完成

---

## 阶段四：最终确认

产物生成完成且单文档评审（review-notes.md）已写入后，更新 TodoWrite 全部标记为完成。直接向用户展示产物摘要：

- 变更名称、产物位置、已创建文件
- review-notes.md 内容摘要：
  - 通用决策：`5 项已预填推荐值`
  - 问题清单：`阻塞 X / 重要 Y / 建议 Z`（每条以 checkbox `[ ]` 起始）

告知用户：
- 如希望调整决策项，直接编辑 `.pg/changes/<change-name>/1-propose-review/review-notes.md`：
  - 通用决策：修改表格的"当前"列
  - 问题清单：把 `[ ]` 改为 `[x]`（已修复）或 `[~]` + 加 `> SKIP：理由`（豁免）
- 编辑后调用 `/2.1-pg-propose-refine {change-name}` 应用决策
- 下一步可执行 `/3-pg-build {change-name}` 开始实现
- 如希望修复 review-notes.md 中的"阻塞/重要"问题后再 apply，回复"修复 review-notes 中的问题"，由本会话继续处理

---

## 产物生成指导原则

- `context`（来自 AGENTS.md，经 context-summary.yaml 缓存）和 `rules`（来自 config.yaml）是给你的约束，不可复制到产物中
- 每个产物文件写入后验证文件存在
- 如果变更名称已存在，询问用户是继续还是新建

---

## 产物清单（硬约束）

每个 change 在 `.pg/changes/<change>/` 下必须生成**且仅生成**以下 4 个产物文件：

| 产物 | 写入位置 | 何时生成 |
|------|---------|---------|
| `proposal.md` | `.pg/changes/<change>/proposal.md` | 阶段 2a |
| `design.md` | `.pg/changes/<change>/design.md` | 阶段 2b |
| `execution-manifest.yaml` | `.pg/changes/<change>/execution-manifest.yaml` | 阶段 2d（含 `stages[i].environment` 字段） |
| `tasks.md` | `.pg/changes/<change>/tasks.md` | 阶段 2d |

**严禁生成**以下文件（v1 遗留物，pg-build 不再读取）：

- ❌ `environment.yaml` —— per-change 的环境选择已写入 `execution-manifest.yaml` 的 `stages[i].environment` 字段，由 `pg-build` 直接读取

任何 stage 缺少产物文件 → workflow_failed 终止。多生成产物文件 → 后续 pg-build 会忽略，但污染产物目录。

---

## ⛔ 禁令

下列操作在**整个提案阶段**均被禁止：
- ❌ 严禁修改任何业务代码文件
- ❌ 严禁执行 lint、typecheck、test 等验证命令
- ❌ 严禁启动任何服务（backend/frontend）
