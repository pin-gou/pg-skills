---
name: pg-quick-build
description:  跳过 pg-propse，不生成 proposal.md/design.md/tasks.md，直接构建代码
license: MIT
compatibility: 需要 `.pg/project.yaml`（schema：modules / environments / tracks / stages）
metadata:
  author: pg
  version: "2.0"
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
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-quick-build
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
  └─ 0.5 question 确认 + TodoWrite
                                                       
Phase 1: 派遣
  ├─ 1.0 构造 ctx dict (design + tasks + modules + env + limits)
  └─ 1.1 Task tool → pg-quick-build/worker ──────────►  接收 ctx
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
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-quick-build
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
    {"id": "V-1", "check": "<可验证的描述>", "evidence": "<curl/日志/jq 形式>"},
    ...
  ],
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
- 所有 `verify` task 的 `covers_v` 合并 = `design.verification` 的 id 集合

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

#### 步骤 0.5：question 确认 + TodoWrite

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

**question tool**：

```
header: 确认计划
options:
  - 确认，开始执行 — 派遣 worker
  - 修改计划 — 用户提供调整
  - 改用 pg-propose — 范围太大, 走完整流程
```

**用户确认后**：

1. 创建 TodoWrite（9 项：步骤 0.0-0.5 + Phase 1 派遣 + Phase 2 收尾）
2. 更新 TodoWrite，准备进入 Phase 1

#### 步骤 0.6：Phase 0 自核查

```
- [ ] 步骤 0.0 自检表已填完整
- [ ] pg-parse-config 已读, modules + env 已取
- [ ] design 构造完成 (files ≤8, verification 至少 1 条)
- [ ] tasks 构造完成 (≤8, 单 module, test 在 dev 前, verify 在最后, covers_v 全覆盖)
- [ ] 上下文预估 ≤ 0.5 * MODEL_CTX
- [ ] 强停条件全部通过
- [ ] question 已确认
- [ ] TodoWrite 已创建
```

未通过 → 修正后再进入 Phase 1。

---

### Phase 1：单次派遣 worker

#### 步骤 1.1：构造 ctx dict

```python
ctx = {
  "design": design,                        # in-memory design dict
  "tasks": tasks,                          # in-memory tasks list
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

#### 步骤 1.2：Task tool 派遣

```python
result = task(
  subagent_type="general-purpose",   # 或 pg-quick-build/worker (如已注册)
  description="微变更全包执行: <design.summary>",
  prompt=build_worker_prompt(ctx),    # 见下方模板
)
```

**Worker prompt 模板**（主 agent 拼装，注入到 Task tool 的 prompt 参数）：

```
你是 pg-quick-build worker。请按以下 ctx 完成微变更全包执行（test → dev → verify → self_check → 自助修 bug）。

## 1. 变更摘要
{design.summary}

## 2. Design（口述版）
{yaml.dump(design, allow_unicode=True)}

## 3. Tasks（有序列表）
{yaml.dump(tasks, allow_unicode=True)}

## 4. Module 配置
{yaml.dump(modules_for_tasks, allow_unicode=True)}

## 5. Environment（仅 name）
env.name: {env_name}
> 环境的 instances / actions 等详情不注入到 prompt；请在步骤 1 环境自检中自行调用
> `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-env {env_name}`
> 获取 `resolved_actions` 后缓存到本地变量供后续 verify task 使用。

## 6. 限制与边界
- max_retries_per_task: 3
- max_total_retries: 8
- 分支: 保持当前分支（pg-quick-build 不切分支）
- 禁止修改 modules[*].root 之外的目录
- 禁止跨 task 边界修复
- 禁止 git push / gh pr create

## 7. 你的完整工作流
（详见 .opencode/agents/pg-quick-build/worker.md 完整说明）
- 步骤1: 环境自检 (git status, log) + --resolve-env 缓存
- 步骤2: 循环 tasks, 每 task 完成后 git commit
- 步骤3: 按 sub 分支执行 (test/dev/verify)
- 步骤4: 失败时 try_fix 自助修
- 步骤5: self_check 3 项 (返回前必做)

## 8. 返回格式
{yaml.dump(return_schema, allow_unicode=True)}
```

> **关键**：worker prompt 必须内联 worker.md 的完整行为规范（或明确指向 `.opencode/agents/pg-quick-build/worker.md` 并要求 worker 读取）。建议直接内联避免 worker 漏读。

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

### Self-check 结果
| 检查项 | 结果 |
|--------|------|
| V-* 覆盖 | ✅ |
| Lint/test 干净 | ✅ |
| 所有 task SUCCESS | ✅ |

### 后续建议（仅文字，不执行）
- 查看改动: `git status` / `git diff`
- 提交暂存: `git add -A && git commit --amend` (合并到上一个 commit) 或 `git reset --soft HEAD~N` 后重整
- 如需正式 proposal 化以备归档: 走 `/2-pg-propose <slug>`
- 如需合并到 master: 走 `/4-pg-verify-and-merge <slug>`（注意：微变更无 review-notes，pg-verify-and-merge 可能要求走 pg-propose 后再触发）
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
| 下游（可选）| `pg-verify-and-merge` | 微变更可直接推送+pr+merge; 但 pg-verify-and-merge 可能要求 review-notes，建议重要变更先走 pg-propose |

---

## 完成总结模板

主 agent 在 Phase 2 末尾输出（已嵌入步骤 2.1/2.2），不重复。

---

## ⛔ 禁令

- ❌ **禁止**调用 `pg-pipeline-runner.py`（runner 是 pg-build 专属）
- ❌ **禁止**在 `.pg/changes/` 下创建任何目录
- ❌ **禁止**加载 worker prompt 之外的 `pg-*` SKILL
- ❌ **禁止**主 agent 自己执行 mvn / curl / 启停服务（这些全部由 worker 完成）
- ❌ **禁止**主 agent 自己做 self_check（worker 的 3 项检查已足够）
- ❌ **禁止** git push / gh pr create（推送由用户自行决定）

---

## 与 pg-define 的集成

在 `pg-define` 模式结束时，如果判断变更范围较小（≤8 文件、≤8 tasks、单 module、无 design 评审需求），主 agent 应主动推荐：

> "这个需求比较清晰，变化范围不大，推荐直接用 `/2b-pg-quick-build <描述>` 快速实现。如果涉及跨模块依赖、复杂设计或需要文档审核，建议用 `/2-pg-propose` 生成完整提案。"
