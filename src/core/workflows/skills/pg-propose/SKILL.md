---
name: pg-propose
description: 生成一个变更提案，一次性产出所有产物（proposal、design、tasks、manifest、scenario）。v0.8.4 起 review-notes 自审 + refine 流程已删除，由 pg-validate-proposal.py 机械校验替代。
license: MIT
compatibility: 需要 `.pg/changes/` 目录结构和 `.pg/project.yaml` 统一配置文件。
metadata:
  author: pg
  version: "1.2.0"
---

# pg-propose

生成变更提案——创建变更目录并一次性产出所有产物：

- `proposal.md`（做什么、为什么做）
- `design.md`（怎么做、验证标准）
- `tasks.md`（按 stages × tracks 划分的实现步骤 + 验证描述）
- `execution-manifest.yaml`（按 tasks.md 结构化生成的 pipeline 编排清单）
- `1-propose-review/on-conditions-eval.md`（机械评估记录 + scenario_tracks_decision SSOT）

**v1.0.0（2026-07-29 大重构）**：

- 🔄 **编号体系重构**：旧 `1a / 1b / ... / 2a / 2b / ... / 阶段三 / 阶段四` 字母后缀编号 → 体系 A 纯数字编号 `阶段 1-4` + `附录 A-E`。消除 1d.5 补丁型编号与 5.5 幽灵编号。
- ✅ {{pg:action.task_tracker}} 从 10 项扩为 **12 项**，与阶段编号一一对应（旧"13 项宣称 / 10 项列出"对齐失败已修复）。
- ✅ 阶段 2 拆分子步骤：2.4.2（LLM 填充 tasks.md body）、2.6.2（LLM 填充 scenario body），独立于脚本调用，避免占位符漏填被校验器拦截。
- ✅ 阶段 3 强化"**唯一**校验点"语义：删除旧 2.5/2.6 内冗余 `ls` 步骤，全部并入 `pg-validate-proposal.py`。
- ✅ `--scenario-decisions` 与 `--scenario-reason` **强必填**，删除"空字符串默认启用"隐式行为。
- ✅ 阶段 1.6（环境描述）段首强化"Context 注入契约"，明示 env-description.yaml 进入阶段 2 全产物写作。
- ✅ SKILL.md 主版本号 `0.9.0 → 1.0.0`；内部子版本号 v3.x 仅在附录 E 保留。

**v0.9.0 历史要点**（2026-07-27，scenario 环境一致性强化，已被 v1.0.0 取代）：
- 阶段 2f 新增"环境能力摘要"强制步骤（被 v1.0.0 阶段 1.6 取代）
- `pg-gen-scenario.py` 新增 `--env-summary` 参数（已被删除）
- `pg-validate-proposal.py` 新增 env-capability 交叉校验规则（已被 env-description.yaml 校验取代）

**v0.8.4 历史要点**（2026-07-27，删除 review-notes 自审 + pg-propose-refine 流程，仍生效）：
- 阶段三 6 类自审清单 + 4a/4b 智能分流 + review-notes.md 产物已删除
- 5 项 common decisions 固化为 `pg-gen-tasks-skeleton.py` 常量块
- `pg-validate-proposal.py` 新增 3 条机械校验规则（V-* 映射 / scenario 引用防护 / 章节编号连续性）

## 文档导航

