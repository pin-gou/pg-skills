---
name: pg-quick-build
description: 仅当用户显式触发快速构建工作流时使用（`/2b-pg-quick-build` 命令，或用户明确说"直接实现/快速构建"）；与该工作流无关的日常任务禁止自行加载。功能：跳过 pg-propose，不生成 proposal.md/design.md/tasks.md，直接构建代码。
license: MIT
compatibility: 需要 `.pg/project.yaml`（schema：modules / environments / tracks / stages）
metadata:
  author: pg
  version: "2.1"
---

# pg-quick-build

pg-propose 的轻量版。**主 agent** 做定界（Phase 0）+ 单次派遣 worker sub-agent，**worker** 全包执行 test + dev + verify + fix + self_check。零产物落盘（不建 `.pg/changes/<name>/`，不写 design.md / tasks.md / proposal.md）。

## 适用范围

| 适合 | 不适合（强制退出 → 建议 `/2-pg-propose`） |
|------|------------------------------------------|
| tasks 列表 ≤8 条 | tasks > 8 条 |
| 文件 ≤8 个 | 文件 > 8 个 |
| 单 track（backend OR frontend OR agent）| 跨 ≥2 module |
| 不需要 design 评审 / 跨团队同步 | API 契约变更需 design review |
| 无 on_conditions 触发的非常驻 stage | 涉及 prepare-env-scripts 等环境层 |
| 无 K8s namespace / DB migration | 涉及数据库 schema 变更 |

**重要**：判定为不适合时，主 agent **立即停止**并通过 `question` tool 建议用户走 `/2-pg-propose`，不强行执行。

---

## 配置 SSOT

从 `.pg/project.yaml`（schema v2）按需读取：

| 路径 | 用途 |
|---|---|
| `modules[*].root` / `lint` / `test.unit` | 注入到 worker 的 module 配置 |
| `environments` 第一个 key + 完整定义 | 默认 environment（worker 自动用 `dev-local` 或配置首个） |
| `tracks[*].max_fix_retries` | 默认 fix 上限（worker `limits.max_retries_per_task` 默认 3） |

**不消费**：`stages`（worker 不按 stage 编排）、`propose.guidelines` / `propose.injections`（pg-propose 专属）、`regression.suite`（pg-regression 专属）、`git.*`（pg-archive 专属）。

启动时执行：

```bash
python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py pg-quick-build
# ↑ stdout 输出 JSON; 内建脚本存在性校验, exit code ≠ 0 → 修复 .pg/project.yaml 再继续
```

---

## 架构

```
主 agent (pg-quick-build SKILL)              worker sub-agent (pg-quick-build/worker)
─────────────────────────────────              ──────────────────────────────────────────
Phase 0: 定界
  ├─ 0.0 自检表
  ├─ 0.1 读 config (modules + environments[0])
  ├─ 0.2 构造 in-memory design
  ├─ 0.3 构造 in-memory tasks
  ├─ 0.4 上下文预估 + 强停判断
  └─ 0.5 question 确认 + {{pg:action.task_tracker}}
                                                       
Phase 1: 派遣
  ├─ 1.0 构造 ctx dict (design + tasks + modules + env + limits)
  └─ 1.1 {{pg:action.subagent_dispatcher}} → pg-quick-build/worker ──────────►  接收 ctx
                                                            ├─ 步骤1: 环境自检
                                                            ├─ 步骤2: 循环 tasks
                                                            │    ├─ sub=test  → 写测试
                                                            │    ├─ sub=dev   → 实现 + lint + test
                                                            │    └─ sub=verify → 启服务 + curl + lint
                                                            ├─ 步骤3: git commit 每 task
                                                            ├─ 步骤4: try_fix 自助修
                                                            └─ 步骤5: self_check 3 项
                                                      
Phase 2: 收尾                                     ◄─────  返回 {status, commits, evidence,
  ├─ 2.0 校验返回值结构                                     self_check, issues, summary}
  ├─ 2.1 SUCCESS → 输出摘要 + 推送建议 (仅文字)
  └─ 2.2 FAILED/ABORTED → 输出失败报告 + 建议走 pg-propose
```

---

## 工作流

### Phase 0：定界

> **目标**：分析需求、构造 design + tasks、做上下文预估、强停判断、获得用户确认。
>
> **禁令**：不生成任何落盘文件、不修改任何代码、不启动任何服务、不加载 worker prompt。

