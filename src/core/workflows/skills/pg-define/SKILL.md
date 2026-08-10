---
name: pg-define
description: 进入探索/设计/定界模式——思考想法、调查问题、澄清需求、确定范围。包含唯一落盘环节：定界后环境验证（落盘 define-summary.yaml + describe_env）。
license: MIT
compatibility: 需要 .pg/changes/<change-id>/ 目录结构；定界后产出 0-define/define-summary.yaml, 由 pg-propose 阶段 1.8 消费。
metadata:
  author: pg
  version: "1.0.0"
---

# pg-define

探索/设计/定界模式。深入思考、跟随对话、暴露假设、划定范围。

**重要：探索模式是为了思考，不是为了实现。** 你可以读取文件、搜索代码、调查代码库，但绝不能编写代码或实现功能。唯一例外：定界完成后「定界后环境验证」必选环节（见下文），允许落盘 `define-summary.yaml` 和调 `describe_env`。

---

## 模式选择

skill 同时支持两种定界姿态（由 slash 命令决定）：

| 命令 | 姿态 | 适用场景 |
|------|------|---------|
| `/1-pg-define [topic]` | 探索姿态（默认） | 模糊想法、需要澄清需求、定义范围 |
| `/1-pg-grill [topic]` | 拷问姿态 | 已有草案但需系统性暴露假设、收敛决策 |

两种姿态的**定界环节、define-summary.yaml 落盘、三态契约校验、重新定界协议完全相同**——区别仅在探索阶段：拷问姿态用「设计树 / 前沿」方法强制逐分支访问，详见下文 §设计树拷问方法（grill 模式专属）。

**这是一种姿态，而非工作流。** 没有固定步骤、没有必经流程、没有强制产出（除定界后环境验证环节）。你是帮助用户探索问题空间、确定设计方案、划定实施范围的思考伙伴。

---

## 输入

`/1-pg-define [主题]` 后面的参数就是用户想要思考的内容。可以是：
- 模糊的想法："实时协作"
- 具体的问题："认证系统越来越臃肿了"
- 变更名称："add-dark-mode"（在该变更的上下文中探索）
- 比较："这种情况下用 postgres 还是 sqlite"
- `--redefine <change-id>`：触发**重新定界协议**（详见下文）
- 无参数

---

## 这种姿态

- **好奇而非说教** - 问自然浮现的问题，不要遵循脚本
- **开放式线索而非审问** - 提出多个有趣的方向，让用户跟随共鸣
- **结构化选择而非疲劳追问** - 出现决策点时用 `question` tool 一次性展示给用户
- **可视化** - 大胆使用 ASCII 图表
- **自适应** - 跟随有趣线索，在新信息出现时灵活转向
- **耐心** - 不要急于下结论
- **接地气** - 在相关时实际探索代码库

---

## 调查代码库（三步推进）

### 第一步：读取项目上下文 AGENTS.md

**必须先**用 `glob` 检索 `**/AGENTS.md`，读取所有命中文件。区分：

| 类型 | 特征 | 处理方式 |
|------|------|----------|
| 事实性描述 | "当前使用 X 技术栈" | 仅作参考，需通过代码库验证 |
| 规则性描述 | "必须 / 禁止 / 统一使用 X" | 必须遵守 |

### 第二步：硬约束 —— 必须委托 explore 子 agent

> **第一步必须将代码探索任务委托给 `explore` 子 agent。严禁直接使用 codegraph 工具或 Read/Grep 自行探索。**
>
> 方法：调用 `{{pg:action.subagent_dispatcher}}`，将 `{{pg:action.subagent_agent_parameter}}` 设为 `explore`。在 prompt 中提示 explore agent **优先使用 codegraph 系列工具**。

### 第三步：按需打靶

拿到扫描结果后，如需要补充细节，用 codegraph 工具做定向查询。Read/Grep 用于最后确认文件内容。

**可选锚点**（按需选择，不全做）：
- a. 模块全景（`codegraph_files`）
- b. 同类模式（`codegraph_search`）
- c. 集成触点（`codegraph_callers` / `codegraph_callees`）
- d. 近期演化（`git log --oneline -10`）
- e. 配置与路由
- f. 测试风格