| 关心的问题 | 看哪里 |
|------------|--------|
| pg-propose 总流程 / 阶段划分 / 黑名单 | 本文件 |
| 附录 A：{{pg:action.task_tracker}} 13 项清单 | [附录 A](#附录-atodowrite-13-项清单) |
| 附录 B：产物清单（硬约束）+ 三产物一致性约束 | [附录 B](#附录-b产物清单硬约束--三产物一致性约束) |
| 附录 C：scenario.yaml 生成指引 | [附录 C](#附录-cscenarioyaml-生成指引) |
| 附录 D：⛔ 禁令 | [附录 D](#附录-d-禁令) |
| 附录 E：文档变更记录 | [附录 E](#附录-e文档变更记录) |
| proposal.md 模板 / proposal_rules 注入 | [references/proposal-templates.md](./references/proposal-templates.md) |
| design.md 模板 / V-* 编号规则 | [references/design-templates.md](./references/design-templates.md) |
| tasks.md 模板 / 章节生成算法 / 各子章节模板 | [references/tasks-templates.md](./references/tasks-templates.md) |
| on_conditions / stages × tracks × modules 三层编排模型 | [references/orchestration-model.md](./references/orchestration-model.md) |
| `.pg/project.yaml` 字段索引 | [references/config-fields.md](./references/config-fields.md) |
| scenario 格式 + placeholder 校验协议 | [references/scenario-format.md](./references/scenario-format.md) |
| define-summary.yaml 模板 / 字段定义 / env_resource_refs 占位约定 | [references/define-summary-templates.md](./references/define-summary-templates.md) |

> **本文件职责**：只承载「流程编排 + 阶段契约 + 黑/白名单」。所有模板字符串、字段定义、规则清单一律下放到 references/ 单一 SSOT。

---

## 输入

- **变更名称**（kebab-case，例如 `add-bucket-s3-info`）
- 来自探索阶段的口头 summary（如有）

> 变更名称不需要以日期开头，archive 目录下的变更以日期开头，是在变更完成时 archive 的日期，新建的变更名字不需要日期开头。

---

## 阶段 1：上下文加载与目录准备

> **本阶段目标**：把所有「设计时假设」转化为「运行时事实」，喂给阶段 2 全产物写作。

### 1.1 {{pg:action.task_tracker}} 初始化

**{{pg:action.task_tracker}} 13 项清单详见 [附录 A](#附录-atodowrite-13-项清单)**。本步骤仅在对话开始时执行一次，创建结构化待办，后续每完成一个阶段动作立刻更新对应项状态。

### 1.2 确认变更名称

从用户输入或探索上下文获取变更名称（kebab-case）。如果用户未提供，直接根据语义生成一个（kebab-case）。

**约束**：变更名称必须为纯语义化的 kebab-case，**禁止**以日期或数字开头（如 `2026-07-16-xxx`）。
日期前缀仅在归档时由归档工具自动添加，新建的变更名称必须是纯语义描述。

### 1.3 创建变更目录

```bash
mkdir -p ".pg/changes/<change-name>/1-propose-review"
```

验证目录已创建。更新附录 A 第 1 项。

### 1.4 加载项目上下文（直接读取所有 AGENTS.md）

直接通读项目内所有 AGENTS.md，提取约束作为 LLM 的 context（无需中间缓存）。通过 `find` 列出所有 AGENTS.md 路径：

> **排除目录覆盖范围**（按语言/工具分组）：
> - VCS: `.git`
> - pg-skills 自身: `.pg/skills`（避免扫到 skill 仓库内部嵌套的 AGENTS.md / SKILL.md）
> - JS / TS / Node: `node_modules`, `dist`, `build`, `.next`, `.nuxt`, `.svelte-kit`, `.turbo`, `coverage`, `playwright-report`, `storybook-static`
> - Java / Kotlin / JVM: `target`, `.gradle`, `out`
> - Python: `__pycache__`, `.venv`, `venv`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox`, `htmlcov`
> - Go / Ruby / PHP: `vendor`, `.bundle`, `vendor/bundle`
> - C# / .NET: `bin`, `obj`
> - Flutter / Dart: `.dart_tool`, `.flutter-plugins`
> - Elixir: `_build`, `deps`
> - Swift / iOS: `DerivedData`

```bash
find . -name AGENTS.md \
  -not -path '*/.git/*' \
  -not -path '*/.pg/skills/*' \
  -not -path '*/node_modules/*' \
  -not -path '*/dist/*' \
  -not -path '*/build/*' \
  -not -path '*/.next/*' \
  -not -path '*/.nuxt/*' \
  -not -path '*/.svelte-kit/*' \
  -not -path '*/.turbo/*' \
  -not -path '*/coverage/*' \
  -not -path '*/playwright-report/*' \
  -not -path '*/storybook-static/*' \
  -not -path '*/target/*' \
  -not -path '*/.gradle/*' \
  -not -path '*/out/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/.venv/*' \
  -not -path '*/venv/*' \
  -not -path '*/.pytest_cache/*' \
  -not -path '*/.mypy_cache/*' \
  -not -path '*/.ruff_cache/*' \
  -not -path '*/.tox/*' \
  -not -path '*/htmlcov/*' \
  -not -path '*/vendor/*' \
  -not -path '*/.bundle/*' \
  -not -path '*/vendor/bundle/*' \
  -not -path '*/bin/*' \
  -not -path '*/obj/*' \
  -not -path '*/.dart_tool/*' \
  -not -path '*/.flutter-plugins/*' \
  -not -path '*/_build/*' \
  -not -path '*/deps/*' \
  -not -path '*/DerivedData/*' \
  | sort
```

LLM 通读这些文件，提取：

- `context`（tech_stack / package / database_conventions / coding_conventions / design_patterns / domain）
- `rules`（review 检查条目列表）

更新附录 A 第 2 项。

### 1.5 获取管线配置（从 config.yaml 读取）

```bash
python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py pg-propose
```

从输出 JSON 获取：`propose`（含 `guidelines` 和 `injections`）/ `tracks` / `stages`。

字段详细含义见 [references/config-fields.md](./references/config-fields.md)。

> **下游依赖**：本步骤输出的 `config.stages[i].environment.selection_rules` 是阶段 1.6 `--env` 参数的选择依据，必须在 1.6 之前完成。

**⚠️ 命令执行位置规约**：

- 所有命令从**项目根路径**执行
- 需切换目录的命令在配置中显式写 `cd <dir> && <cmd>`（如 `test: cd <module-name> && mvn test`）
- `rebuild_and_restart` / `verify` 脚本应自包含 cwd 处理

### 1.6 生成环境描述（env-description.yaml，per-change）

> **Context 注入契约**：本步骤完成后，env-description.yaml 作为强制 context 进入阶段 2 全产物（proposal.md / design.md / tasks.md / scenario-*.yaml）写作。LLM 必须在每个产物中显式引用 6 段中已声明的具体资源（不允许"环境未就绪"类兜底措辞）。

**职责**：让 LLM 知道当前 change 在目标 environment 上的真实状态（不是 design-time 假设），作为 design.md / tasks.md / scenario-*.yaml 的输入。这是 v6 新流程，与旧 env-capability.yaml 机制不兼容：旧机制是 LLM 看 prepare 脚本猜能力，新机制是项目自带 describe_env 脚本真实探测。

**关键约束**：
- describe_env 与 prepare_env **独立**：两脚本分别维护，describe_env 不调用 prepare_env，不假设其已执行（实现独立性）
- **语义契约**：env-description.yaml 描述的是 prepare_env **成功执行后**该环境的预期基线状态。pg-build 实际执行时先调 prepare_env 确保成功，再执行 scenario track，届时环境状态应与本文件一致。LLM 应以此基线判断 scenario 可验证性，而非以"当前环境未就绪"为由跳过
- describe_env **必须显式声明**在 `environments.<env>.describe_env.script`，无默认值（Q8 决策）
- 文件位置：`.pg/changes/<change-id>/env-description.yaml`（per-change 特定，固定路径覆盖，Q1 决策）
- 失败处理：**中断**调用方（Q2 决策，无 partial / fallback / LLM 推断兜底）
- 不再有 fingerprint / HIT-MISS 机制（Q4 决策）：每次 propose 都重新执行 describe_env
- `--env` 参数值从阶段 1.5 获取的 `config.stages[i].environment.selection_rules` 推导

**执行协议**：

```bash
# 由 caller (pg-propose) 显式调用, 注入 PG_RUN_CALLER=pg-propose
# v7 起: 统一用 --session 作为路径派生源, 不再传 --change-id
# --env 值来自阶段 1.5 的 config.stages[i].environment.selection_rules
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
  --caller pg-propose \
  --session <change-id> \
  --env <env-name> \
  --action describe_env
```

**注入环境变量**（SSOT: `src/runtime/spec/hook-env-vars.yaml` v6）：

| 变量 | 必读 | 用途 |
|---|---|---|
| `PG_RUN_CALLER` | ✅ | 调用方标识（pg-propose） |
| `PG_PROJECT_ROOT` | ✅ | 项目根路径 |
| `PG_SESSION_ID` | ✅ | session-id（日志路由） |
| `PG_CHANGE_ID` | ✅ | change-id（输出路径） |
| `PG_ENV_NAME` | ✅ | 目标 environment 名 |
| `PG_OUTPUT_PATH` | ✅ | env-description.yaml 输出绝对路径（脚本必须写入此文件） |
| `PG_HOOK_LOG_DIR` | - | 日志目录 |
| `PG_LOG_FILE` | - | hook stdout/stderr 目标 |
| `PG_HOOK_TIMEOUT` | - | 超时秒数 |

**脚本行为契约**：

1. **只读探测**：不修改环境状态（不启停服务、不写种子数据、不动 DB schema）
2. **输出 schema**：严格符合 `src/runtime/spec/env-description.schema.json`（6 段 + relations）
3. **失败处理**：
   - 退出非 0 → 调用方中断，提示用户修复 describe_env 脚本
   - 失败现场：写 `${PG_OUTPUT_PATH}.partial`（含 `described_status: failed`），便于调试
4. **必读检查**：缺 `PG_RUN_CALLER` / `PG_PROJECT_ROOT` / `PG_CHANGE_ID` / `PG_ENV_NAME` / `PG_OUTPUT_PATH` 任意一个即报错退出

**schema 结构**（6 段 + relations，每段可选）：

- `infra_services` — 基础设施服务（DB / Cache / MQ / 对象存储 / K8s / 容器运行时 / 服务网格）
- `business_systems` — 业务系统（上下游 / 兄弟服务 / mock / stub）
- `data_resources` — 数据资源（DB schema / 种子数据 / 消息主题 / 文件 / CRD）
- `config_resources` — 配置资源（应用配置 / TLS 证书 / env vars / 密钥）
- `runtime_environment` — 运行时环境（OS / 网络 / DNS / 虚拟设备 / 监控接入 / cron）
- `external_dependencies` — 外部依赖（第三方 SaaS / 跨集群 / 共享基础设施）
- `relations` — 跨段关联（depends_on / owns / reads / writes / references / proxies_to / monitors）

详细字段定义见 `src/runtime/spec/env-description.schema.json` JSON Schema。

**LLM context 注入**（describe_env 执行成功后）：

```
## 环境描述（来自 .pg/changes/<change-id>/env-description.yaml）

### <env_name> 资源拓扑
{ env-description.yaml.environments.<env_name> 全文 }

⚠️ 写 proposal.md / design.md / tasks.md / scenario-*.yaml 时：
  - scenario given 必须引用 6 段中已声明的具体资源（如 `infra_services[name=kubernetes].instances[0].id`）
  - 不得以 "环境未就绪" / "OSS 未配置" / "测试数据缺失" 为由跳过
  - 资源命名 / ID 规则参考 `infra_services` / `data_resources` 中真实 instance id
  - 状态判断参考 `data_resources[].state.status`（empty / seeded / configured / partial / unknown）
```

**禁止**：
- 把 env-description.yaml 内容复制到 proposal.md / design.md / tasks.md / scenario-*.yaml 产物里——它只是约束看的
- 修改 `.pg/context/env-capability.yaml`（已废弃，本版本不再支持旧路径）

**示例**：

完整 env-description.yaml 示例见 `examples/env-description.example.yaml`（K8s + DB + Cache + 业务系统场景）。

更新附录 A 第 3 项。

### 1.7 加载 propose.injections.proposal（结构化规则注入）

`.pg/project.yaml` 的 `propose.injections.proposal` 段是结构化规则列表，按 `after_section` 字段注入到 `proposal.md` 模板。字段约定与注入算法见 [references/proposal-templates.md](./references/proposal-templates.md)「proposal_rules 注入机制」段。

更新附录 A 第 4 项。

### 1.8 加载 define-summary.yaml（条件性，pg-1-define 定界产物）

**触发条件**：`.pg/changes/<change-id>/0-define/define-summary.yaml` 存在（由 pg-1-define 的「定界后环境验证」环节落盘）。不存在 → 跳过本步骤（向后兼容：define-summary 是可选前置产物，旧流程不受影响）。

**职责**：把 define 阶段基于真实 env-description 讨论产出的 V-* 状态（`verifiable` / `degraded` / `skipped`）作为阶段 2 全产物写作的输入。**本阶段只加载 + 校验，不做「假设 vs 事实」对账**——对账在 pg-1-define 定界环节已完成，define-summary 中的 `post_discussion_status` 是最终结论。

**执行协议**：

```bash
# 机械校验（唯一校验点）
python3 .pg/skills/src/core/workflows/skills/pg-propose/scripts/pg-validate-proposal.py define-summary <change-id>
```

校验内容（见 `pg-validate-proposal.py` 的 `cmd_define_summary`）：

1. 文件存在性 + YAML 可解析性
2. 结构校验（对照 `.pg/skills/src/runtime/spec/define-summary.schema.json`）
3. `change_id` 与目录名一致性
4. `target_environment` 与 env-description.yaml 的 `described_for.environment` 一致性
5. `env_resource_refs` ↔ env-description.yaml 交叉校验（verifiable 必填且引用的段/资源名真实存在；non-verifiable 必须为空）

**失败处理**：校验非 0 退出 → 中断，stderr 末尾输出可执行命令 `/1-pg-define --redefine <change-id>`（详见 `pg-define` SKILL §重新定界协议），由 agent 或用户手动执行。典型原因：阶段 1.6 重跑 describe_env 后环境漂移导致 `env_resource_refs` 引用失效，或新增 `requires_capabilities` 未在 env-description 中声明（PR-A2 起）。

**Context 注入契约**（校验通过后）：define-summary.yaml 作为强制 context 进入阶段 2 全产物写作：

```
## 定界结论（来自 .pg/changes/<change-id>/0-define/define-summary.yaml）

### V-* 状态
{ verification_needs 全文：id / name / what / post_discussion_status / env_resource_refs / downgrade_when_missing }

⚠️ 写 proposal.md / design.md / tasks.md / scenario-*.yaml 时：
  - post_discussion_status=verifiable 的 V-*：scenario given/then 必须直接复用 env_resource_refs 的 {env.…} 占位引用
  - post_discussion_status=degraded 的 V-*：design.md「环境限制与验证策略」段记录降级路径，scenario 加 @skip 标记
  - post_discussion_status=skipped 的 V-*：proposal.md「未做」段必须列出，scenario 不写该 V-*
  - define-summary 的 V-* id 编号必须与 design.md / tasks.md / scenario 的 V-* 保持一致
```

**字段定义与模板**见 [references/define-summary-templates.md](./references/define-summary-templates.md)。

更新附录 A 第 5 项。

---

## 阶段 2：产物生成

按顺序生成：proposal.md → design.md → 判定类型 → tasks.md → execution-manifest.yaml → scenario-*.yaml（条件）。每个产物依赖前一个产物的内容。

每生成一个产物后，更新附录 A 对应项。

### 2.1 proposal.md

路径：`.pg/changes/<change-name>/proposal.md`

**模板 + propose.injections.proposal 注入**见 [references/proposal-templates.md](./references/proposal-templates.md)。更新附录 A 第 6 项。

### 2.2 design.md

路径：`.pg/changes/<change-name>/design.md`

**模板 + V-* 编号规则**见 [references/design-templates.md](./references/design-templates.md)。更新附录 A 第 7 项。

**v0.9.0 新增**：design.md 必须包含"环境限制与验证策略"段（在"错误码与编号段"之后、"可观测性"之前），依据 `.pg/changes/<change-id>/env-description.yaml`（阶段 1.6 产出）判断每个 V-* 在目标 env 是否可验证。该段是阶段 2.6 scenario 编写的直接输入。

### 2.3 判定变更类型 & affected_tracks & scenario track(s) 启用决策

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
   - `--scenario-decisions` 支持 per-track 决策：`"scenario-e2e=true,scenario-perf=false"`
   - **`--scenario-decisions` 与 `--scenario-reason` 均为强必填**：禁止空字符串、未指定、隐式默认。空值直接报错退出，避免 scenario 轨道决策走隐式默认。
   - `--scenario-reason` 必填结构化要求：必须包含「跨 role 协作验证? / 新 API 端点? / 跨模块联调?」三问答复（1-2 句），写入 `on-conditions-eval.md` 的 `scenario_tracks_decision` 段
4. 把 `affected_tracks` 和 `scenario track(s) 决策 + 依据`写入 design.md 末尾的"变更类型判定"留痕小节

更新附录 A 第 8 项。

**design.md 硬约束**：全部列出在 [references/design-templates.md](./references/design-templates.md) 的"## 约束"段，本文件不重复展开。SKILL.md 只承载流程。

### 2.4 生成 tasks.md

> **核心变化（v3.2）**：tasks.md 的章节标题骨架、章节编号 N、simple/standard 分流、
> environment block quote、final-gate 章节、`on_conditions` 评估记录模板——
> **全部由 `pg-gen-tasks-skeleton.py` 机械生成**，LLM 只负责按骨架填充 body 内容。

**完整生成算法 + 各子章节模板**见 [references/tasks-templates.md](./references/tasks-templates.md)。

#### 2.4.1 调用 pg-gen-tasks-skeleton.py

```bash
python3 .pg/skills/src/core/workflows/skills/pg-propose/scripts/pg-gen-tasks-skeleton.py \
  --change <change-name> \
  --proposal-md .pg/changes/<change>/proposal.md \
  --affected-tracks "<track1>,<track2>,..." \
  --environment "<stage1>→<env1>,<stage2>→<env2>,..." \
  --selected-stages "<stage1>,<stage2>,..." \
  --scenario-decisions "track1=true,track2=true" \
  --scenario-reason "<决策依据，1-2 句>"
```

参数来源：

| 参数 | 来源 | 必填 |
|------|------|------|
| `--change` | 阶段 1.2 确认的变更名 | ✅ |
| `--proposal-md` | 阶段 2.1 产物 | ✅ |
| `--affected-tracks` | 阶段 2.3 判定结果 | ✅ |
| `--environment` | LLM 按 `config.stages[i].environment.selection_rules` 选择 | ✅ |
| `--selected-stages` | LLM 根据 on_conditions 推导 | ✅ |
| `--scenario-decisions` | per-track scenario 启用决策（**强必填**，禁止空字符串） | ✅ |
| `--scenario-reason` | 决策依据（**强必填**，结构化 1-2 句） | ✅ |

脚本输出：

- `.pg/changes/<change>/tasks.md`：完整骨架（所有 scenario track disabled 时不含 scenario 章节）
- `.pg/changes/<change>/1-propose-review/on-conditions-eval.md`：`on_conditions` 评估记录 + **scenario_tracks_decision 段（SSOT，per-track）**
- stdout JSON：sections 数组（章节清单 + 元数据 + `scenario_tracks` 字段）

LLM 读取 sections JSON 后，按 `references/tasks-templates.md`「各子章节模板」段填充 body。

更新附录 A 第 9 项。

#### 2.4.2 LLM 填充 tasks.md body

按 `references/tasks-templates.md`「各子章节模板」段填充 body。**禁止**：

- 修改任何 heading 文本、章节编号 N、stage/track/sub 前缀、标签
- 调整章节顺序或跳过任何章节
- 删除任何章节（包括 on_conditions 未命中的章节，heading 也保留）
- 在 verify 章节的命令步骤后追加具体 shell 命令

本子步骤完成后，附录 A 第 9 项整体标记完成（2.4.1 + 2.4.2 共享一项）。

### 2.5 生成 execution-manifest.yaml

LLM **不直接写** execution-manifest.yaml，通过 CLI 工具基于 tasks.md 自动生成。

```bash
python3 .pg/skills/src/core/workflows/skills/pg-propose/scripts/pg-gen-manifest.py CHANGE_NAME
```

**产物依赖关系**：
- manifest 依赖 tasks.md（heading 格式 + 章节完整性），在 2.4 完成后方可调用
- manifest 的 `scenario-<track>.enabled` 由 `on-conditions-eval.md` 的 `scenario_tracks_decision` 段决定（SSOT，禁用时不进入 manifest）

更新附录 A 第 10 项。

### 2.6 条件生成 scenario-<track>.yaml

**触发条件**：仅当 `on-conditions-eval.md` 中 `scenario_tracks_decision` 段有至少一个 track 的 `enabled = true` 时执行。

#### 2.6.1 调用 pg-gen-scenario.py 生成 skeleton

**v6 不再需要 `--env-summary` 参数**，env-description.yaml 已包含全部信息：

```bash
python3 .pg/skills/src/core/workflows/skills/pg-propose/scripts/pg-gen-scenario.py CHANGE_NAME
```

脚本自动：
- 读 `on-conditions-eval.md` 的 `scenario_tracks_decision` 段（SSOT）
- 遍历每个 enabled=true 的 track，写 `scenario-<track-id>.yaml` skeleton（LLM 必填 Scenario 内容）
- 无 enabled track → no-op（不写文件）

#### 2.6.2 LLM 填充 scenario body

读取 env-description.yaml 作为 scenario 编写输入：

从阶段 1.6 生成的 `.pg/changes/<change-id>/env-description.yaml` 中，针对 `--environment` 指定的目标 env，提取以下信息并写入 LLM 工作记忆：

- **infra_services**：可用基础设施（DB / Cache / MQ / K8s 等），包括 `instances[].id` / `endpoint` / `version` / `reachable`
- **business_systems**：业务系统（上下游 / mock），包括 `endpoints[].url` / `auth`
- **data_resources**：数据资源状态（重点关注 `state.status` = empty/seeded/configured/partial）
- **config_resources**：配置 / 凭证 / TLS 证书的位置与编码
- **runtime_environment**：OS / DNS / 网络配置
- **external_dependencies**：外部依赖与 fallback 策略
- **relations**：资源间依赖关系

scenario given 必须**直接引用** env-description.yaml 中已声明的具体资源路径，例如：
- `infra_services[name=kubernetes].instances[0].id` 为 `kuboard-dev-1-15`
- `data_resources[name=kb_helm_chart_repo].state.status` 为 `empty`（scenario 必须显式声明由前置 scenario 创建）

**scenario 占位符硬约束**：将 skeleton 中的 S-example 替换为真实 Scenario（**given/then 必须引用 env-description.yaml 中的具体资源**）。若 2.6.2 未完成（占位符未替换），阶段 3 校验器会报 `scenario_placeholder_unfilled` 错误。

详见 [附录 C](#附录-cscenarioyaml-生成指引)。

更新附录 A 第 11 项。

---

## 阶段 3：校验（**唯一**校验点）

> **本阶段是 propose 阶段**唯一**的文件存在性 + 占位符 + 三产物一致性校验点。** 阶段 2 内不再做 ls / 占位符预校验，全部推迟到此。

### 3.1 三产物一致性校验

```bash
python3 .pg/skills/src/core/workflows/skills/pg-propose/scripts/pg-validate-proposal.py manifest CHANGE_NAME
```

**v3.5+ 起**：本步骤是 propose 阶段**唯一**的产物校验点，原阶段 2 内 2.5/2.6 的两次校验已合并到此。

校验覆盖：
- tasks.md ↔ manifest section 引用一致性
- track type ↔ project.yaml 拓扑
- manifest ⟷ project.yaml environments 引用
- tasks.md / manifest / scenario-<track>.yaml 三产物与 `on-conditions-eval.md` 的 `scenario_tracks_decision` SSOT 一致
- scenario-<track>.yaml 占位符是否已被 LLM 填充（v3.7 新增，详见 [references/scenario-format.md] 中的 placeholder 协议）
- 所有 scenario track 启用的产物文件存在性 + scenario track 禁用的产物文件不存在性

更新附录 A 第 12 项。

### 3.2 校验失败处理（最多 2 轮）

- `manifest_section_missing` → 修正 tasks.md 章节 heading
- `manifest_track_no_phases` → 补充 standard track 缺少的 phase 章节
- `manifest_track_type_mismatch` → 确认 project.yaml 中 track type 正确
- `manifest_environment_invalid` → 确认环境名在 project.yaml environments 中
- `scenario_yaml_missing` → 跑 pg-gen-scenario.py 生成（scenario track 启用时）
- `scenario_yaml_should_not_exist` → 删除 scenario-<track>.yaml（scenario track 禁用时）
- `scenario_yaml_orphan` → 删除 scenario-<track>.yaml 或重新跑 2.4-2.6
- `scenario_placeholder_unfilled` → 回到 2.6.2 补填 LLM Scenario body
- 修正后回到对应产物生成步
- 第 3 轮仍失败 → workflow_failed，提示用户重跑 pg-propose 重新生成产物

### 3.3 scenario track 一致性约束（SSOT）

scenario track 是常驻 track，但 LLM 仍可在阶段二 2.3 决策为某个 track 设置 `enabled = false`（纯单模块改动）。**但三个产物（tasks.md / manifest / scenario-<track>.yaml）必须一致**：

| `scenario_tracks_decision` | tasks.md scenario 章节 | manifest scenario track | scenario-<track>.yaml |
|---------------------------|------------------------|------------------------|----------------------|
| track-A: `enabled=true` | ✅ 存在 | ✅ 存在 + `enabled=true` + `scenario_yaml` 字段 | ✅ `scenario-track-A.yaml` 存在 |
| track-B: `enabled=false` | ❌ 不存在 | ❌ 不存在 | ❌ 不存在 |

违反时 `pg-validate-proposal.py` 会报 `scenario_yaml_missing` / `scenario_yaml_should_not_exist` / `scenario_yaml_orphan` 错误，必须修复。

### 3.4 v0.8.4 机械校验（替代原 review-notes 自审）

`pg-validate-proposal.py` 已集成 3 条新规则，替代原 6 类 LLM 自审 + review-notes 决策表。`review-notes.md` 不再生成。

**校验规则**（详见 `pg-validate-proposal.py` `_validate_v0_8_3_rules` 段）：

| 规则 | 触发条件 | 等级 |
|------|---------|------|
| `v_identifier_uncovered` | design.md V-{track}-N 未被 tasks.md verify 章节引用 | WARN |
| `scenario_yaml_referenced` | tasks.md body 引用 scenario-*.yaml 路径 | WARN |
| `tasks_md_section_duplicate` / `tasks_md_section_skipped` | 章节编号重号 / 跳号 | WARN |

**v1.4 新增（PR-B2）三态契约校验**（ERROR 等级，违反时阻断）：

| 规则 | 触发条件 |
|------|---------|
| `define_summary_verifiable_uncovered` | define-summary 中 `verifiable` 的 V-* id 未出现在任何 scenario-*.yaml covers |
| `define_summary_degraded_no_fallback` | define-summary 中 `degraded` 的 V-* id 未出现在 design.md「环境限制与验证策略」段 |
| `define_summary_skipped_not_in_proposal` | define-summary 中 `skipped` 的 V-* id 未出现在 proposal.md「风险和注意事项」或「未做」段 |

WARN 不阻塞 build，但会显著提示 LLM 在阶段 3 校验后重点审视。

**5 项 common decisions 固化**（替代 review-notes.md 决策表）：

| 字段 | 默认值 | 含义 |
|------|-------|------|
| `error_response_strategy` | A | ApiResponse 全局统一格式 |
| `auth_scope` | project | /tenants/{t}/projects/{p}/ scope |
| `data_migration_strategy` | C | 默认无 schema 变更 |
| `transaction_boundary` | A | 默认单 service @Transactional |
| `frontend_interaction_style` | A | 默认 el-dialog 弹窗 |

修改路径：直接改 `pg-gen-tasks-skeleton.py` 顶部 `COMMON_DECISIONS` 常量块，重跑 `pg-gen-tasks-skeleton.py` 生效。

**阶段 3 行为契约**：

- **禁止**使用 `question` tool 中断流程
- **禁止**自动修改 proposal/design/tasks 主体内容
- **禁止**手工修改 `execution-manifest.yaml` 的 `enabled` / `reason` / `on_conditions_eval` 字段
  - 如需变更，**必须重跑** `pg-gen-tasks-skeleton.py` + `pg-gen-manifest.py` + `pg-gen-scenario.py`，让 SSOT 自动同步
- **禁止**在 scenario track 启用时手工编辑 `tasks.md` 删除 scenario 章节
   - 必须改 `--scenario-decisions "track=false"` 重跑 2.4
- **唯一允许的产物修改**：纯格式问题（markdown 标题层级错乱、代码块语言标记缺失、明显笔误）
- 校验完成后更新附录 A 第 12 项为完成

---

## 阶段 4：汇报

### 4.1 展示产物摘要给用户

直接向用户展示产物摘要：

- **变更名称、产物位置、已创建文件**（6 个产物）：
  - `.pg/changes/<change>/proposal.md`（必填）
  - `.pg/changes/<change>/design.md`（必填）
  - `.pg/changes/<change>/tasks.md`（必填）
  - `.pg/changes/<change>/execution-manifest.yaml`（必填）
  - `.pg/changes/<change>/scenario-<track>.yaml`（**每个启用**的 scenario track 一个）
  - `.pg/changes/<change>/1-propose-review/on-conditions-eval.md`（必填）

- **scenario_tracks_decision 状态**（从 on-conditions-eval.md 读取）：
  - 每个 scenario track 的 `enabled` 状态：`{track_id}: {enabled/disabled}`
  - enabled track → tasks.md / manifest / scenario-<track>.yaml 三产物均含对应章节
  - disabled track → 上述三产物均不含该 track（避免冗余）

- **机械校验结果**：
  - `pg-validate-proposal.py manifest <change>` 返回 `OK: all manifest checks passed`（含 v0.8.4 三条规则）
  - 任何 WARN 项应记录到 LLM context 但不阻塞

告知用户：

- 5 项 common decisions 已通过 `pg-gen-tasks-skeleton.py` 常量块固化（不再提供用户改动的接口）
- 如需调整 track 启用决策，修改 `on-conditions-eval.md` 的 `scenario_tracks_decision` 段（不建议，需重跑三个生成脚本）
- 下一步可执行 `/3-pg-build {change-name}` 开始实现

更新附录 A 第 13 项为完成。

---

## 附录产物生成指导原则

- `context`（来自所有 AGENTS.md）和 `propose.guidelines`（来自 config.yaml）是给你的约束，不可复制到产物中
- 每个产物文件写入后验证文件存在
- 如果变更名称已存在，询问用户是继续还是新建

---

## 附录 A：{{pg:action.task_tracker}} 13 项清单

> 本清单与阶段 1-4 编号一一对应。LLM agent 在每个阶段步骤完成后立即更新对应项状态。

```
 1. [待开始] 1.3 创建变更目录（含 1-propose-review 子目录）
 2. [待开始] 1.4 加载项目上下文（find AGENTS.md + 通读提取 context/rules）
 3. [待开始] 1.6 生成环境描述（env-description.yaml，describe_env hook）
 4. [待开始] 1.7 加载 propose.injections.proposal（结构化规则注入）
 5. [待开始] 1.8 加载 define-summary.yaml（pg-1-define 定界产物，条件性）
 6. [待开始] 2.1 生成 proposal.md
 7. [待开始] 2.2 生成 design.md（含"环境限制与验证策略"段）
 8. [待开始] 2.3 判定 affected_tracks & scenario track(s) 启用决策（--scenario-decisions + --scenario-reason **强必填**）
 9. [待开始] 2.4 生成 tasks.md（2.4.1 调用 pg-gen-tasks-skeleton.py + 2.4.2 LLM 填充 body）
10. [待开始] 2.5 生成 execution-manifest.yaml（pg-gen-manifest.py）
11. [待开始] 2.6 条件生成 scenario-<track>.yaml（2.6.1 调用 pg-gen-scenario.py + 2.6.2 LLM 填充 Scenario body + covers 引用 design.md V-*）
12. [待开始] 3.1 三产物一致性校验（pg-validate-proposal.py，**唯一**校验点）
13. [待开始] 4.1 展示产物摘要给用户
```

> **与正文阶段的映射关系**：
> - 阶段 1.1（{{pg:action.task_tracker}} 初始化）本身是创建本清单的动作，不计入 13 项
> - 阶段 1.2（确认变更名称）属于用户交互，不需要 {{pg:action.task_tracker}}
> - 阶段 1.5（获取管线配置）属于过渡步骤，无产物，不计入 13 项
> - 阶段 1.8（加载 define-summary.yaml）为条件性步骤（仅当 define-summary.yaml 存在时执行），仍占一项
> - 阶段 2.4 / 2.6 拆分后，每个子步骤共享一个 {{pg:action.task_tracker}} 项（2.4 → 9, 2.6 → 11），完成时同时更新

---

## 附录 B：产物清单（硬约束）

每个 change 在 `.pg/changes/<change>/` 下生成 6 个产物文件（5 必填 + 1 评审 + N 个条件性 scenario-<track>.yaml，N=启用 scenario track 数）；另有 1 个条件性前置输入（define-summary.yaml，由 pg-1-define 生成）：

| 产物 | 写入位置 | 何时生成 | 必填 |
|------|---------|---------|------|
| `proposal.md` | `.pg/changes/<change>/proposal.md` | 阶段 2.1 | ✅ 必填 |
| `design.md` | `.pg/changes/<change>/design.md` | 阶段 2.2 | ✅ 必填 |
| `tasks.md` | `.pg/changes/<change>/tasks.md` | 阶段 2.4（pg-gen-tasks-skeleton.py 生成，含 scenario 章节当且仅当至少一个 scenario track 启用） | ✅ 必填 |
| `execution-manifest.yaml` | `.pg/changes/<change>/execution-manifest.yaml` | 阶段 2.5（pg-gen-manifest.py 生成，含 scenario track 当且仅当对应 track 启用） | ✅ 必填 |
| `on-conditions-eval.md` | `.pg/changes/<change>/1-propose-review/on-conditions-eval.md` | 阶段 2.4.1（pg-gen-tasks-skeleton.py 生成，含 `scenario_tracks_decision` SSOT 段） | ✅ 必填 |
| `scenario-<track>.yaml` | `.pg/changes/<change>/scenario-<track>.yaml` | 阶段 2.6（pg-gen-scenario.py 生成，**每个启用**的 scenario track 一个文件） | ⚠️ 条件必填 |
| `0-define/define-summary.yaml` | `.pg/changes/<change>/0-define/define-summary.yaml` | pg-1-define「定界后环境验证」环节落盘（非 pg-propose 生成），阶段 1.8 加载 + 校验 | ⚠️ 条件性前置输入 |

### 三产物一致性约束（v3.6）

`tasks.md` / `execution-manifest.yaml` / `scenario-<track>.yaml` 三个产物严格一致，无冗余无回退：

- `on-conditions-eval.md` 的 `scenario_tracks_decision` 段是 SSOT（per-track）
- `pg-gen-tasks-skeleton.py` / `pg-gen-manifest.py` / `pg-gen-scenario.py` 三个脚本都从 SSOT 派生
- `pg-validate-proposal.py` 校验三产物与 SSOT 一致

---

## 附录 C：scenario.yaml 生成指引（v3.6+，仅当 scenario track 启用）

> **SSOT**：scenario-<track>.yaml 是 scenario-execute agent 的唯一输入，**禁止** scenario-execute agent 重写或修改。
> 修改需重跑 `pg-gen-scenario.py` 重新生成 skeleton。

**生成路径**：阶段 2.6.1 调用 `pg-gen-scenario.py` 自动写盘 `.pg/changes/<change>/scenario-<track>.yaml` skeleton（LLM 必填 Scenario 内容）。每个启用的 scenario track 生成一个独立文件。

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
3. **`and` cleanup 段必备**：每个 Scenario 都含 `and`，避免失败时脏数据污染（纯 browser-only 场景除外）
4. **Scenario 数量动态**：建议数 = `max(3, ceil(design.md 的 V-* 总数 × 0.8))`；上限软化为 7（超出仅 warning，不阻塞）；下限 2（含正/负至少各 1）
5. **覆盖度 5 维度**：scenario 集合须覆盖下列维度至少 3 项——
   - **happy**：正常流程跑通（200/201 + 资源落地）
   - **negative**：错误路径（404/422/403/资源不存在/参数非法）
   - **permission**：权限/边界（跨 tenant 访问、RBAC 拒绝）
   - **cross-module**：跨模块联调（backend + frontend + agent 联合）
   - **ui-smoke**：浏览器冒烟（type=browser，验证 DOM/Network/console）
6. **类型维度**：当 design.md 包含 frontend track 的 V-* 时，scenario 集合须同时含 ≥1 个 `type=api` 与 ≥1 个 `type=browser` 的 Scenario；纯 backend 改动不强制
7. **`covers` 追溯字段**：每个 Scenario 推荐含 `covers: [V-xxx-N, ...]` 列表，引用 design.md 中至少 1 条 V-* 验证项；`covers` 字段缺失或空数组 → `pg-validate-proposal.py` 警告（不阻塞）
8. **生成时优先级**：先写 happy → 再补 negative → 再补 permission / cross-module → 最后补 ui-smoke；`critical: true` 限 1-3 个（happy + 1 个 negative）

**严禁生成**以下文件（v1 遗留物，pg-build 不再读取）：

- ❌ `environment.yaml` —— per-change 的环境选择已写入 `execution-manifest.yaml` 的 `stages[i].environment` 字段，由 `pg-build` 直接读取

任何 stage 缺少必填产物文件 → workflow_failed 终止。多生成产物文件 → 后续 pg-build 会忽略，但污染产物目录。

---

## 附录 D：⛔ 禁令

下列操作在**整个提案阶段**均被禁止：

- ❌ 严禁修改任何业务代码文件
- ❌ 严禁执行 lint、typecheck、test 等验证命令
- ❌ 严禁启动任何服务（backend/frontend）
- ❌ 严禁修改 `0-define/define-summary.yaml`（pg-1-define 产物，propose 阶段只读；校验失败应回到 pg-1-define 修复）
- ❌ 严禁把 env-description.yaml / define-summary.yaml 内容复制到 proposal/design/tasks/scenario 产物中（只是约束看的）

---

## 附录 E：文档变更记录

- **v1.2.0（2026-08-05）**：pg-1-define「定界后环境验证」产物接入（define-summary.yaml）。
  - **新增阶段 1.8**：条件性加载 `.pg/changes/<change-id>/0-define/define-summary.yaml`（pg-1-define 定界环节落盘），只加载 + 校验，不做「假设 vs 事实」对账（对账在 pg-1-define 已完成）。
  - **新增 schema**：`.pg/skills/src/runtime/spec/define-summary.schema.json`；示例 `.pg/skills/examples/define-summary.example.yaml`。
  - **新增校验子命令**：`pg-validate-proposal.py define-summary <change-id>`（阶段 1.8 唯一校验点）：结构校验 + change_id 一致性 + target_environment 一致性 + env_resource_refs ↔ env-description 交叉校验。
  - **`env_resource_refs` 占位约定**：`{env.<段>[name=<资源名>]…}`，与 scenario given 的 `{env.…}` 占位一致，禁止硬编码 IP/hostname/端口。
  - **{{pg:action.task_tracker}} 12 项 → 13 项**：插入第 5 项（阶段 1.8），后续项编号顺延 +1。
  - **附录 B** 产物清单新增条件性前置输入 `0-define/define-summary.yaml`；**附录 D** 禁令新增"严禁修改 define-summary.yaml"+"严禁复制 define-summary/env-description 内容到产物"。
  - **pg-1-define.md** 同步：新增「定界后环境验证」可选环节（唯一允许落盘 + 调 describe_env 的环节，需用户明确授权）+ 「不要自动记录」护栏明文例外。
  - **设计动机**：把"发现环境无法提供测试依赖"的时机从 propose（非互动，无法要求人工介入）提前到 define（可互动，可当场与用户讨论降级路径）。
  - 单测：`tests/test_define_summary.py`（12 case，全通过）；存量 `test_review_section.py` 3 失败为本变更前既有问题，与本变更无关。

- **v1.1.0（2026-08-01）**：scenario 硬编码 endpoint 校验（P0-1）。
  - `pg-gen-scenario.py` skeleton 注释升级：given/when/then 段明确标注"必须用 `{env.<段>.<name>.<字段路径>}` 占位引用 env-description.yaml 资源"。
  - `pg-validate-proposal.py` 新增规则 `scenario_given_hardcoded_endpoint`：扫描 enabled scenario track 的 yaml 中 `given`/`when`/`then`/`evidence` 字段，命中 IPv4 / `ssh://user@` / `http(s)://host:port` / `port=N(4-5 digits)` 时报 ERROR（与 placeholder 同级）。
  - 豁免：`{env.*}` 占位符 / `#` 注释行 / `localhost` / `127.0.0.1` / `0.0.0.0` / `port<1000`。
  - 回滚路径：环境变量 `PG_PROPOSE_V110_HARDCODED=0` 临时关闭新规则。
  - 新增单测 `tests/test_scenario_hardcoded.py`（10 case）。
  - 触发：vm-agent-e2e archive 时 scenario-scr.yaml 命中 11 条硬编码错误（`192.168.122.221` / `ssh://ubuntu@` / `http://192.168.122.1:9082`），未来 propose 阶段即可拦截。

- **v1.0.1（2026-07-31）**：修复阶段 1 依赖倒置——交换 1.5/1.6 顺序。
  - **问题**：旧 1.5（describe_env）需要 `--env <env-name>`，但环境名的 SSOT（`config.stages[i].environment.selection_rules`）在旧 1.6（管线配置）才加载，导致 LLM 执行 1.5 时无法确定 env name。
  - **修复**：1.5 → 获取管线配置（原 1.6），1.6 → 生成环境描述（原 1.5）。依赖链变为 `管线配置 → env name → describe_env`，无倒置。
  - 1.5 新增"下游依赖"注释；1.6 关键约束新增 `--env` 参数来源说明；执行协议 bash 注释补充 env 来源。
  - 同步更新所有交叉引用（L28/L32/L282/L397/附录 A 第 3 项/附录 A 备注/附录 E v1.0.0 条目）。

- **v1.0.0（2026-07-29）**：大重构——编号体系 + 同步优化。
  - **编号体系重构**：阶段一 `1a/1b/.../1d.5/1e/1f` + 阶段二 `2a/2b/.../2g` + 阶段三/四 → 体系 A `阶段 1-4` + `附录 A-E`，纯数字编号。消除 1d.5 补丁型编号与 5.5 幽灵编号。
  - **{{pg:action.task_tracker}} 扩容**：10 项 → 12 项，与阶段编号一一对应。修复旧"13 项宣称 / 10 项列出"对齐失败。
  - **阶段 2 子步骤拆分**：2.4 → 2.4.1（脚本调用）+ 2.4.2（LLM body 填充）；2.6 → 2.6.1（脚本调用）+ 2.6.2（LLM body 填充）。避免占位符漏填被校验器拦截。
  - **阶段 3 强化**：明示"**唯一**校验点"，删除旧 2.5/2.6 内冗余 `ls` 步骤并入 `pg-validate-proposal.py`。
  - **scenario-decisions 强必填**：删除"空字符串默认启用"隐式行为；`--scenario-decisions` 与 `--scenario-reason` 均为必填，scenario-reason 需含结构化三问答复。
  - **1.6 段首强化**：明示 env-description.yaml Context 注入契约，约束阶段 2 全产物写作。
  - **版本号表述统一**：metadata.version 0.9.0 → 1.0.0；内部子版本号 v3.x 仅在变更记录保留。
  - **章节物理位置**：产物清单 + 三产物一致性约束 + scenario.yaml 生成指引 + 禁令 + 变更记录统一移至附录。

- **v0.9.0（2026-07-27）**：scenario 环境一致性强化。
  - 阶段 2f 新增步骤 2「生成环境能力摘要」（LLM 强制步骤）：从 env-capability.yaml 提取目标 env 的 host/services/缺失能力，输出 `[ENV-SUMMARY]` 作为 scenario given/then 的硬约束
  - 硬约束：scenario given 必须与 `[ENV-SUMMARY]` 一致；then 中数量断言 ≤ env 实际 host 数；不可验证的 V-* 不应出现在 scenario covers 中
  - `references/design-templates.md` 新增"环境限制与验证策略"段模板（design 阶段暴露哪些 V-* 在目标 env 不可验证）
  - `pg-gen-scenario.py` 新增 `--env-summary` 可选参数：skeleton 中注入 `[ENV-CONSTRAINT]` YAML 注释块
  - `pg-validate-proposal.py` 新增 `scenario_given_exceeds_env_capacity` / `scenario_given_unknown_host_id` 两条 warning 级规则（兜底拦截）

- **v0.8.4（2026-07-27）**：删除 review-notes 自审 + pg-propose-refine 流程。
  - 删除阶段三 6 类自审清单 + 4a/4b 智能分流 + review-notes.md 产物
  - 删除 `pg-propose-refine/` SKILL 目录、命令文件、`.pg/skills/src/core/workflows/` 软链接
  - 5 项 common decisions 固化为 `pg-gen-tasks-skeleton.py` 常量块
  - `pg-validate-proposal.py` 新增 3 条机械校验规则（V-* 映射 / scenario 引用防护 / 章节编号连续性）
  - `pg-gen-tasks-skeleton.py` 新增 `COMMON_DECISIONS` 常量块
  - 同步清理 `pg-build/SKILL.md`、`fix-review.yaml`、`scenario-fix.yaml`、`scenario-execute.yaml`、`reducer.py`、`bootstrap.py` 中对 `pg-propose-refine` 的硬编码提示文案（指向 `/2-pg-propose`）
  - 同步清理 `pg-quick-build/SKILL.md` 中"无 review-notes"说明
  - 旧 `.pg/changes/archive/*/review-notes.md` 不删，作为历史决策记录保留

- **v3.8（2026-07-26）**：删除 `.pg/context/summary.yaml` 中间缓存 + 扩展 `find AGENTS.md` 排除目录。
  - 删除 `summary.yaml` 缓存层：1d 阶段改为直接通读所有 AGENTS.md 提取 context，不再生成 / 读取 `.pg/context/summary.yaml`。联动删除 `scripts/check-review-cache.sh`、`src/runtime/bin/pg` 中 summary.yaml 检查项（doctor 编号重排 1-7）。`references/review-notes-format.md` / `references/design-templates.md` / `references/config-fields.md` 中"summary.yaml"字面替换为"AGENTS.md"。`docs/index.html` 描述同步更新。
  - 扩展 `find` 排除目录：除原有 `node_modules / target / .git / dist / build` 外，新增 `.pg/skills`（避免递归到 skill 仓库自身）+ 多语言 build 目录（`.next / .nuxt / .svelte-kit / .turbo / coverage / playwright-report / storybook-static / .gradle / out / __pycache__ / .venv / venv / .pytest_cache / .mypy_cache / .ruff_cache / .tox / htmlcov / vendor / .bundle / vendor/bundle / bin / obj / .dart_tool / .flutter-plugins / _build / deps / DerivedData`）。分组说明用 Markdown blockquote 写在代码块上方，避免行内 `#` 注释破坏 `\` 续行。
  - **向后兼容**：旧项目的 `.pg/context/summary.yaml` 文件可手动删除（pg-skills 不再读取）。新 `find` 命令对历史 change 目录无影响。

- **v3.7（2026-07-15）**：流程精简与自动应用。
  - **优化项 1**：合并 2e/2f 内的两次 `pg-validate-proposal.py` 调用到 2g，**唯一**校验点统一错误口径。
  - **优化项 2B**：`pg-gen-scenario.py` 新增 `check_scenario_placeholders()` / `check_scenario_file()`；`pg-validate-proposal.py` 在 2g 中校验 `scenario_<track>.yaml` 占位符已被 LLM 替换。新错误码 `scenario_placeholder_unfilled`。详见 `references/scenario-format.md` placeholder 校验协议段。
  - **优化项 3**：`SKILL.md` 的 `design.md 约束`段下放到 `references/design-templates.md` 的"## 约束"段，原位置留引用。
  - **优化项 4B**：阶段四新增 4a/4b 二分路径；新增脚本 `pg-auto-refine-check.py` 检测 review-notes.md 是否符合全推荐条件，符合则自动进入 refine 的"全推荐场景"分支（机械应用 5 项 common_decisions + 冻结），无需用户手动调用 refine。三触发条件：① 5 项 common_decisions `当前 == 推荐`；② 任意 `[ ]`/`[x]`（无 `[~]`）；③ 用户未编辑（无 `[~]`/`[x]`/`✅`/`已应用时间` 标记）。
  - 单元测试：新覆盖 `pg-gen-scenario.py` placeholder 校验 + `pg-auto-refine-check.py` 全推荐/有 SKIP/已编辑三场景；`pg-validate-proposal.py` placeholder 校验链路复用 `test_three_product_consistency.py` 已有 `setUp` 模式扩展。
  - 向后兼容：`scenario_<track>.yaml` 旧文件若含占位符会被新校验捕获；建议在 LLM 填充流程前先跑一次 `pg-validate-proposal.py` 看错误定位。

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