#### 步骤 0.0：自检表

在任何 `Read/Edit/Write/Bash`（除读取配置）之前填充：

```
- [ ] 变更名已确定（slug, kebab-case）
- [ ] 变更涉及的文件已列出（≤8 个）
- [ ] 涉及哪些 module（必须为 1 个）
- [ ] 涉及哪些 track（必须为 1 个）
- [ ] tasks 数量预估（必须 ≤8）
- [ ] 是否需要修改生产代码（默认 yes）
- [ ] 是否需要测试（默认 yes, 强断言）
- [ ] 默认 environment（取 environments[0]）
```

未完整 → 不得进入 0.1。

#### 步骤 0.1：读配置

```bash
python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py pg-quick-build
```

从输出 JSON 取 `modules` + `environments.keys()[0]` + 完整 env 定义。

#### 步骤 0.2：构造 in-memory design

主 agent 基于用户需求，在自己的对话上下文里构造：

```python
design = {
  "summary": "<一句话变更描述>",
  "files": [
    {"path": "<绝对或相对项目根路径>", "intent": "create|modify|delete", "approx_lines": <int>},
    ...
  ],
  "verification": [
    {"id": "V-1", "check": "<可验证的描述>", "evidence": "<curl/日志/jq 形式>",
     "verifiable": True},   # 步骤 0.5 填充; True=在目标 env 可达, False=资源缺失
    ...
  ],
  "context": {
    # 步骤 0.5 填充（仅当触发 env 探测时存在）
    "environment": {<describe_env 6 段资源拓扑>},
    "env_capability": {
      "source": "describe_env" | "skip",
      "verifiable_v": ["V-1"],
      "unverifiable_v": ["V-2"],
    },
  },
}
```

#### 步骤 0.3：构造 in-memory tasks

按依赖顺序构造（test 必须在 dev 之前，verify 必须在最后）：

```python
tasks = [
  {"id": 1, "sub": "test", "title": "...", "target_module": "...",
   "target_files": [...], "command_hint": "..."},
  {"id": 2, "sub": "dev", "title": "...", "target_module": "...",
   "target_files": [...], "constraint": "..."},
  {"id": 3, "sub": "verify", "title": "...", "target_module": "...",
   "target_files": [], "covers_v": ["V-1", "V-2"]},
  ...
]
```

**硬约束**：

- `len(tasks) <= 8`
- `len(design.files) <= 8`
- `target_module` 全部相同（即只 1 个 module）
- 至少 1 个 `sub=="verify"` task
- 所有 `verify` task 的 `covers_v` 合并 ⊆ `design.verification` 中 `verifiable=True` 的子集（步骤 0.5 后判定）
- 当步骤 0.5 跳过（`source = "skip"`）时，`covers_v` 合并 = `design.verification` 的 id 集合（v2.0 行为）

#### 步骤 0.4：上下文预估 + 强停判断

```python
def estimate_ctx(design, tasks):
    # 文件上下文: 每行 ~30 token
    file_ctx = sum(f["approx_lines"] * 30 for f in design["files"])
    # task 注入上下文: 每个 ~2.5K token (含 command_hint + 框架)
    task_ctx = len(tasks) * 2500
    # prompt 框架 + 自检输出: ~4K
    frame_ctx = 4000
    return file_ctx + task_ctx + frame_ctx

estimate = estimate_ctx(design, tasks)
MODEL_CTX = 128000  # pg-expert 上下文窗口

if estimate > 0.5 * MODEL_CTX:
    abort_with_suggestion("预估上下文超限, 建议走 pg-propose")
```

**强停条件**（任一命中即停）：

| 条件 | 建议 |
|---|---|
| `len(tasks) > 8` | 拆分为多个微变更, 或走 pg-propose |
| `len(design.files) > 8` | 同上 |
| `affected_modules` size > 1 | 走 pg-propose（跨模块）|
| `estimate > 0.5 * MODEL_CTX` | 走 pg-propose |
| 用户需求涉及 DB migration / K8s 资源 | 走 pg-propose |
| 用户需求涉及多 track 联调 | 走 pg-propose |

#### 步骤 0.5：env-description 真实探测 + V-* 可达性过滤（v2.1 新增，可选）