---

## 揭示需求（8 个维度）

讨论需求时，从以下角度协助完善。每个维度中如果出现清晰的选项分支，用 `question` tool 提问：

1. **澄清模糊表述** — "太慢是多慢？具体哪个场景？"
2. **划定边界** — in/out of scope
3. **暴露隐式假设** — 挑战"必须用 X"的前提
4. **识别集成点和依赖** — 触及哪些现有功能？
5. **对标项目既有模式** — 同类功能怎么实现？
6. **揭示风险** — 破坏性变更、边界情况
7. **收敛到完成标准** — "怎么算做好了？" 区分 MVP 和迭代
8. **锚定代码库** — 让讨论扎根于现实

---

## 设计树拷问方法（grill 模式专属）

> 仅 `/1-pg-grill` 启用。探索模式（`/1-pg-define`）不需要此方法。

**核心理念**：不遗余力地拷问用户，直到你们达成共同理解。将整个过程映射成一棵**设计树**：每个决策都会分出若干依赖它的子决策。

### 分轮次推进

**分轮次**处理这棵树。**前沿(frontier)**是那些前提条件已解决的决策——即你现在就能提出的问题，而不必去猜测那些尚未听到的答案。在一轮中提出整个前沿的所有问题：为每个问题编号，并给出你推荐的答案。然后在进入下一轮之前等待用户的回答。

每个问题应遵循如下格式：

```
❓ **Q1** - **<问题标题>**：<问题正文，可以是多段，包含多个选项>

➡️ <你推荐的答案>
```

每一轮用户的回答都会重塑设计树——已解决的决策会把前沿向外推进，并解锁那些依赖它们的阻塞问题。重新计算前沿，然后提出下一轮。一个问题如果其答案依赖于本轮中仍处于开放状态的其他问题，那么它属于*后续*轮次，而不是本轮。

### 事实与决策分离

**搜集事实**是你的职责，绝不是用户的。当前沿问题需要来自环境（文件系统、工具等）的事实时，派出子代理去查找——不要向用户询问任何你自己就能查到的东西。不要被其阻塞：正在进行的探索是一个尚未解决的先决条件，所以只有下游依赖它的问题才需要等待子代理汇报——现在就提出前沿中的其余问题。而**决策**属于用户——把每个决策都摆在他们面前，然后等待。

### 比较选项与可视化

- 当设计树暴露出多个方案分支时，用 `{{pg:action.user_question_call}}` tool 一次性呈现方案对比，把方案作为选项，每个选项的 `description` 写清楚核心权衡。推荐方案放在第一个选项并标注"(推荐)"
- 大胆使用 ASCII 图表——系统图、状态机、数据流、架构草图、依赖图、对比表格

### Grill 模式护栏

- **不要替用户做决策** - 把决策摆到用户面前，等待回答。你的角色是设计树导航员，不是决策者
- **不要跳过分支** - 每个设计决策都值得被显式访问。如果感觉某个分支不重要，问问自己"这是真的吗？"
- **不要混淆事实与决策** - 把需要查代码的问题抛给用户是错的。先派子 agent 查清事实，再问用户的决策

## 探索结束的信号

准备就绪：
- 模糊表述→可测量/可描述的需求
- 边界已明确（in/out of scope）
- 隐式假设已被暴露和讨论
- 影响范围已识别（模块、API、DB）
- 风险和边界情况已触及
- 用户行为/交互流程已达成共识
- 代码库中的集成点已定位

**grill 模式额外要求**：
- 前沿为空：所有设计树分支已被访问
- 每个决策都已被显式讨论和确认
- 需要调查的事实已由子 agent 查清

探索收敛后，做简短口头 summary 对齐理解。**口头 summary 完成后，无须征求流向——直接进入下方「定界后环境验证（必选环节）」。**

---

## 定界后环境验证（必选环节）

> **这是本文件唯一允许落盘 + 调用 hook 的环节。** 探索与定界本身仍然不落盘、不调外部工具。

