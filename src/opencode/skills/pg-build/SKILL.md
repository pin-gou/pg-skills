---
name: pg-build
description: 基于 Pipeline Runner 的变更实现工作流。在 pg-propose 生成变更产物后执行，runner 脚本自动编排流程，LLM 仅负责子 agent 派遣。
license: MIT
compatibility: 项目根目录需要 `.pg/project.yaml` 统一配置文件（schema：modules / environments / tracks / stages 四段结构，schema 校验见 `.pg/skills/src/runtime/spec/project.schema.json`）。
metadata:
  author: pg-spec
  version: "3.1"
---

# pg-build

端到端实现一个变更，使用 Pipeline Runner 脚本自动编排执行顺序、重试、回退。
LLM 只做两件事：调用 `next` → 派送子 agent → 调用 `record`。

---

## 报告体系

pg-build 的所有过程产物统一放在 `<change>/2-build/` 子目录下（与 `1-propose-review/` 平行）。核心交付物（`proposal.md` / `design.md` / `tasks.md`）保留在 change 根目录。

子 agent 产出的报告采用**序号式命名**：`{track.id}-{N}-{kind}.md`

- `{track.id}` — track 名称（backend / frontend / agent / c / g 等）
- `{N}` — 该 track 内报告的累计序号，由 agent 启动时**扫描子目录已有文件推断**（取最大 + 1，**无需 runner 传递**）
- `{kind}` — 报告类型，区分语义

### 报告类型与生成者

| 报告 | 文件名模式（位于 `2-build/`）| 生成者 | 触发源 | 关注点 |
|------|----------|--------|--------|--------|
| **验证报告** | `{track.id}-{N}-verify.md` | verify agent | test/dev 完成后；或 fix 循环后 re-verify | "我**验证了**哪些 V-N 项、结果如何" |
| **修复记录（verify 触发）** | `{track.id}-{N}-verify-fix.md` | fix agent | verify ESCALATE | "我**修复了** verify 派发的 issue" |
| **门控评估报告** | `{track.id}-{N}-gate-assessment.md` | **gate agent（自行写盘）** | verify PROCEED 后 | "我**评审了**哪些 P-N 项、PASS/FAIL" |
| **修复记录（gate 触发）** | `{track.id}-{N}-gate-fix.md` | fix-gate agent | gate FAIL | "我**修复了** gate 列出的 G-N gap"；description：fix-gate agent **直接读源 gate 报告**（dispatch_file 注入 `context.gate_report_path`），runner **不**解析 G-N 章节、不提取 `gate_gap_id` / `file_pos` / `fix_hint` 等结构化字段——与 verify-fix 行为一致：fix agent 直接读源 verify 报告 |
| **跨 track 总评** | `final-gate-assessment.md` | **final-gate（gate agent 复用，自行写盘）** | 所有 track gate PASS 后 | 跨 track 整体判定（**不嵌入序号**） |

> **写盘责任统一**：所有 4 类报告（verify / verify-fix / gate / final-gate）均由 **对应 sub-agent 自行用 `cat >` 写盘**，编排器不替写。LLM 主循环收到 agent 返回后**只**做 `record`，不再做 `cat > ...` 落盘。

> **state 文件**：`.context-chain.state`、`.pipeline-state.json` 也存放在 `2-build/` 子目录下。

### Change 目录布局

```
.pg/changes/<change>/
├── proposal.md            ← pg-propose 交付物（不动）
├── design.md              ← pg-propose 交付物（不动）
├── tasks.md               ← pg-propose 交付物（不动，pg-build 持续更新 checkbox）
├── 1-propose-review/      ← pg-propose 阶段自审产物（build 不读，归档保留）
│   └── review-notes.md    ← 单文档评审：通用决策表 + 问题清单 checkbox
└── 2-build/        ← pg-build 过程产物
    ├── context-chain.md
    ├── .context-chain.state
    ├── .pipeline-state.json
    ├── known-issues.md
    ├── final-gate-assessment.md
    └── {track}-{N}-{kind}.md
```

### review-notes.md 在 apply 阶段的角色

pg-build **不读** `1-propose-review/review-notes.md`。

- **不消费**：build 阶段的所有 agent（test / dev / verify / gate / fix / fix-gate / final-gate）**不应**读取 review-notes.md 寻找"做什么/怎么验证"的指引——这些信息已在 `proposal.md` / `design.md` / `tasks.md` 中
- **不修改**：build 阶段**不应**修改 review-notes.md（refine 阶段独占）
- **归档保留**：变更归档到 `changes/archive/` 时，review-notes.md 随 proposal/design/tasks 一起复制归档，作为"评审历史"完整快照

### 序号作用域

- **每 track 独立从 1 开始**：`backend-1-*` 与 `frontend-1-*` 互不干扰
- **同一 track 内序号连续**：`backend-3-verify.md` (ESCALATE) → `backend-4-verify-fix.md` (修复) → `backend-5-verify.md` (re-verify)
- **final-gate 独立命名**：不嵌入序号

### 序号推断算法（agent 启动时执行）

```bash
# 1. 列同 track 已有报告 (扫描 2-build/ 子目录)
existing=$(ls .pg/changes/{change_name}/2-build/{track.id}-*.md 2>/dev/null)

# 2. 提取已有最大序号
max_n=$(echo "$existing" | grep -oP "(?<=${track.id}-)\d+(?=-)" | sort -n | tail -1)

# 3. 新序号 = max_n + 1（无文件时为 1）
new_n=$(( ${max_n:-0} + 1 ))

# 4. 写文件前再扫一次, 确认无并发冲突
```

### 阅读路径示例

**路径 A：顺利通过**

```
backend-1-verify.md           (PROCEED)
backend-2-gate-assessment.md  (PASS)
```

**路径 B：verify ESCALATE → fix → re-verify**

```
backend-1-verify.md           (ESCALATE)
backend-2-verify-fix.md       (修复记录)
backend-3-verify.md           (PROCEED)
backend-4-gate-assessment.md  (PASS)
```