> **目标**：在派遣 worker 之前，对 design.verification 每条 V-* 做"目标 env 是否真实可达"判定，避免 worker 收到"看起来合理但跑不通"的验证任务，显著降低 worker 失败重试次数。
>
> **白名单触发**：仅当满足以下任一条件时执行，否则跳过（`source = "skip"`），保持 v2.0 行为：
> - 用户需求涉及多环境（staging / prod）
> - 涉及 K8s namespace / DB / Cache / MQ / 外部服务
> - 用户在 pg-define 阶段明确要求 env 探测
>
> **零产物承诺**：本步骤的 env-description 输出写 `.pg/quick-build/<session>/env-description.yaml`（**不写 `.pg/changes/`**，不污染 change 目录）。`source = "describe_env"` 时该文件存在；`source = "skip"` 时不存在。

#### 步骤 0.5.1：调用 describe_env

```bash
SESSION_ID="<iso-date>-<keyword>"   # 与 AGENTS.md §7.2 格式一致

python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
  --caller pg-quick-build \
  --session "$SESSION_ID" \
  --env <env_name> \
  --action describe_env
```

> caller `pg-quick-build` 是 v2.1 新注册的合法 caller（详见 `src/runtime/spec/hook-env-vars.yaml` v6 + `pg-invoke-hook.py:KNOWN_CALLERS`），日志路由到 `.pg/quick-build/<session>/<env>-logs/`。

**读取输出**：env-description.yaml 落到 `${PG_OUTPUT_PATH}`（caller 注入到 hook），主 agent 提取 6 段（infra_services / business_systems / data_resources / config_resources / runtime_environment / external_dependencies）注入到 `design.context.environment`。

#### 步骤 0.5.2：V-* 可达性判定

调用 `pg-quick-build-env-capability.py`（新增脚本，纯函数）：

```bash
python3 .pg/skills/src/core/workflows/skills/pg-quick-build/scripts/pg-quick-build-env-capability.py <<EOF
{
  "env_description": $(jq . < .pg/quick-build/<session>/env-description.yaml),
  "verifications": ${design_verifications_json}
}
EOF
```

脚本输出：

```json
{
  "verifiable_v": ["V-1", "V-3"],
  "unverifiable_v": ["V-2"],
  "issues": [
    {"v_id": "V-2", "reason": "state_missing", "resource_ref": "infra_services[name=postgres]"}
  ]
}
```

**判定规则**（与 pg-propose v0.9.0 同步）：

- V-* check / evidence 字段引用形如 `infra_services[name=postgres].instances[0].id` 的资源 ID
- 该资源存在于 env-description 且 `state.status ∈ {ready, configured, seeded, running, available}` → `verifiable`
- 否则 → `unverifiable`，进入 `design.context.env_capability.unverifiable_v` 留痕
- 资源命名严格使用 env-description 中的具体 ID（禁止以"环境未就绪" / "OSS 未配置"为兜底）
- 未引用任何资源 ID 的 V-*（纯单元测试 / 静态分析）默认 `verifiable`

#### 步骤 0.5.3：过滤 tasks.covers_v

调用脚本同文件的 `filter_covers_v(tasks, verifiable_v)`，从 verify task 的 `covers_v` 中剔除 unverifiable V-*。

**强停条件新增**（任一命中即停）：

| 条件 | 建议 |
|---|---|
| `len(unverifiable_v) > len(verifiable_v)` | V-* 多数不可达（>=50%），建议走 pg-propose 完整产物路径 |
| describe_env hook 调用失败（含 timeout / exit 非 0） | 整个流程 abort + 建议走 pg-propose（环境探测失败不该让 worker 盲目跑） |

#### 步骤 0.5.4：Phase 0.5 自核查

```
- [ ] describe_env 调用成功（或 source 显式标记 skip）
- [ ] design.context.environment 已填充 6 段资源拓扑（skip 时为空对象）
- [ ] design.context.env_capability.{verifiable_v, unverifiable_v, issues} 三段已填充
- [ ] tasks 中所有 verify task 的 covers_v 已过滤（无 unverifiable V 残留）
- [ ] 强停条件两项全部通过
```

未通过 → 修正后再进入步骤 0.6（question 确认）。

#### 步骤 0.6：question 确认 + {{pg:action.task_tracker}}

