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
 - `environment.yaml`（per-change environment 选择 SSOT）
 - `tasks.md`（按 stages × tracks 划分的实现步骤 + 验证描述）
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

立即创建 9 项 TodoWrite：

```
1. [待开始] 创建变更目录
2. [待开始] 加载项目上下文（AGENTS.md → context-summary.yaml）
3. [待开始] 生成 proposal.md
4. [待开始] 生成 design.md
5. [待开始] 判定变更类型
6. [待开始] 生成 environment.yaml
7. [待开始] 生成 tasks.md
8. [待开始] tasks.md 可消费性验证循环
9. [待开始] 自审产物，写入单文档 review-notes.md
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

按顺序生成：proposal.md → design.md → 判定类型 → environment.yaml → tasks.md。每个产物依赖前一个产物的内容。
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

### 2c.5 推导 affected_paths & on_conditions 触发判定

更新 TodoWrite 第 5.5 项。

**目的**：从 proposal.md 推断本变更 affected_paths，评估每个 `config.stages[*].on_conditions`
是否命中，决定 `enabled_stages` 列表。这一步在 2c（affected_tracks）之后、2d（environment.yaml）
之前执行，其结果会同时影响 tasks.md 章节顺序与 environment.yaml 内容。

**步骤**：

1. 扫描 proposal.md "### 包含"段下所有 `- **xxx**` 列表项，提取每个项目描述中的文件路径（glob 模式）
2. 补充扫描 proposal.md "## 方案概述" 段的路径引用
3. 构造 `affected_paths` 列表
4. 遍历 config.stages，对每个含 `on_conditions` 的 stage：
   - 读取 on_conditions 列表（自然语言规则）
   - 对每条规则做 LLM 推理评估：
     - **路径维度**：affected_paths 命中规则中的 glob（如 `pg-spec-deprecated/scripts/**`）
     - **语义维度**：proposal.md 含规则中的关键词（如"环境层脚本"、"fixtures"、"setup 脚本注入"）
     - 任一维度命中 → 规则成立
   - 任一规则成立 → stage 启用
5. 输出 `enabled_stages`（常驻 stage + 触发的 stage）
6. **在 review-notes.md 留痕**：记录每条 on_conditions 的评估结果（命中/未命中 + 依据）

**示例**（写到 context-summary 不写产物）：

```yaml
affected_paths:
  - pg-spec-deprecated/scripts/fixtures/**
  - pg-spec-deprecated/scripts/dev-local/dev-local-setup.sh
on_conditions 评估:
  stage prepare-env-scripts:
    规则 1: "本变更 affected_paths 命中 pg-spec-deprecated/scripts/** 任一路径" → 命中 ✅
    结论: stage 启用 ✅
enabled_stages:
  - prepare-env-scripts    # 触发启用
  - dev-backend-and-agent  # 常驻
```

**注意事项**：
- on_conditions 评估存在不确定性（自然语言），必须在 review-notes.md 留痕，让 review 阶段可追溯
- 若 affected_paths 为空 → 所有纯路径维度规则不命中；纯语义维度规则仍需评估
- 若 LLM 推理不确定 → 在 review-notes.md "阻塞/重要"段标记，让用户决策

**关联文档**：见 [./references/orchestration-model.md](./references/orchestration-model.md)「on_conditions & stage 动态启用」段。

### 2d. environment.yaml

更新 TodoWrite 第 6 项（必须在 tasks.md 之前生成）。
路径：`.pg/changes/<change-name>/environment.yaml`

**这是 per-change environment 选择的 SSOT**（runner 直接读取此文件决定每个 `environment.required: true` 的 stage 使用哪个 environment）。

**生成算法**：
1. 读取 `.pg/project.yaml` 的 `stages` 段，过滤出所有 `environment.required: true` 的 stage
2. 对每个这样的 stage，读取其 `environment.selection_rules`（自然语言规则数组）
3. 根据本 change 的 `affected_tracks`，按规则逐条匹配，给出该 stage 选定的 environment 名称
4. 写出 YAML（per-stage map）：
   ```yaml
   # Per-change environment selection — SSOT
# 由 pg-propose 根据 config.yaml 中各 stage 的 environment.selection_rules 生成
    # runner 在每个 environment.required=true 的 stage 读取对应 stage 字段
   # value 可为: <env-name> | skip
   
   dev-mock-integration: dev-local       # 或 dev-3tier / skip
   real-integration: dev-local            # 或 dev-3tier / skip
   ```
5. 校验：每个选定的 environment 名称必须存在于 `config.yaml.environments` 的 keys 中，否则抛错

**关键约束**：
- **必须严格按 `environment.selection_rules` 的顺序匹配**——这些规则是项目级定义，pg-propose 不应"自作主张"覆盖
- **若某 stage 无规则匹配**，pg-propose 应使用该规则列表的最后一条作为兜底；如仍无明确默认，向用户请求决策
- **runner 行为**：environment.yaml 缺失 → 报错终止；yaml 中某 stage 未声明 → 报错终止；yaml 中 env 值不在 config.yaml.environments 中 → 报错终止

**on_conditions 触发的 stage 处理**（与 2c.5 联动）：
- 若 enabled_stages 包含 prepare-env-scripts 等被 on_conditions 触发的 stage，
  且该 stage 的 `environment.required=true`，则需按其 `selection_rules` 选择 env 并追加到 environment.yaml
- 若该 stage 的 `environment.required=false`，environment.yaml 不追加该 stage
- 示例（prepare-env-scripts 被触发且 required=false 时，environment.yaml 不追加该 stage）：

```yaml
dev-backend-and-agent: dev-local
```

### 2e. tasks.md

更新 TodoWrite 第 7 项。
路径：`.pg/changes/<change-name>/tasks.md`

> **重要**：生成前先读 [./references/orchestration-model.md](./references/orchestration-model.md)「Track 类型」段确认每个 track 是 standard 还是 simple。**simple track 只生成 1 个章节**（canonical form heading 含 `(simple track: runner 直接执行 commands)` + body 单 `- 无` 行），不走 4 子章节（test/dev/verify/gate）模板。

**生成算法**（stages × tracks 二维展开 + track 类型分流）：见 [./references/tasks-templates.md](./references/tasks-templates.md)。

核心规则摘要：
- **environment 选择已由 environment.yaml 决定**，tasks.md 不再生成 `## Deployments` 段
- 章节编号 N 从 1 开始顺序递增
- **standard track** 生成 4 个子章节：`test` / `dev` / `verify` / `gate`
- **simple track**（`tracks.<id>.type == "simple"`）生成 1 个章节（runner 直接执行 commands）
- standard track 章节标题使用 `## <N>. {stage.name}.{track_id}:{sub} - <label>` 格式
- simple track 章节标题使用 `## <N>. {stage.name}.{track_id} - {stage.name} {track_id}  (simple track: runner 直接执行 commands)` 格式
- 任务编号使用 `- [ ] <N>.<M>` 格式
- 不在 `affected_tracks` 中的 track 所有任务写 `- 无`
- 所有 stage 结束后必须追加 `final-gate` 章节

**约束**（来自统一配置 `rules.tasks`）：
- 使用中文撰写
- 任务编号必须使用 `- [ ] X.Y` 格式
- verify 阶段严格顺序：lint → test → start service → verify
- 每个 track 的 verify 阶段末尾必须含 Evidence Block 占位
- gate 阶段不执行任何命令，纯审查
- final-gate 章节不包含具体编程任务，仅作为编排器归档前的执行标记

**on_conditions 触发的 stage 章节顺序**（与 2c.5 联动）：
- tasks.md 的章节顺序由 `enabled_stages`（2c.5 推导）决定，**不是 config.yaml.stages 数组原序**
- pg-propose 在生成时按 enabled_stages 顺序生成每个 stage × track 的 4 个子章节
- 章节编号 N 从 1 开始重新计数
- 详细算法见 [./references/tasks-templates.md](./references/tasks-templates.md)「生成算法」段


### 2e.5 tasks.md 可消费性验证循环

更新 TodoWrite 第 8 项。

**循环**（MAX_FIX_RETRIES = 2）：

```
2e.5.1  运行 python3 .opencode/skills/pg-build/scripts/pg-validate-tasks.py validate <change>
        从 stdout 读取 JSON 报告
2e.5.2  若 valid == true → 更新 TodoWrite，进入阶段三
2e.5.3  若 valid == false → 遍历 JSON.issues 中 severity == "error" 的项：
        - missing_track → 判断是 on_conditions 跳过的 stage 还是真正漏写
            * on_conditions 跳过 → 传 --skip-stages <stage> 重新跑 validator
            * 真正漏写    → 在 tasks.md 补充缺失的 4 个子章节（test/dev/verify/gate）
        - invalid_sub  → 修正 section heading 中的 sub 命名
        - missing_final_gate → 追加 final-gate 章节
        warning/info 级的 issues 不阻断循环，LLM 可自主决定是否一并修正
2e.5.4  fix_counter 自增
        若 fix_counter < 2 → 回到 2e.5.1
2e.5.5  若 fix_counter >= 2 后仍 valid == false →
        将仍有 error 的 issue 记录到 review-notes.md 的「阻塞」段
        强制前进到阶段三
```

**on_conditions 跳过的 stage：使用 `--skip-stages` 参数**

当 `enabled_stages` 不包含某 stage（如 `prepare-env-scripts` 因 affected_paths 不命中 `pg-spec-deprecated/scripts/**` 而被跳过），
tasks.md 不生成该 stage 章节。调用 validator 时**必须**显式传 `--skip-stages`：

```bash
python3 .opencode/skills/pg-build/scripts/pg-validate-tasks.py \
    validate <change> \
    --skip-stages prepare-env-scripts
```

**对比：补占位章节（不推荐）**：

- 在 tasks.md 写 4 个 `## N. <stage>.<track>:*` 章节，每节一行 `- 无` —— 也能让 validator 通过
- 副作用：tasks.md 编号偏移（如 1-4 留给占位章节），实施者读起来困惑
- **不推荐** —— 优先用 `--skip-stages`

**`--skip-stages` 行为契约**：

- 逗号分隔多值，可重复传：`--skip-stages a --skip-stages b,c` 等价 `--skip-stages a,b,c`
- 严格按 `stage.name` 整词匹配（不是前缀）：传 `dev-backend-and-agent` 不会顺带跳过 `dev-backend-and-agent-extra`
- 被跳过的 item 不出现在 `summary.errors`，但会出现在 `summary.skipped_items` 与 human report 的 `## Skipped Items` 段
- review-notes.md「on_conditions 评估记录」是 LLM 评估结果的**可追溯凭据**，与 `--skip-stages` 互为补充

**2e.5.1 调用模板**（推荐）：

```bash
# 默认必传 on_conditions 评估的 skipped stages
SKIP_STAGES="prepare-env-scripts"  # 按需修改
python3 .opencode/skills/pg-build/scripts/pg-validate-tasks.py \
    validate <change> \
    --skip-stages "$SKIP_STAGES"
```

**验证范围与约束**：
- 仅检查**结构可消费性**（章节存在性、heading 格式、sub 命名、编号连续性）
- 不检查内容语义（属于阶段三自审的范畴）
- 不修改 tasks.md 以外的任何文件
- warning/info 不阻断循环，但应在 review-notes.md 中留痕（信息性备注）


---

## 阶段三：自审（内联自 pg-propose-refine）

**本阶段不修改 proposal/design/tasks 本身**，只读产物 + AGENTS.md 规则 + context-summary.yaml，对以下 6 类问题做系统化检查，把发现写入 `.pg/changes/<change-name>/1-propose-review/review-notes.md`（新文件）。

更新 TodoWrite 第 9 项。

| 编号 | 检查类别 | 关注点 |
|------|---------|--------|
| 3.5.1 | 范围一致性 | proposal "包含/不包含" vs tasks 实际工作 |
| 3.5.2 | API 完整性 | 请求体/响应/状态码/权限/边界 |
| 3.5.3 | 设计缺陷 | 数据模型/异常处理/安全/性能/幂等 |
| 3.5.4 | 任务歧义 | 动作/上下文/验收/依赖/文件路径 |
| 3.5.5 | 验证流程 | 覆盖率/可测试性/负面场景/跨 stage 依赖 |
| 3.5.6 | 测试案例影响 | 受影响测试/新增测试/测试数据/测试隔离 |

**详细清单**：见 [./references/review-checklist.md](./references/review-checklist.md)。

### 写入 review-notes.md

**格式模板与编辑决策符号**：见 [./references/review-notes-format.md](./references/review-notes-format.md)。

review-notes.md 包含：
- **5 项通用决策表**（error_response_strategy / auth_scope / data_migration_strategy / transaction_boundary / frontend_interaction_style）
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

## ⛔ 禁令

下列操作在**整个提案阶段**均被禁止：
- ❌ 严禁修改任何业务代码文件
- ❌ 严禁执行 lint、typecheck、test 等验证命令
- ❌ 严禁启动任何服务（backend/frontend）