**路径 C：gate FAIL → fix-gate → re-verify**

```
backend-1-verify.md           (PROCEED)
backend-2-gate-assessment.md  (FAIL)
backend-3-gate-fix.md         (修复记录)
backend-4-verify.md           (PROCEED)
backend-5-gate-assessment.md  (PASS)
```

**路径 D：混合（最复杂）**

```
backend-1-verify.md           (ESCALATE)
backend-2-verify.md           (re-verify, PROCEED)
backend-3-gate-assessment.md  (FAIL)
backend-4-gate-fix.md         (修复)
backend-5-verify.md           (re-verify, PROCEED)
backend-6-gate-assessment.md  (PASS)
```

> **历史归档**：已存在的 `verification-report-*` / `fixed-gaps-cycle-*` / `gate-assessment-*` 文件（位于 archive 子目录或 change 根）**不动**，新报告一律用新名 + 新子目录。runner 启动时会自动迁移 change 根目录的 state 文件到 `2-build/` 子目录（幂等操作）。

---

**脚本**：`.opencode/skills/pg-build/scripts/pg-pipeline-runner.py`

LLM **只需跟这一个脚本交互**，将其赋值给变量简化调用：

```bash
RUNNER="python3 .opencode/skills/pg-build/scripts/pg-pipeline-runner.py"
```

---

## 前置条件

变更产物（`proposal.md`、`design.md`、`tasks.md`）位于 `.pg/changes/<change-name>/` 下。
项目根目录有 `.pg/project.yaml` 且包含 `pipeline.order` 和 `pipeline.tracks`。

---

## 执行流程

Runner 脚本封装了 pg-build 的全部编排逻辑（当前阶段检测、跳过、子阶段推进、重试限制、回退、上下文链记录）。
LLM 主循环是**调用-派送-记录**循环，每次派送前执行以下 3 步构建 prompt：

```
RUNNER="python3 .opencode/skills/pg-build/scripts/pg-pipeline-runner.py"
CALL_TIMEOUT=600  # 首次调用默认 timeout，后续由 runner 返回的 next_call_timeout_seconds 更新

while true; do
  ACTION_JSON=$(bash -c "$RUNNER next $CHANGE" 2>&1)  # 缓存动作 JSON
  # 解析本轮推荐 timeout 供下一次 bash 调用使用
  CALL_TIMEOUT=$(echo "$ACTION_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('next_call_timeout_seconds', $CALL_TIMEOUT))")
  ACTION=$(echo "$ACTION_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('action', ''))")
  case $ACTION in
    "dispatch")         → 传 dispatch_file 路径给 sub-agent（见「派送 sub-agent」节）→ record
    "dispatch_fix")     → 同上（runner 已把 `context.verify_report_path` 注入 dispatch_file，fix agent 自行读源 verify 报告）
    "dispatch_final_gate") → 同上（runner 已把 final-gate 审计上下文写进 dispatch_file）
    "error") → 打印 ACTION.reason 和 ACTION.fix_hint 给用户 → 询问如何处理（手动修复 / 删除 state.json 从头开始 / 终止）→ 不自动重试
    "phase_result")
      缓存 ACTION.environment 信息（name / config / instances）
      if $ACTION.terminate == true:
        → 输出失败报告（prepare_env 失败，流程终止）
        → break
      else:
        → $RUNNER record <change> completed "" "env ready"
      ;;
    "done")
      if $ACTION.status == "completed":
        → 输出摘要报告
        → 加载 pg-verify-and-merge SKILL 继续执行（自动合并验证）
      else:
        → 输出失败报告 → break
      ;;
    "workflow_failed") → 输出报告 → break  # fatal=true，禁止重试
  esac
done
```
> **关于 `CALL_TIMEOUT`**：每次 `next`/`record` 的响应都包含 `next_call_timeout_seconds` 字段，是 runner 根据 config.yaml 中所有 `prepare_env`/`clean_env` 的 `timeout_seconds` 最大值 + 30s 余量算出的。LLM 必须将此值作为下一次 bash 调用的 timeout，确保长时间运行的 phase 脚本（如 `dev-local-setup.sh` 需 300s）不被 bash tool 的默认 120s 超时提前杀死，首次调用 `next` 时，超时时间设置为 600s。

> ⚠️ **编排器禁止令**：编排器（主循环）不得执行任何实现/验证/部署操作（mvn、curl、启停服务等）。子 agent 返回空时只能 `record failed` → `next`，由 runner 自动重试该子 agent。自行干活会破坏职责分离和 runner state 一致性。

### 启动时的初始化提交

runner 在**第一次派遣 sub-agent 之前**会同步执行一次 `git add -A` + `git commit`，**不 push**。这样：

- `pg-propose` 阶段产出的提案原产物（`proposal.md` / `design.md` / `tasks.md` 等）作为"pg-build 启动基线"落 git，与后续 sub 阶段的代码改动在 git 历史中清晰分层。
- feature branch 切换是 runner 启动期的副作用，init commit 自然落在 `feat/pg/<change>` 分支上。
- 失败不会阻塞 dispatch；runner 把 init commit 的执行结果暴露给 LLM，便于补做或人工补救。

**行为细节**：

1. **触发时机**：在 `cmd_next` 中按以下顺序——
   1. `migrate_legacy_state_files`（创建 `2-build/`，迁移遗留 state 文件）
   2. `_ensure_context_chain`（写 `context-chain.md`）
   3. **`_ensure_feature_branch`** ← 切到 `feat/pg/<change>` 分支
   4. **`_maybe_bootstrap_init_commit`** ← 先 `save_state({init_committed: True})` 标记 + 写 `2-build/.pipeline-state.json`，再 `_auto_commit_on_init` 提交
   5. 派遣第一个 sub-agent
2. **commit message**：固定格式 `chore(<change>): bootstrap pg-build`。
3. **commit 内容**：`git add -A` 全量提交，覆盖——
   - 提案原产物（`proposal.md` / `design.md` / `tasks.md` 等）
   - 干净的 `2-build/.pipeline-state.json`（仅含 `init_committed: True` 与 change name）
   - `_ensure_feature_branch` 在切换分支前的 stash（如有）