**展示计划**：

```
## 计划

**变更名**: <slug>
**变更摘要**: <design.summary>
**Environment**: <env_name> (config.yaml 中 environments 第一个)
**Module**: <唯一 module>
**分支**: 保持在当前分支（pg-quick-build 不切分支，直接在原分支上修改代码）

### Design
| 文件 | 改动 | 预估行数 |
|------|------|----------|
| ... | create/modify/delete | N |

### Verification
| ID | 验证项 | 证据形式 |
|----|--------|----------|
| V-1 | ... | curl ... |
| V-2 | ... | mvn checkstyle 日志 |

### Tasks
| # | sub | 标题 |
|---|-----|------|
| 1 | test  | ... |
| 2 | dev   | ... |
| 3 | verify| ... |

### 预估上下文
~<N>K tokens (limit: 64K)

### Worker 单派遣
所有 task 由 `pg-quick-build/worker` 一次性完成（test → dev → verify → 自检 → 自助修 bug）。
```

**{{pg:action.user_question}}**：

```
header: 确认计划
options:
  - 确认，开始执行 — 派遣 worker
  - 修改计划 — 用户提供调整
  - 改用 pg-propose — 范围太大, 走完整流程
```

**用户确认后**：

1. 创建 {{pg:action.task_tracker}}（9 项：步骤 0.0-0.5 + Phase 1 派遣 + Phase 2 收尾）
2. 更新 {{pg:action.task_tracker}}，准备进入 Phase 1

#### 步骤 0.7：Phase 0 自核查

```
- [ ] 步骤 0.0 自检表已填完整
- [ ] pg-parse-config 已读, modules + env 已取
- [ ] design 构造完成 (files ≤8, verification 至少 1 条, 含 verifiable 字段)
- [ ] tasks 构造完成 (≤8, 单 module, test 在 dev 前, verify 在最后)
- [ ] 上下文预估 ≤ 0.5 * MODEL_CTX
- [ ] 步骤 0.5 已执行或显式 skip；covers_v 已过滤
- [ ] 强停条件全部通过（含 unverifiable_v 占比检查）
- [ ] question 已确认
- [ ] {{pg:action.task_tracker}} 已创建
```

未通过 → 修正后再进入 Phase 1。

---

### Phase 1：单次派遣 worker

#### 步骤 1.1：构造 ctx dict

```python
ctx = {
  "design": design,                        # in-memory design dict (含 context.{environment, env_capability})
  "tasks": tasks,                          # in-memory tasks list (covers_v 已过滤)
  "modules": config["modules"],            # 完整 modules 段
  "env": {
    "name": env_name,                      # environments[0] - worker 自行 --resolve-env 取详情
  },
  "limits": {
    "max_retries_per_task": 3,
    "max_total_retries": 8,
  },
}
```

#### 步骤 1.2：{{pg:action.subagent_dispatcher}} 派遣

```python
result = task(
  {{pg:action.subagent_agent_parameter}}="general-purpose",   # 或 pg-quick-build/worker (如已注册)
  description="微变更全包执行: <design.summary>",
  prompt=build_worker_prompt(ctx),    # 见下方模板
)
```

**Worker prompt 模板**（主 agent 拼装，注入到 {{pg:action.subagent_dispatcher}} 的 prompt 参数）：