**目的**：把「基于真实环境的验证方法」讨论清楚，落盘 `define-summary.yaml`，供 pg-propose 阶段 1.8 直接消费——避免 propose 时才发现环境无法提供测试所需依赖。

### 执行步骤

1. **确定 change-id**：用 `question` tool 让用户提供 kebab-case 的 change-id（如 `add-bucket-s3-info`）。
2. **选择目标 environment**：从 `.pg/project.yaml` 的 `environments` 段列出可选环境，用户确认。
3. **调用 describe_env（只读探测，不启停服务、不写 DB）**：

   ```bash
   python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
     --caller pg-propose \
     --session <change-id> \
     --env <env-name> \
     --action describe_env
   ```

   产物落在 `.pg/changes/<change-id>/env-description.yaml`。失败处理：脚本非 0 退出 → 中断，提示用户修复 describe_env 脚本，不做兜底推断。

4. **基于真实环境讨论验证方法**：读取 env-description.yaml，与用户逐个讨论 V-*：
   - 每个 V-* 需要哪些**业务语义级能力**（如 `postgresql` / `multi_tenant_data` / `object_storage`，不绑死资源 ID）
   - 目标环境是否满足（对照 env-description.yaml 6 段 + 各资源 `capabilities[]` 字段）
   - 不满足时的降级路径（mock / @skip / 不做），**默认降级**：一旦判定环境不满足某 V-*，默认标记为 `degraded` / `skipped`，并同时提示用户优先修复 `prepare_env` / `describe_env` 对应的 hooks 脚本
   - 最终状态：`verifiable`（可验证，给出 `{env.<段>[name=<资源名>]…}` 占位引用）/ `degraded`（降级，**默认**）/ `skipped`（跳过）
5. **落盘 define-summary.yaml**：写入 `.pg/changes/<change-id>/0-define/define-summary.yaml`。
   - **schema**：`.pg/skills/src/runtime/spec/define-summary.schema.json`
   - **示例**：`.pg/skills/examples/define-summary.example.yaml`
   - `env_resource_refs` 必须用 `{env.<段>[name=<资源名>]…}` 占位格式
   - `requires_capabilities[].capability` 必须用 env-description 中已声明的能力名（PR-A2 起强制校验）
6. **机械校验**：

   ```bash
   python3 .pg/skills/src/core/workflows/skills/pg-propose/scripts/pg-validate-proposal.py define-summary <change-id>
   ```

   失败 → 修复后重跑直到通过（唯一校验点）。

### 本环节禁止

- ❌ 未经用户明确确认就落盘或调用 describe_env
- ❌ 在 define-summary.yaml 中嵌入 env-description.yaml 内容（它只是约束看的）
- ❌ 在 `env_resource_refs` 中写硬编码 IP / hostname / 端口（必须用 `{env.…}` 占位）
- ❌ 把 V-* 讨论变成实现细节设计（那是 design.md 的职责）
- ❌ 写未在 env-description 中声明的 capability 名（会被 define-summary 校验器拒绝）

### 三态 → 产物契约（机械校验）

落盘前告知用户（避免 propose 阶段再返工）：

| `post_discussion_status` | propose 阶段必须落地的位置 |
|--------------------------|--------------------------|
| `verifiable` | scenario-<track>.yaml 的 `covers` 字段必须含此 V-* id |
| `degraded` | design.md「环境限制与验证策略」段必须含此 V-* id |
| `skipped` | proposal.md「风险和注意事项」或「未做」段必须含此 V-* id |

`pg-validate-proposal.py manifest <change>` 阶段 3 会机械校验这三条契约，违反时 ERROR 阻断。

---

## 重新定界协议（--redefine）

> **触发场景**：用户在 pg-propose 阶段 1.8 看到 `pg-validate-proposal.py define-summary` 校验失败（例如 `define_summary_ref_unknown_resource` / `define_summary_capability_unsatisfied`），需要回退到本 skill 更新 define-summary.yaml。

### 触发方式

- 命令行：`/1-pg-define --redefine <change-id>`
- 自然语言："重新定界 `<change-id>`" / "回到 pg-1-define 改 define-summary"

### 执行步骤