4. **跳过条件**：若 `git status --porcelain` 为空（理论不会发生，但兜底），跳过并把 `reason` 写进 `init_commit.reason`。
5. **幂等性**：靠 `state["init_committed"]` 字段保证只触发一次。Runner 进程内 / 跨会话均幂等——`save_state` 把标记写入 `2-build/.pipeline-state.json`，下次 `cmd_next` 通过 `load_state` 读取后跳过。
6. **失败处理**：init commit 失败时**仍**会把 `init_committed=True` 写入 state（避免每次重试都失败），但 `init_commit.committed=false` 暴露给 LLM，由 LLM 决定是否手动 `git commit` 补救。**不阻塞 dispatch**。
7. **与 migrate 的协作**：init commit 在 `migrate_legacy_state_files` **之前**执行。由于 init 时 `2-build/.pipeline-state.json` 已通过 `save_state` 写入（target 已存在），后续 migrate 看到遗留的 `change_root/.pipeline-state.json` 时会走"target 已存在则删除 legacy"分支，**不会覆盖**我们刚写入的 `init_committed` 标记。
8. **不 push**：推送仍由 `pg-verify-and-merge` 的 Phase 3 统一完成。

**`next` 返回 JSON 新增 `init_commit` 字段**（仅首次派遣时挂载）：

```json
{
  "action": "dispatch",
  "item": "backend",
  "sub": "test",
  ...,
  "commit": {
    "attempted": true,
    "committed": true,
    "branch": "feat/pg/my-change",
    "sha": "def5678",
    "message": "chore(my-change): auto-record backend:test completed",
    "reason": null
  },
  "init_commit": {
    "attempted": true,
    "committed": true,
    "branch": "feat/pg/my-change",
    "sha": "abc1234",
    "message": "chore(my-change): bootstrap pg-build",
    "reason": null
  }
}
```

跳过场景：

```json
{
  "init_commit": {
    "attempted": true,
    "committed": false,
    "branch": "feat/pg/my-change",
    "reason": "工作区干净，无可提交内容（init 阶段）"
  }
}
```

> **`init_commit` 字段挂载范围**：仅在 `cmd_next` 首次返回 `dispatch` action 时挂载；`dispatch_fix` / `dispatch_final_gate` / `execute_phase` / `done` / `workflow_failed` 路径**不挂**。这是因为 `state["init_committed"]` 标记已经守门——只有第一次派遣会真正触发 init commit。

LLM 在收到首次 dispatch 返回时，可在终端简报里同时展示 `init_commit.committed` / `init_commit.sha` 与 `commit.committed` / `commit.sha`，方便用户跟踪"启动基线 + 当前阶段"的两层 git 历史。

### 每次 record 的自动提交

runner 在每次 `record` 同步执行一次 `git add -A` + `git commit`，**不 push**。这样：

- 每个 sub 阶段（test / dev / verify / gate / fix / final-gate）的代码改动立即落到 feature branch 的 git 历史里，便于阶段回滚 / `git diff` 对照。
- LLM 不必记得每次手动 commit。

**行为细节**：

1. **触发时机**：`cmd_record` 处理每个 status 分支（`completed` / `failed` / `escalate` / `pass` / `fail`）后、返回 JSON 之前注入 commit 结果。**`workflow_failed`** 路径同样会注入。
2. **commit message**：固定格式 `chore(<change>): auto-record <item>:<sub> <status>`，例：
   - `chore(my-change): auto-record backend:test completed`
   - `chore(my-change): auto-record frontend:gate pass`
   - `chore(my-change): auto-record final-gate:gate pass`
3. **跳过条件**：若 `git status --porcelain` 为空（工作区干净），跳过提交并把 `reason` 写进 record 返回的 `commit.reason`，不会产生空 commit。
4. **粒度**：`git add -A` 全量提交，跟随 runner 当前 LLM 累积的所有改动（不仅是上一个 sub 阶段的产出）。
5. **冲突**：自动 commit 与 final-gate pass 路径里的 `_git_commit_archive`（提交 `archive change <target-name>`）并存。**顺序：先 `save_state(completed=True)` 把最终 state 落盘到 change 目录，再 archive + 提交**——archive 移动整个目录时一次性带走 `.pipeline-state.json`，避免 archive 之后 `save_state` 在原路径重建目录、`_auto_commit_on_record` 误回写孤立 state 文件。
6. **与 init commit 的关系**：init commit（见上一节「启动时的初始化提交」）在 runner 启动早期发生一次，与 record commit 互不重叠——init commit 落"pg-build 启动基线"，record commit 落"sub 阶段代码改动"，archive commit 落"归档目录移动"。三条 commit 的 message 前缀分别为 `chore(<change>): bootstrap pg-build` / `chore(<change>): auto-record <item>:<sub> <status>` / `archive change <target-name>`，git log 里可清晰区分。
7. **不 push**：推送仍由 `pg-verify-and-merge` 的 Phase 3 统一完成。

**record 返回 JSON 新增 `commit` 字段**：

```json
{
  "action": "dispatch",
  "item": "backend",
  "sub": "test",
  ...,
  "commit": {
    "attempted": true,
    "committed": true,
    "branch": "feat/pg/my-change",
    "sha": "abc1234",
    "message": "chore(my-change): auto-record backend:test completed",
    "reason": null
  }
}
```

跳过场景：

```json
{
  "commit": {
    "attempted": true,
    "committed": false,
    "branch": "feat/pg/my-change",
    "reason": "工作区干净，无可提交内容"
  }
}
```

LLM 在收到 record 返回时，可在终端简报里展示 `commit.committed` 和 `commit.sha`，方便用户跟踪历史。

### 完成时的自动归档

runner 在返回 `done`（即 final-gate pass）之前，会自动完成以下清理动作：