```
你是 pg-quick-build worker。请按以下 ctx 完成微变更全包执行（test → dev → verify → self_check → 自助修 bug）。

## 1. 变更摘要
{design.summary}

## 2. Design（口述版）
{yaml.dump(design_without_context, allow_unicode=True)}
> 注：design.context.environment 是步骤 0.5 env 真实探测结果，仅作为 dev/verify 阶段的资源引用参考，
> 不进入覆盖度计算。

## 3. Tasks（有序列表）
{yaml.dump(tasks, allow_unicode=True)}

## 3.5 Env 上下文（来自 describe_env，仅当 design.context.environment 非空时存在）
{yaml.dump(design.context.environment, allow_unicode=True, default_flow_style=False)}
> 约束：
> - dev 阶段需要引用外部资源时（如 DB host / K8s namespace），使用本段中的具体 instance id
> - verify 阶段启动服务 / 探针端口时，使用本段中的 endpoint / port 字段
> - 禁止假设本段之外的资源存在
> - 当本段为空（source=skip）时，按 v2.0 行为执行，仅靠 --resolve-env 拿 actions

## 4. Module 配置
{yaml.dump(modules_for_tasks, allow_unicode=True)}

## 5. Environment（仅 name）
env.name: {env_name}
> 环境的 instances / actions 等详情不注入到 prompt；请在步骤 1 环境自检中自行调用
> `python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py --resolve-env {env_name}`
> 获取 `resolved_actions` 后缓存到本地变量供后续 verify task 使用。

## 6. 限制与边界
- max_retries_per_task: 3
- max_total_retries: 8
- 分支: 保持当前分支（pg-quick-build 不切分支）
- 禁止修改 modules[*].root 之外的目录
- 禁止跨 task 边界修复
- 禁止 git push / gh pr create

## 7. 你的完整工作流
（详见 .pg/skills/src/core/workflows/agents/pg-quick-build/worker.md 完整说明）
- 步骤1: 环境自检 (git status, log) + --resolve-env 缓存
- 步骤2: 循环 tasks, 每 task 完成后 git commit
- 步骤3: 按 sub 分支执行 (test/dev/verify)
- 步骤4: 失败时 try_fix 自助修
- 步骤5: self_check 3 项 (返回前必做)
  - V-* 覆盖度仅计算 design.verification 中 verifiable=true 的子集（由 ctx 给出）

## 8. 返回格式
{yaml.dump(return_schema, allow_unicode=True)}
```

> **关键**：worker prompt 必须内联 worker.md 的完整行为规范（或明确指向 `.pg/skills/src/core/workflows/agents/pg-quick-build/worker.md` 并要求 worker 读取）。建议直接内联避免 worker 漏读。

#### 步骤 1.3：接收 result，做最小校验

主 agent 只校验返回值的**结构完整性**（不重复 worker 的 3 项 self_check）：

```python
assert result["status"] in ("SUCCESS", "FAILED", "ABORTED")
assert isinstance(result["evidence"], dict)
assert isinstance(result["self_check"], dict)
```

任一断言失败 → 视为 INFRASTRUCTURE_FAILURE，不计入重试，报告用户。

---

### Phase 2：收尾

#### 步骤 2.1：SUCCESS 路径

输出摘要（不执行 push）：

```
## 微变更完成

**变更名**: {slug}
**Environment**: {env_name}
**Module**: {module_name}
**Tasks**: {len(completed)}/{len(tasks)} 完成
**Commit 数**: {len(commits)} (worker 每 task 一 commit)

| # | sub | 标题 | commit | 状态 |
|---|-----|------|--------|------|
| 1 | test  | ... | abc1234 | ✅ |
| 2 | dev   | ... | def5678 | ✅ |
| 3 | verify| ... | ghi9012 | ✅ |

### V-* 证据
- **V-1**: <evidence 摘要>
- **V-2**: <evidence 摘要>

### Env 探测（v2.1 新增）
- 来源: {describe_env | skip}
- Verifiable V: {verifiable_v 列表}
- Unverifiable V: {unverifiable_v 列表}（已被步骤 0.5 剔除出 covers_v）

### Self-check 结果
| 检查项 | 结果 |
|--------|------|
| V-* 覆盖（仅 verifiable 子集） | ✅ |
| Lint/test 干净 | ✅ |
| 所有 task SUCCESS | ✅ |

### 后续建议（仅文字，不执行）
- 查看改动: `git status` / `git diff`
- 提交暂存: `git add -A && git commit --amend` (合并到上一个 commit) 或 `git reset --soft HEAD~N` 后重整
- 如需正式 proposal 化以备归档: 走 `/2-pg-propose <slug>`
- 如需合并到 master: 由用户明确要求（如"verify 并合并"）后触发 pg-verify-and-merge（微变更无 review-notes 流程，pg-verify-and-merge 直接接收）
```

#### 步骤 2.2：FAILED / ABORTED 路径

输出失败报告：