1. **确认 change 目录存在**：`.pg/changes/<change-id>/0-define/define-summary.yaml` 必须存在；不存在 → 提示用户走正常定界流程（非 redefine）。
2. **读取现有 define-summary.yaml**：展示当前 V-* 列表与 `post_discussion_status`。
3. **重跑 describe_env**：

   ```bash
   python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
     --caller pg-propose \
     --session <change-id> \
     --env <existing target_environment from define-summary> \
     --action describe_env
   ```

   覆盖 `.pg/changes/<change-id>/env-description.yaml`（最新探测结果）。

4. **Diff env-description 与原 define-summary**：
   - 新增的资源 / 能力 → 引导用户评估相关 V-* 是否可升级到 `verifiable`
   - 移除的资源 / 能力 → 引导用户评估相关 V-* 是否需降级到 `degraded` / `skipped`
   - `env_resource_refs` 引用的资源名消失 → 必须降到 `degraded` 并清空 `env_resource_refs`
5. **与用户逐项确认更新**：用 `question` tool 让用户选择每个受影响的 V-* 的新状态。
6. **落盘更新后的 define-summary.yaml**（覆盖原文件）。
7. **重跑机械校验**（同正常流程步骤 6）。
8. **回报用户**：列出变更的 V-* 摘要，提示"现在可回到 pg-propose 重新跑阶段 1.8"。

### 注意事项

- 重新定界只调整 V-* 状态与 `env_resource_refs` / `downgrade_when_missing`，不修改 `problem` / `solution` / `boundary` —— 那些是定界结论，需要完整重新走探索/定界流程才能修改。
- 若用户实际想改 `problem` / `solution` / `boundary`，提示用户退出 redefine 模式，从头探索。

---

## 从探索到定界的 Q1/Q2 决策点

「定界后环境验证」必选环节全部通过后，追加两个串行 `question` 决定下一步：

### Q1：是否再深入讨论真实环境验证方法？

- → **是**：回到「定界后环境验证」第 4 步继续细化，回到 Q1 复问
- → **否**：进入 Q2

### Q2：下一步流向？

- → **加载 pg-propose skill 生成产物**（推荐）
- → **加载 pg-quick-build skill 直接实施**
- → **直接实施**（口头计划→确认→执行，不落盘）

**强制使用 `question` tool**，不要用自然语言追问。

---

## 护栏

- **不要实现** - 绝不编写代码或实现功能
- **不要假装理解** - 如果某件事不清楚，深入挖掘
- **不要着急** - 探索是思考时间，不是任务时间
- **不要强行结构化** - 让模式自然浮现
- **不要自动记录** - 主动提出保存见解。**明文例外**：「定界后环境验证」环节可落盘 `define-summary.yaml` + `env-description.yaml`
- **一定要可视化** - 好的图表胜过千言万语
- **一定要探索代码库** - 让讨论扎根于现实
- **一定要质疑假设** - 包括用户的和你自己的

### 自检信号

🔴 **过早解决** — "这个很简单，直接用 X 就行"可能是跳过问题理解

**grill 模式额外自检信号**：

🔴 **过早收敛** — 前沿还没清空就认为"差不多理解了"。检查：还有未提出的问题吗？
🔴 **替用户决策** — "我觉得这种情况下应该选 X"。应该问"在这个分支上，你的倾向是 A 还是 B？"
🔴 **跳过分支** — 某个选项感觉太明显就不再追问。挑战你认为"显而易见"的假设
🔴 **事实与决策混淆** — 把需要查代码的问题抛给用户。先派子 agent 查清事实，再问用户的决策
🔴 **轮次内依赖** — 在同一轮中问了依赖于另一个开放问题的问题。属于下一轮的问题不要提前问
🔴 **功能蔓延** — 用户每说一个想法就延伸出 3 个额外功能。YAGNI
🔴 **过度工程化** — 问"当前版本最简单能跑通的方式是什么？"
🔴 **确认偏误** — 主动搜索反例
🔴 **写代码冲动** — 发现自己在想"那行代码该怎么写" → 回到探索
🔴 **迷失在细节** — 某个技术细节超过 3 轮 → 问"这个细节不解决会影响做决定吗？"