1. 调用共享脚本 `.opencode/skills/pg-archive/scripts/pg-archive.py move <change>`，把 `.pg/changes/<change>/` 移到 `.pg/changes/archive/YYYY-MM-DD-<change>/`（同名冲突走 `.N` 后缀）。
2. 在当前 feature branch (`feat/pg/<change>`) 上创建 `archive change <target-name>` commit（`git rm --cached` 旧路径 + `git add` 新路径 + commit）。

**关键约束**：
- 自动归档失败**不会阻塞 done 返回**——runner 仍然返回 `{"action": "done", "status": "completed"}`，但 `archive.failed = true` 会在 JSON 中暴露。LLM/manager 应检查 `archive` 字段，决定是否手动调用 `pg-archive` SKILL 补做。
- 自动 commit **不 push**——推送由 `pg-verify-and-merge` 在 Phase 3 完成。
- 自动 commit 落在 feature branch 上，由 `pg-verify-and-merge` 的 merge 流程带入 master。

done 返回的 archive 字段结构：

```json
{
  "action": "done",
  "status": "completed",
  "archive": {
    "ok": true,
    "target_name": "2026-06-15-my-change",
    "src": ".pg/changes/my-change",
    "target": ".pg/changes/archive/2026-06-15-my-change",
    "commit": {
      "attempted": true,
      "committed": true,
      "branch": "feat/pg/my-change",
      "sha": "abc1234",
      "message": "archive change 2026-06-15-my-change"
    }
  }
}
```

---

## 脚本命令参照

LLM 只需与 `pg-pipeline-runner.py` 交互，不要直接调用其他脚本：

```bash
RUNNER="python3 .opencode/skills/pg-build/scripts/pg-pipeline-runner.py"
```

| 命令 | 用法 | 说明 |
|------|------|------|
| `next` | `$RUNNER next <change>` | **主循环入口**。返回 dispatch action 或终态。首次调用会自动完成初始化（含 feature branch 创建、state 初始化、init commit） |
| `record` | `$RUNNER record <change> <status>` | 记录子 agent 执行结果，驱动状态机前进 |
| `check` | `$RUNNER check <change> <item>` | **调试用**。检查某 track 的任务完成状态。`item` 支持两种格式：`agent`（查全部 sub）或 `agent:verify`（查单个 sub） |
| `progress` | `$RUNNER progress <change>` | **调试用**。显示所有 track 的整体完成进度 |

不存在 `init`、`start` 等其他命令。首次调用 `next` 自动完成初始化。

---

## 派送 sub-agent

### 核心机制：runner 写文件，sub-agent 自己读

`next` 返回的 **`dispatch_file`** 字段是 sub-agent 完整任务指令的**文件路径**——runner 在写文件前已完成：

1. Jinja 模板渲染（`{{context.*}}` 替换）
2. `build_rules` 的 prepend/append 合并（由 runner 内部 `_merge_prompt_injection` 完成）
3. 全局 seq 编号分配与文件落盘
4. `manifest.yaml` 追加

**orchestrator 唯一要做的事**：把 `dispatch_file` 路径告知 sub-agent（用 Task tool 派送），让 sub-agent 自己读文件。

**绝对禁止**：
- ❌ 修改 runner 返回的 `dispatch_file` 路径
- ❌ 试图自己重新组织 prompt（runner 已写好）
- ❌ 读 dispatch_file 内容后改写再传给 sub-agent
- ❌ 直接复制 dispatch_file 内容作为 Task tool 的 prompt

> 设计动机：LLM orchestrator 在派送 sub-agent 时有强烈"重写/总结"本能（哪怕字段名叫 `prompt_final_no_modify` 也会被改）。把指令完全 bypass 到文件系统是**架构层面**的根治——orchestrator 根本不接触指令内容，也就不可能改。

### 派送代码示例

```python
# 从 dispatch action 取出文件路径
dispatch_file = action["dispatch_file"]

# 把路径作为 Task tool 的 prompt 传入——不传任何改写过的内容
task_prompt = (
    f"你的完整任务指令已由 runner 写入文件 {dispatch_file}。\n"
    f"**第一步**：用 Read 工具读取该文件，逐字执行其中所有内容。\n"
    f"**禁止**：改写、摘要或重组文件中的指令。"
)

# 派送 sub-agent
Task(
    subagent_type=action["agent"],  # e.g. "pg-build/dev"
    description=f"Execute {action['item']}:{action['sub']}",
    prompt=task_prompt,
)
```

### 关键字段

| 字段 | 说明 |
|------|------|
| `dispatch_file` | 完整任务指令文件路径（含已合并的 `build_rules` 内容），由 sub-agent 读取 |
| `dispatch_seq` | 本次派遣的全局 seq 编号（3 位 0 填充，如 `005`） |
| `report_seq` | 预分配给 sub-agent 报告的 seq 编号（`dispatch_seq + 1`），sub-agent 写报告时**必须**使用此值 |
| `next_call_timeout_seconds` | bash 超时值（仅简单 track 返回） |

### 步骤 2：dispatch 并 record

| 场景 | 动作 |
|------|------|
| dispatch / dispatch_fix / dispatch_final_gate | 读 `action["dispatch_file"]` → 写一句"读文件 {dispatch_file} 逐字执行"作为 Task tool 的 prompt → 派送。**不要读 dispatch_file 内容、不要改写**。完成后 `$RUNNER record ...` |
| done | 输出摘要报告 → 加载 pg-verify-and-merge SKILL 继续执行（自动合并验证） |
| workflow_failed | 输出失败报告 → break |
| phase_result | 缓存环境信息；如 `terminate==true` 则输出失败报告 → break；否则 `$RUNNER record completed` |
| **error** (新) | 输出 `reason` + `fix_hint` 给用户，询问如何处理（手动修复 state / 跳过 / 终止）；不自动重试 |

> record 命令调用 `$RUNNER record ...` 时，bash tool timeout 必须使用最近一次 `next` 或 `record` 响应中的 `next_call_timeout_seconds` 值，防止长时间运行的 phase 脚本被默认 120s 超时提前杀死。