```
## 微变更失败

**变更名**: {slug}
**状态**: {result.status}
**完成 task**: {len(tasks_completed)}/{len(tasks)}

### 失败 task
- **task #{id}** ({sub}): {summary}
  - 最后错误: <output 末尾 20 行>

### Self-check 结果
<列出 PASS/FAIL>

### 建议
- 失败 task 已被 worker 自助修尝试 {max_retries_per_task} 次后放弃
- 建议改走 `/2-pg-propose {slug}` 生成完整提案 + design + tasks, 由 pg-build 全流程接手
- 或手动修复后重新 `/2b-pg-quick-build <summary>` 重跑（会基于现有分支继续）

### 当前分支状态
git log --oneline -10
<最近 10 个 commits>
```

---

## 错误处理

### 强停条件（Phase 0 触发）

任一命中 → 主 agent 立即停止 + 通过 `question` 建议走 pg-propose：

| 条件 | 处理 |
|---|---|
| `len(tasks) > 8` | "任务过多（{N} > 8），建议拆分为多个微变更或走 pg-propose" |
| `len(design.files) > 8` | "文件过多（{N} > 8），建议走 pg-propose" |
| `affected_modules.size > 1` | "跨 {N} 个 module，建议走 pg-propose" |
| `estimate > 0.5 * ctx` | "预估上下文超限，建议走 pg-propose" |
| 涉及 DB migration / K8s 资源 | "涉及基础设施层变更，建议走 pg-propose" |
| `len(unverifiable_v) > len(verifiable_v)`（v2.1） | "半数以上 V-* 在目标 env 不可达，建议走 pg-propose 完整路径" |
| describe_env 调用失败（v2.1） | "env-description 探测失败，建议走 pg-propose 由 v6 完整路径处理" |

### Worker 失败（Phase 1/2 触发）

| 情况 | 处理 |
|---|---|
| Worker 返回 FAILED（self_check 不通过）| 列出 issues，建议走 pg-propose 或手动修 |
| Worker 返回 ABORTED（3 次重试失败 / 累计 > 8）| 列出失败 task，建议走 pg-propose |
| Worker 返回值结构不完整（assert 失败）| 视为 INFRASTRUCTURE_FAILURE，不重试，报告用户 |
| Worker 执行超时（bash tool 触发 timeout）| 检查 git log 确认已有 commits, 输出当前进度，报告用户 |

### 基础设施失败

- `pg-parse-config.py` exit code ≠ 0 → 修复 config.yaml 后重试

---

## 与其他 SKILL 的关系

| 上下游 | SKILL | 关系 |
|---|---|---|
| 上游 | `pg-define` (command) | pg-quick-build 可在 pg-define 探索后接管, 不强制 |
| 同级 | `pg-propose` | pg-propose 生成完整 proposal/design/tasks 落盘; pg-quick-build 不落盘 |
| 同级 | `pg-fix-issue` | bug 修复场景优选 pg-fix-issue; pg-quick-build 不适合修复 bug |
| 下游（可选，仅用户显式触发）| `pg-verify-and-merge` | 微变更可直接推送+pr+merge; 重要变更建议先走 pg-propose 生成完整产物; 合并动作需用户明确要求后触发 |

---

## 完成总结模板

主 agent 在 Phase 2 末尾输出（已嵌入步骤 2.1/2.2），不重复。

---

## ⛔ 禁令

- ❌ **禁止**调用 `pg-pipeline-runner.py`（runner 是 pg-build 专属）
- ❌ **禁止**在 `.pg/changes/` 下创建任何目录（v2.1 例外：env-description 输出写 `.pg/quick-build/<session>/`，**不**写 `.pg/changes/`）
- ❌ **禁止**加载 worker prompt 之外的 `pg-*` SKILL
- ❌ **禁止**主 agent 自己执行 mvn / curl / 启停服务（这些全部由 worker 完成）
- ❌ **禁止**主 agent 自己做 self_check（worker 的 3 项检查已足够）
- ❌ **禁止** git push / gh pr create（推送由用户自行决定）
- ❌ **禁止**把 env-description 复制到 worker prompt 之外的产物（v2.1 约束：仅注入主 agent context 与 worker prompt §3.5）

---

## 与 pg-define 的集成

在 `pg-define` 模式结束时，如果判断变更范围较小（≤8 文件、≤8 tasks、单 module、无 design 评审需求），主 agent 应主动推荐：

> "这个需求比较清晰，变化范围不大，推荐直接用 `/2b-pg-quick-build <描述>` 快速实现。如果涉及跨模块依赖、复杂设计或需要文档审核，建议用 `/2-pg-propose` 生成完整提案。"