| 场景 | record 命令 |
|------|------------|
| test/dev 成功 | `$RUNNER record <change> completed "" "<summary>" "<outputs (逗号分隔)>" ""` |
| test/dev 失败 | `$RUNNER record <change> failed "" "" "" "<issues>"` |
| verify PROCEED | `$RUNNER record <change> completed "" "<summary>"` |
| verify ESCALATE | `$RUNNER record <change> escalate` |
| gate PASS | `$RUNNER record <change> pass` |
| gate FAIL | `$RUNNER record <change> fail` |
| fix 完成 | `$RUNNER record <change> completed "" "<summary>"` |

注意：summary/outputs/issues 字段用双引号包裹，换行符用空格替代，确保作为单个 CLI 参数传递。

#### record status 与 sub 强制对应表（runner 入口会拒绝越界调用）

**从 v3.4 起**，runner 在 `cmd_record` 入口加了 sub-status 语义守卫：LLM 用错 record status（比如 verify 完成后调 `record pass`）会**立即**返回 `action: error, fatal: false`，并附带 `reason` + `fix_hint`，**不会污染 state**。

| 当前 sub | 允许的 record status | 拒绝调用的 status |
|----------|---------------------|-------------------|
| test     | completed, failed   | pass, fail, escalate |
| dev      | completed, failed   | pass, fail, escalate |
| verify   | completed, escalate, failed | pass, fail |
| fix      | completed, failed   | pass, fail, escalate |
| fix-gate | completed, failed   | pass, fail, escalate |
| gate     | pass, fail          | completed, failed, escalate |
| simple   | completed, failed   | pass, fail, escalate |

**常见错误**（regression: 2026-06-29 `fix-upgrade-download-url-libvirt-missing` 教训）：

- ❌ verify 报告 PROCEED → `record pass`（会让 runner 把 tasks.md §4 gate 误勾为完成，导致 §3 verify 永远不被 mark → 无限循环派遣 verify）
- ✅ verify 报告 PROCEED → `record completed`
- ❌ gate 报告通过 → `record completed`
- ✅ gate 报告通过 → `record pass`
- ❌ verify 报告需要 fix → `record failed`
- ✅ verify 报告需要 fix → `record escalate`

#### state ↔ tasks.md 一致性守卫（v3.4+）

runner 在 `cmd_record` 和 `cmd_next` 入口都会校验 **state vs tasks.md 一致性**。如果检测到漂移（如 state 说 sub=verify 但 tasks.md §4 gate 才是第一个未完成 section），返回 `action: error` + `drift_kind`：

| drift_kind | 含义 | 推荐处理 |
|------------|------|---------|
| `sub_drift` | state["current"]["sub"] 与 tasks.md 第一个未完成 section 不一致 | 检查是否上一步用错 record 命令；用 `pg-pipeline-state.py rollback <track>` 回滚错误勾选 |
| `track_in_completed_but_section_open` | state 标记 track 完成但 tasks.md 仍有未勾 section | 同上，回滚后重跑 next |
| `all_sections_marked_but_track_not_completed` | tasks.md 全勾但 state 未标 track 完成 | 手动 `pg-pipeline-state.py mark <track>` 补登 |

LLM 主循环看到 `error` action 后应：
1. 打印 `reason` 和 `fix_hint` 给用户
2. **不**自动重试
3. 询问用户处理方式（手动修复 / 删除 `.pipeline-state.json` 从头开始 / 终止流程）

---

## 模板语法参考（runner 端 _render_prompt_template 支持）

> 以下语法仅供开发调试参考。orchestrator 不需要手动使用；runner 在写 dispatch_file 前已完成渲染与 `build_rules` 合并。

- `{{var}}` / `{{context.field.sub}}` — 取值；`context.` 前缀会回退到 ctx 顶层 key
- `{{var \| filter(arg=N)}}` — 过滤器（`tojson(indent=N)` / `toyaml` 支持；新模板默认用 `toyaml` 压缩 prompt 篇幅）
- `{#if cond}...{/if}`` — 条件块（cond 支持 `var in [...]` / `this.X` / 真值）
- `{#each list}...{/each}` — 循环块（循环体内 `this` 绑定当前项）
- 缺失字段渲染为空字符串（不暴露模板占位符）

runner 端常量 `_PROMPT_TEMPLATE_BASE` + 6 个 `_PROMPT_BLOCK_*` 常量定义了各 sub 类型的具体模板。SKILL.md 展示的模板块（如 `## 任务：{{context.id}} - {{context.label}}`）是这些常量的文档副本，实际渲染以 runner 端代码为准。

### Agent 报告类型对照（统一时序编号）

所有 sub-agent 产出的报告文件遵循**全局递增 3 位 seq 编号**（`001`, `002`, ...），与 `dispatch_file` 共享同一 seq 空间。`{seq}` 字段由 runner 预分配（`action["report_seq"]`），sub-agent 写报告时**必须**使用。

| sub | 必读报告 | 报告落盘路径 | 必填字段 / 必跑流程补充 |
|-----|---------|-------------|----------------------|
| `test` | tasks.md 中 test 章节 | 无（仅更新 tasks.md 复选框） | TDD 红 Phase：只写测试代码，不创建或修改生产代码。预期首次编译失败。 |
| `dev` | design.md / tasks.md dev 章节 | 修改源码 + 更新 tasks.md 复选框 | 调用 `pg-invoke-hook.py invoke-hook` 触发 start/stop/logs/tail；改完跑 `stage.test_commands` 验证 |
| `verify` | tasks.md verify 章节 | `2-build/{report_seq}-{item}-verify.md` | 按需 `pg-invoke-hook.py invoke-hook` 启停服务；按 tasks.md V-* 清单逐项 curl/检查 |
| `gate` | 上一轮 verify 报告 | `2-build/{report_seq}-{item}-gate-verify.md` | **只读不写**源码；`cat >` 自行写盘 |
| `fix-gate` | 最近 gate report（G-N 章节） | `2-build/{report_seq}-{item}-fix-gate-verify-{cycle}.md` | 仅用 `pg-invoke-hook.py invoke-hook --action start\|stop` |
| `fix` | verify 报告（ESCALATE Issue 详情） | `2-build/{report_seq}-{item}-fix-verify-{cycle}.md` | **继承 base dispatch 全集**（见「fix agent 的 prompt 构建」章节，必填 9 项） |
| `simple` | 自身执行 | `2-build/{report_seq}-{item}-simple-verify.md` | 简单 track 不写 `tasks.md` 复选框 |
| `final-gate` | 各 track gate assessment + design.md（🆕 标记） + context-chain.md | `2-build/{report_seq}-final-gate-gate-verify.md` | 独立文件（item=final-gate, kind=gate-verify） |

**所有 agent 类型**：如果 `context.rollback_context` 存在，在 prompt 末尾追加：

```
[ROLLBACK CONTEXT]
- failed_at: {{context.rollback_context.failed_at}}
- reason: {{context.rollback_context.reason}}
- source: {{context.rollback_context.source}}

你必须优先审查该根因是否已修复，再执行本阶段的正常任务。
```

---

## Deployment 工具调用约定

新 schema 中**没有** `track.rebuild_and_restart` 字段——deployment 启停的执行由 **LLM 主导**。LLM 通过 `pg-invoke-hook.py invoke-hook` CLI 触发 role action，runtime 层独立 CLI 内部负责从 project.yaml 反查 spec 并调用 pg-run-hook.py。

### pg-invoke-hook.py CLI 形式

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --session <S> --env <ENV> --role <ROLE> --instance <INSTANCE> --action <ACTION> \
  [--stage <ST>] [--tail-lines <N>] [--skill pg-build]
```

> v4 协议：`--change` 改为 `--session`（canonical），`--skill` 硬缺省 `ad-hoc`，SKILL 调用必须显式标注 `--skill pg-build`。
> 历史兼容: `pg-pipeline-runner.py invoke-hook` 仍可用（thin wrapper 转发到 `pg-invoke-hook.py`），新代码统一写新路径。

| 标志 | 必填 | 说明 |
|------|------|------|
| `--session` | ✅ | session 名（v4）。pg-build 调用 = 提案目录名。`--change` 保留 1 版本作为 deprecated alias |
| `--env` | ✅ | 必须在 project.yaml `environments` 列表中 |
| `--role` | ⚠️ | backend / frontend / agent。**仅 `--action start\|stop\|logs\|tail` 必填**；`--action prepare_env\|clean_env` 时忽略 |
| `--instance` | ⚠️ | 必须在 `environments.<env>.roles.<role>.instances[]` 中。**仅 `--action start\|stop\|logs\|tail` 必填**；`--action prepare_env\|clean_env` 时忽略 |
| `--action` | ✅ | `start` / `stop` / `logs` / `tail`（per-role） 或 `prepare_env` / `clean_env`（environment-level） |
| `--stage` | ❌ | 默认 `manual`；用于 spec.stage 标记 |
| `--tail-lines` | ❌ | 仅 `--action logs\|tail` 生效；runner 把它作为 hook args 末尾追加 |
| `--skill` / `--caller` | ❌ | **硬缺省 `ad-hoc`**。SKILL 调用必须显式标注（pg-build → `--skill pg-build`）。注入为 `PG_RUN_CALLER` 环境变量 |

**`--action prepare_env` / `--action clean_env`**（v3.0 新增）：environment-level lifecycle hooks，
定义在 `environments.<env>.prepare_env` / `clean_env`（不在 roles 之下）。调用时**不传** `--role` / `--instance`。
典型用途：pg-fix-issue 在 Phase 3 用户选"是 prepare" 后由编排器主动触发。

**`--timeout` / `--host` / `--port` 均不是 CLI flag**——LLM 不传，由 runner 从 project.yaml 自动反查。

### 何时该启停服务

| 触发时机 | 动作 |
|---------|------|
| dev/verify/fix 需要启动某 role 服务 | `pg-invoke-hook.py invoke-hook --session X --env Y --role backend --instance backend-1 --action start --skill pg-build` |
| 看日志 | `pg-invoke-hook.py invoke-hook --session X --env Y --role backend --instance backend-1 --action logs --tail-lines 100 --skill pg-build` |
| 整个 change 跑完（可选） | 依次 `pg-invoke-hook.py invoke-hook ... --action stop --skill pg-build` 收尾 |

> 启动与否由 LLM 自行判断，runner 不替你启停。

### runner 提供的保证

- `stage.environment.required=false`（如 `dev-isolated`）：`environment.hooks` 为 `null`，LLM 不应试图启动服务
- `tasks.md ## Deployments` 中某 track 标记为 `skip`：该 track 不被派遣，自然也不会有 `environment.hooks`
- `tasks.md ## Deployments` 中 `real-integration: dev-3tier`：runner 会用 `dev-3tier` 替换 `config.yaml` 默认 environment；`hooks.action_metadata` 自动从对应环境的 project.yaml 反查
- `prepare_env`/`clean_env` 由 runner 自动执行，LLM 通过 `phase_result` action 接收执行结果和完整环境配置。成功后 LLM 必须调用 `record completed` 推进流程；失败时 `terminate=true` 表示流程终止。

### agent prompt 中的样例

dev agent 收到的 `environment.hooks` 形如：

```json
{
  "supported_actions": ["logs", "start", "stop", "tail"],
  "action_metadata": {
    "backend": {
      "start":  {"timeout_seconds": 300, "description": "start 脚本完成了 停止已有实例、构建模块、部署实例、启动实例的 4 个步骤。"},
      "stop":   {"timeout_seconds": 30},
      "logs":   {"timeout_seconds": 30},
      "tail":   {"timeout_seconds": null}
    },
    "frontend": {"start": {"timeout_seconds": 60}, "stop": {"timeout_seconds": 30}, "logs": {"timeout_seconds": 30}, "tail": {"timeout_seconds": null}},
    "agent":    {"start": {"timeout_seconds": 60}, "stop": {"timeout_seconds": 30}, "logs": {"timeout_seconds": 30}, "tail": {"timeout_seconds": null}}
  },
  "invocation": {
    "command_template": "python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook --session <SESSION> --env <ENV> --role <ROLE> --instance <INSTANCE> --action <ACTION> [--stage <STAGE>] [--tail-lines <N>] [--skill <SKILL>]",
    "required_args": ["--session", "--env", "--role", "--instance", "--action"],
    "optional_args": ["--stage", "--tail-lines", "--skill"],
    "notes": [
      "timeout_seconds is INFORMATION (read from project.yaml via action_metadata). LLM does NOT pass it.",
      "--tail-lines only applies to --action logs|tail; runner appends it to the hook's args list as the last two elements.",
      "host / port are NOT CLI flags; runner resolves them from instances[] in project.yaml by instance name."
    ]
  }
}
```

LLM **不**再看到 pre-rendered cmd 字典；调用 runner 时只关心 invoke-hook CLI 形式。
```

---

## fix agent 的 prompt 构建

fix agent **完全继承 base dispatch 模板（步骤 2）的所有字段**：变更名称 / Track 配置 / Module 配置 / Stage 配置 / Hooks 调用约定 + 实例拓扑 / 产物路径 / 模块路径约束 / 验证要求。在此基础上，**追加**「必读源报告」指引。

> ⚠️ **fix agent prompt 必填字段清单**（缺一不可，缺项任务无法完成）：
> 1. 变更名称 + Track 配置（id / review_level / modules / max_fix_retries / fix_routing）
> 2. Module 配置（root / language / build / lint / test.unit / test.integration）
> 3. Stage 配置（name / test_key / gate / test_commands / environment.name / environment.instances / environment.hooks）
> 4. **Hooks 调用约定**（runner invoke-hook CLI 形式 + action_metadata；启动/看日志/收尾的 CLI 命令）
> 5. 产物路径（proposal / design / tasks）
> 6. **模块路径约束**（硬规则）
> 7. **必读源报告路径**：`context.verify_report_path`（runner 已注入，fix agent 用 Read 工具自行读源 verify 报告全文）
> 8. **写盘要求**：修复记录写到 `2-build/{track.id}-(N+1)-verify-fix.md`（用 `cat >` 自行写盘）

**「必读源报告」块**（runner 在 dispatch_file 中注入 `context.verify_report_path`）：

```
### 必读源报告（verify ESCALATE 派发）

- **源 verify 报告**: `{{context.verify_report_path}}`

请用 Read 工具**逐字**读取该文件。报告包含 verify agent 记录的
ESCALATE Issue 详情、失败证据（HTTP 响应 / 日志片段 / stack trace）、
V-* 验证项的逐项结果等**完整上下文**。runner **不**对报告做结构化抽取，
所有修复决策必须基于报告原文。
```

> **设计原则**：编排器对 verification report **不做任何加工**，只传递
> `context.verify_report_path` 路径给 fix agent。fix agent 用 Read 工具
> 自行读源报告原文，避免 runner 的正则解析丢失失败证据、日志片段等
> 关键上下文。

**fix-gate agent 同理**：dispatch_file 注入 `context.gate_report_path`，
fix-gate agent 用 Read 工具读源 gate 报告全文（可能含多个 `### {track}:G-N`
章节，agent 需通读整份报告识别**全部**未修复的 gap 一次性修复）。

**修复后必跑流程**（fix agent 必须自检通过才能返回 SUCCESS）：

1. 修改源码
2. 跑 `stage.test_commands[0]` 单元测试（必须通过）
3. 跑 `modules.<module>.lint`（必须 0 警告）
4. 启动 `role.<role>.start@<instance>` 服务（如需）
5. 跑 tasks.md verify 章节的所有 V-* 验证项（curl 等）
6. 抓 `role.<role>.logs@<instance>` 日志确认无 ERROR
7. 停止 `role.<role>.stop@<instance>` 服务（如启动过）
8. 用 `cat > 2-build/{track.id}-(N+1)-verify-fix.md << 'EOF' ... EOF` 自行写盘

返回格式同 base dispatch（summary / outputs / tasks_updated / status）。

## final-gate 的 prompt 构建

final-gate 使用 gate agent，但传入跨 track 的合集上下文：

```
## 任务：Final Gate — 跨 track 依赖审查

### 变更名称
{context.change-name}

### Track 配置
- track.id: final（特殊标记，runner 内部 marker，不在 config.yaml 中）
- track.review_level: standard

### 产物路径
- proposal: {context.proposal_path}
- tasks: {{context.tasks_path}}
- design_doc_path（首个）: {{context.design_doc_path}}
- design_doc_paths: {{context.design_doc_paths}}
- report_paths: {context.report_paths}

### 必读上下文清单

final-gate agent 必须读取以下 4 类文件才能做完整审计：

1. **所有 design.md**（`context.design_doc_paths`）—— 找 🆕 标记的跨 track 验证项
2. **所有 track 的 gate assessment 报告**（`context.report_paths`）—— 路径模式 `2-build/{track.id}-{N}-gate-assessment.md`
3. **context-chain.md**（`.pg/changes/<change>/2-build/context-chain.md`）—— 了解 sub-agent 执行历史与已知问题
4. **2-build/known-issues.md**（如存在）—— 累积的 gate-fix 兜底问题

### 执行要求

**🆕 标记语义**：design.md 中以 `🆕` 开头的验证项表示**跨 track 依赖**（如「V-backend-1 → frontend 必须能用」）。每个 🆕 项必须找到至少一个其他 track 的 gate-assessment.md 证明已实现。

**审计步骤**：

1. 遍历所有 `context.design_doc_paths`，提取所有 🆕 标记的跨 track 验证项
2. 对每条 🆕 项，确认目标 track 的 `gate-assessment.md` 里有对应实现证据
3. 检查所有 `context.report_paths` 都是 PASS 状态
4. 检查 `context-chain.md` 没有未解决的 error
5. 列出跨 track 不一致 / 缺失项（如有）

**写盘要求（必须）**：完成所有审计后，用 `cat > .pg/changes/<change-name>/2-build/final-gate-assessment.md << 'EOF' ... EOF` 自行写盘。**不要**把 markdown 全文塞进返回里——编排器不会替你落盘。

### 返回格式

- summary: 一句话总结整体判定（PASS / FAIL）
- **不要**返回 markdown 全文（已落盘到 `final-gate-assessment.md`）
```

---

## Simple Track 派遣契约

`tracks.<id>.type == "simple"` 的 track 不走 TDVG 四阶段，而是被 runner **派遣给 `pg-build/simple` sub-agent** 执行。这样可以利用 LLM 能力做错误自动修复（缺依赖等）。

### 触发条件

- config.yaml 中 `tracks.<id>.type: simple`
- runner 检测到后：
  1. `_noopify_simple_track_sections(change)` 幂等地把 tasks.md 对应章节改写为 canonical noop form（heading 注释追加 `(simple track: 派遣 pg-build/simple agent 执行 commands)` + body 单行 `- 无`）
  2. `cmd_next` 在 `is_simple_track` 分支调用 `_build_simple_dispatch` 返回 `action=dispatch, agent=pg-build/simple, sub=simple`
  3. LLM 主循环收到 dispatch 后用 Task tool 派遣 `pg-build/simple` agent

### ctx 注入

runner 通过 `_build_simple_context` 构造 ctx，包含：

- `_change`, `id`, `label` — 基础标识
- `track_type`, `track_timeout`, `track_on_failure` — simple track 配置
- `commands_normalized` — 命令 SSOT，每条含 `idx / cmd / timeout_seconds / on_failure / retry_max / retry_timeout_seconds / is_retry / is_continue / is_fail`
- `next_report_n` — agent 写盘报告的序号
- `stage` — 仅 simple track 关联 environment 时填充

模板用 `_PROMPT_TEMPLATE_BASE` + `_PROMPT_BLOCK_SIMPLE`（位于 `pg-pipeline-runner.py`），与 dev/verify 风格保持一致。

### 返回契约

| agent 返回 status | runner 行为 |
|---|---|
| SUCCESS | 标记 simple track 为 completed，推进 pipeline |
| FAILED + track.on_failure=continue_all | warning 继续推进 pipeline |
| FAILED + track.on_failure=fail | workflow_failed，终止 pipeline |

`track.on_failure=continue_all` 由 runner 在 record 阶段判定（不在 agent 内部决策），与原 runner 自执行模式保持一致。

### 报告落盘

simple agent 必须用 `cat > 2-build/{track.id}-{N}-simple.md <<'EOF' ... EOF` 自行写盘（N 由 runner 在 ctx.next_report_n 注入）。报告内容：每条 command 的 cmd / 退出码 / stdout 末尾 ~50 行 / stderr 末尾 ~50 行 / 耗时 / 最终判定。

### next_call_timeout_seconds

`sum(cmd.timeout_seconds) + N*30` 余量（N = 命令数）。runner 在 dispatch 返回中挂载此值，LLM 主循环用作下一次 bash 调用的 timeout。

---

## 重试与恢复

- **编译/构建失败**: 检查错误并修复后重新执行 `next`（runner 继续当前子阶段）
- **子 agent 返回空**: 重新执行 `record failed` 触发重试（重试次数取 config.yaml 对应 track 的 max_fail_retries）。**编排器不得自行执行该 agent 的工作**
- **会话中断**: 重新运行 `next`，runner 从断点恢复
- **测试框架不稳定**: 如确认是 flaky test，可先手动 `mark` 跳过，再重新 `next`
- **`workflow_failed` 为终态，禁止重试**: 当 runner 返回 `{"action": "workflow_failed", "fatal": true, ...}` 时，状态机已进入不可恢复的失败状态。LLM **必须**立即输出失败报告并结束流程，不得调用 `next` 或 `record` 尝试恢复。如果要重试，需要人工介入：先修复根因，再手动删除 `2-build/.pipeline-state.json`，然后重新运行 `next`（相当于从头开始）

### Gate-Fix 循环

Gate FAIL 自动触发 gate-fix 循环。报告流转遵循[报告体系](#报告体系)章节的序号式命名（所有文件位于 `2-build/` 子目录）:

1. runner 解析 `2-build/{track.id}-{N}-gate-assessment.md` 的 `### {track.id}:G-N` 章节
2. 根据 `**关联 task**` 字段**局部回退** tasks.md（兜底为整 track 回退）
3. 派 `pg-build/fix-gate` agent 修复，输出 `2-build/{track.id}-(N+1)-gate-fix.md`
4. 完成后 re-dispatch verify，输出 `2-build/{track.id}-(N+2)-verify.md`
5. verify PROCEED 后 re-dispatch gate，输出 `2-build/{track.id}-(N+3)-gate-assessment.md`
6. 按 max_gate_fix_retries 轮，耗尽后:
   - 把未修复的 gap 追加到 `2-build/known-issues.md`
   - 继续推进后续 track / final-gate，不阻塞

**典型循环示例**（仅 gate-fix）：

```
backend-1-verify.md
backend-2-gate-assessment.md  (FAIL)
backend-3-gate-fix.md         (修复)
backend-4-verify.md           (re-verify, PROCEED)
backend-5-gate-assessment.md  (PASS)
```

---

## 脚本内部行为（LLM 不关心）

Runner 自动处理以下逻辑，LLM **不应**绕过脚本自行操作：

- `detect`, `mark`, `rollback`, `gate-rollback` — 通过 `pg-pipeline-state.py` 内部管理（LLM 不需要直接调用）
- `pg_context_chain.py` — 执行历史记录（runner 直接 import）
- 重试计数（取 config.yaml track 级配置：max_fail_retries / max_fix_retries / max_gate_fix_retries）
- Gate FAIL 后的局部 rollback + rollback context 写入
- Gate-fix 循环耗尽后写入 known-issues.md，继续推进
- Phase 命令自执行
