---
name: pg-build-v2
description: 基于 Event Sourcing + Reducer 模式的 pipeline 编排引擎。替代 pg-build（旧 v1/v2 双轨架构）。
license: MIT
compatibility: 需要 .pg/project.yaml / execution-manifest.yaml 驱动编排。
metadata:
  author: pg-spec
  version: "1.0"
---

# pg-build-v2

端到端实现变更的 pipeline 编排引擎。**事件溯源**（Event Sourcing）+ **纯函数 Reducer** 取代旧架构的过程式状态机 + 散落 save_state。

> **v2.1.1 重要变更**：env hook（`prepare_env` / `clean_env`）不再在 runner 进程内同步执行。
> runner 只返回 plan，**编排器自己 bash 执行**（bash timeout = `plan.timeout_seconds`），
> 然后调 `env-action-result` 上报结果。这避免了 prepare_env 内部 timeout > LLM bash timeout
> 导致中断的问题，并支持多 stage / 多 env 切换。

> **v2.2 重要变更（BREAKING）**：
> 1. `record` CLI 改为 **argparse 仅 flag 模式**，旧位置参数调用已废弃（报错提示）。
> 2. **fix cycle 默认跳过 verify cycle=2**：fix 完成后直接 dispatch gate（`direct_to_gate`）。
>    可通过 `tracks.<id>.fix_routing: re_verify` 回退到旧行为。
> 3. escalate 时**强制 `tasks_updated`**，必须包含失败 V-* 的 task_id。
> 4. **dev/test/fix 阶段强制 `--outputs`**，关闭空产物声明漏洞。
> 5. **fix 阶段强制 `--report`**，必须写盘追溯文件。
> 6. dispatch 文件命名规范：`{seq}-{track}-fix-{cycle}.md`（去掉冗余 phase 字段）。
> 7. fix agent 只跑**失败的 V-* + 核心冒烟**，不再全量重跑验证。

---

## 架构概览

```
event_log (append-only JSONL)  ← 唯一持久化入口
    │
    ▼
reduce_state(pure function)    ← 状态转换（无 I/O）
    │
    ▼
PipelineAction                  ← 下一步动作（dispatch / advance / done / failed / env_switch）
    │
    ▼
orchestrator                   ← next() / record() / progress()
    │
    ▼
sub-agent (via Task tool)      ← 执行 test / dev / verify / gate / fix / fix-gate
```

**核心差异**（对比旧 pg-build）：

| 特性 | pg-build（旧） | pg-build-v2（新） |
|------|---------------|------------------|
| 状态管理 | mutable dict + 51 处 save_state | 不可变 frozen dataclass + reducer |
| 持久化 | `.pipeline-state.json` 覆盖写入 | `pipeline.events` append-only JSONL |
| 代码量 | ~7800 LOC（v1+v2 双轨） | ~3000 LOC（单轨） |
| 模板 | 嵌入 .py 字符串（484 行） | 独立 YAML 文件 |
| 子循环 | `in_fix_cycle` 状态 flag | SubPipeline 递归复用 reducer |
| 查看器 | 无接口 | event log 天然 SSE/WebSocket 源 |
| env hook 执行 | runner 同步执行（timeout 受 runner 限制） | **编排器 bash 执行**（timeout 由 LLM bash 决定） |
| 多 stage env 切换 | 单 stage 假设 | detect.py 自动检测 stage 边界 |

---

## CLI 用法

```bash
RUNNER="python3 .opencode/skills/pg-build-v2/scripts/pg-pipeline-runner.py"

# bootstrap 副作用（首次）：migrate / branch / init-commit
# v2.1.1: 不再同步执行 prepare_env，env hook 拆到首次 next() 返回的 env_switch
$RUNNER bootstrap <change>

# 获取下一步 action
$RUNNER next <change>

# 记录 sub-agent 结果（v2.2: argparse 仅 flag 模式，旧位置参数已废弃）
$RUNNER record <change> <status> --report <path> --summary "<摘要>" [--outputs <p1>,<p2>] [--issues <i1>,<i2>] [--evidence <e1> [--evidence <e2> ...]] [--tasks-updated <t1> [--tasks-updated <t2> ...]]

# 查看进度
$RUNNER progress <change>

# 返回 env hook 执行 plan（不执行），由编排器自行 bash 执行
$RUNNER env-action <change> <phase> <stage> <env> [hook_timeout_seconds]

# env hook 执行完后由编排器调用：写 *_COMPLETED event + 更新 stage_prepared/current_stage
$RUNNER env-action-result <change> <phase> <stage> <env> <ok> [log_path] [exit_code] [started_ts] [error]
```

**status**: `completed | failed | escalate | pass | fail`

**env-action phase**: `prepare_env | clean_env`

**env-action-result ok**: `ok | failed`

---

## 编排器执行协议

### 主循环

编排器（调用 SKILL 的 LLM）通过调用 runner CLI 实现 pipeline 推进：

```
循环:
  0. [首次] $RUNNER bootstrap <change> (10s timeout)
     - 若返回的 env_hook_plan 非空：编排器 bash 执行 plan.command
       （bash timeout = plan.timeout_seconds），完成后调 env-action-result
       — 同步骤 1 的 env_switch 协议。

  1. 调 `next <change>` (10s timeout) → 检查 action 字段
  2. switch(action):
       "env_switch"        → 编排器调 $RUNNER env-action <change> <phase> <stage> <env_name> [hook_timeout]
                             (timeout=10s, 只返回 plan)
                             ↓
                             编排器 bash 执行 plan.command
                             (bash timeout = plan.timeout_seconds)
                             ↓
                             编排器调 $RUNNER env-action-result <change> <phase> <stage> <env_name> ok <log_path> <exit_code> <started_ts>
                             失败 → 编排器调 $RUNNER env-action-result ... failed <log_path> <exit_code> <started_ts> "<error message>"
                             失败 → 编排器终止循环，提示用户修复环境
                             成功 → 回步骤 1

       "dispatch"          → 派遣 sub-agent (见下方协议)
       "dispatch_final_gate" → 派遣 pg-build/gate agent
       "advance"           → 回步骤 1
       "done"              → 触发 verify-and-merge
       "workflow_failed"   → pipeline 失败, 终止
```

**action 字段完整清单**：

| action | 触发时机 | 编排器响应 |
|--------|---------|-----------|
| `env_switch` | stage 边界切换 | env-action → bash → env-action-result → 回 next |
| `dispatch` | sub-agent 派遣 | 派遣对应 agent，完成后 record |
| `dispatch_final_gate` | final-gate 派遣 | 派遣 pg-build/gate agent |
| `advance` | 当前 step 完成 | 自动调 next |
| `done` | pipeline 全部完成 | verify-and-merge |
| `workflow_failed` | 重试耗尽 / fatal | 终止循环 |

### 环境准备（v2.1.1：编排器真正执行 env hook）

`bootstrap` / `env-action` / `env-action-result` 是独立 CLI 命令，**只做解析 / event log / state 更新**，不执行 env hook。

**关键不变量**：

- `state.stage_prepared` 与 `state.current_stage` **只在 `env-action-result` 成功时更新**
- `env-action-result` 失败时**不更新** state，编排器终止循环让用户修
- `state.stage_prepared` 是判断 stage 边界的唯一依据

### Dispatch 协议

runner 返回 dispatch action 带 `dispatch_file` 字段。编排器：
1. **绝不读取 dispatch_file 内容进行加工**
2. **只告诉 sub-agent dispatch 文件路径**
3. 正确用法：`task(prompt="任务指令在 {dispatch_file} 中，请读取并执行，完成后返回 JSON")`

### Sub-agent 返回契约（强制）

所有 sub-agent 必须返回 JSON：

```json
{
  "summary": "<= 200 字字符串",
  "outputs": ["<产物文件绝对路径>", ...],
  "tasks_updated": ["<task_id>", ...],
  "status": "completed | failed | escalate | pass | fail",
  "evidence_paths": ["<证据>", ...],
  "report_path": "<必须存在且可读>"
}
```

### 多 Stage 环境切换

`detect.py:next_pending()` 检测 stage 边界，返回 `env_switch` action：

- `prepare_env` 成功后 `stage_prepared.add(stage_name)`, `current_stage=stage_name`
- `clean_env` 成功后 `stage_prepared.discard(stage_name)`, `current_stage` 不变

**`env_switch` action 包含 `hook_timeout_seconds` 字段**，编排器传入 `env-action` 的 `[hook_timeout_seconds]` 参数。

---

## Record 状态守卫

| sub | 允许 status |
|-----|------------|
| test/dev/simple | completed, failed |
| verify | completed, escalate, failed |
| fix/fix-gate | completed, failed |
| gate | pass, fail |
| final-gate | pass, fail |

---

## Event Schema

所有 event 写入 `{change}/2-build/pipeline.events`，JSONL 格式。

| Event type | 触发时机 | data 关键字段 |
|---|---|---|
| `pipeline_started` | 首次 next | change, pipeline_order |
| `bootstrap_step_completed` | bootstrap 子步 | step, detail |
| `prepare_env_started` | 编排器调 `env-action` 时 | env_name, stage, ts |
| `prepare_env_completed` | 编排器调 `env-action-result` 时 | env_name, stage, exit_code, ok, started_ts |
| `clean_env_started` | 编排器调 `env-action` clean_env 时 | env_name, stage, ts |
| `clean_env_completed` | 编排器调 `env-action-result` clean_env 时 | env_name, stage, exit_code, ok, started_ts |
| `record_received` | LLM 调 record | track, phase, status, summary |
| `fix_cycle_started` | verify escalate | track, cycle |
| `gate_cycle_started` | gate fail | track, cycle |
| `fix_skipped_verify` | v2.2: fix 完成后直接进 gate | track |
| `track_completed` | gate pass / exhausted | track, status |
| `pipeline_completed` | final-gate pass | final_status |
| `workflow_failed` | fatal | reason |

---

## v2.2 新增协议

### record CLI 用法（v2.2）

v2.2 起 `record` 仅支持 argparse flag 模式：

```bash
python3 scripts/pg-pipeline-runner.py record <change> <status> \
    --report <绝对路径> \
    --summary "<=200 字摘要" \
    --outputs <p1>,<p2> \
    --evidence <绝对路径> [--evidence ...] \
    --tasks-updated <task_id> [--tasks-updated ...] \
    --issues <问题描述>
```

**status 与 phase 对照矩阵**（同 §Record 状态守卫表）。

**evidence 规则**（各 phase 要求）：

| phase | evidence 必填 | report 必填 | outputs 必填 |
|-------|:---:|:---:|:---:|
| verify | ✅ | ✅ | ❌ |
| gate/final-gate | ✅ | ✅ | ❌ |
| fix | ❌ | ✅ | ✅ |
| test | ❌ | ❌ | ✅ |
| dev | ❌ | ❌ | ✅ |
| simple | ❌ | ❌ | ❌ |

### tasks_updated 字段

- **定义**：本次 record 完成的 sub-agent 实际修改或影响的 task_id 列表（如 `["2.1", "2.3"]`）
- **escalate 时必填**：`escalate` 必须包含失败的 V-* ID（如 `["V-backend-4", "V-backend-7"]`）
- **其他 phase**：选填，但推荐填写以便追踪

用法：
```bash
$RUNNER record add-user-export escalate --evidence /path/report.md --tasks-updated V-backend-4 --tasks-updated V-backend-7 --summary "..." 
```

### outputs 字段（v2.2 新增强制执行）

- **test/dev/fix 阶段**：`--outputs` 必填，不可为空
- **格式**：逗号分隔的绝对路径，如 `--outputs /project/src/Foo.java,/project/src/Bar.java`
- **目的**：防止 sub-agent 声明"完成"但实际无任何文件改动

### fix_routing 配置

控制 fix cycle 完成后的流向。在 `project.yaml` 中配置：

```yaml
tracks:
  backend:
    modules: [backend]
    fix_routing: direct_to_gate  # 默认值（无需显式配置）
    # fix_routing: re_verify    # 保留旧行为：fix 后 dispatch verify cycle=2
```

| 值 | 行为 | 适用场景 |
|-----|------|---------|
| `direct_to_gate`（默认） | fix 完成后直接 dispatch gate | 信任 fix agent 的修复报告，节省～15min |
| `re_verify` | fix 后 dispatch verify cycle=2 | 高风险变更，需要双重验证 |

### fix dispatch 文件命名规范（v2.2）

```
# 旧命名（v2.1）
{seq}-{track}-{phase}-fix-{cycle}.md  →  006-dev.backend-fix-fix-1.md

# 新命名（v2.2）
{seq}-{track}-fix-{cycle}.md          →  006-dev.backend-fix-1.md
```

旧命名被**废弃**但 `_collect_missing_gate_assessments` 仍兼容识别。

---

## 子 Pipeline 机制（v2.2 更新）

| 循环 | 触发条件 | 流向 |
|------|---------|------|
| **fix 循环** | `verify escalate` | SubPipeline(fix) → 默认 `direct_to_gate`（跳过 verify cycle=2）；显式 `re_verify` 时仍回 verify |
| **gate-fix 循环** | `gate fail` | SubPipeline(fix-gate, verify, gate) → 回到主 pipeline |

---

## 常见错误排查

| 现象 | 原因 | 修复 |
|------|------|------|
| `action: error` + `No active item` | 连续两次 record 未调 next | 每次 record 后调 next |
| `action: error` + `invalid transition` | record status 用错 | 对照 Record 状态守卫表 |
| sub-agent 告"任务不完整" | dispatch 文件没传或路径错 | 检查 `dispatch_file` 路径 |
| `action: error` + `evidence_missing` | verify/gate 的 `evidence_paths` 为空 | 重跑验证，强调必须产出 evidence |
| env hook 卡死循环 | 编排器忘调 `env-action-result` | 每次 bash 跑完 env hook 必调 `env-action-result` |
| 首个 stage 没 prepare 直接 dispatch | `_first_next` 不再预设 prepared（v2.1.1） | 升级后编排器自动收到首个 env_switch |
| `--report /tmp/nonexistent.md` → 报错 | runner 检查 report 文件是否存在 | 报告文件没写盘，或路径写错 |
| `schema_violation: ... 要求 --outputs 非空` | dev/test/fix 阶段没传 `--outputs` | sub-agent 必须返回产物文件列表 |
| `schema_violation: escalate 要求 --evidence 非空` | escalate 没传 `--evidence` | 必须带 verify 报告路径 |
| fix 完成后没有 dispatch verify 而是直接 dispatch gate | v2.2 默认 `direct_to_gate` | 正常行为。如需回退，设 `fix_routing: re_verify` |

---

## 子 Pipeline 机制

- **fix 循环**：`verify escalate` → SubPipeline(fix, verify) → 回到主 pipeline
- **gate-fix 循环**：`gate fail` → SubPipeline(fix-gate, verify, gate) → 回到主 pipeline

---

## V1 兼容脚本

```bash
python3 .opencode/skills/pg-build-v2/scripts/migrations/v1_to_events.py <change_root>
```

从旧 `.pipeline-state.json` 重建 `pipeline.events` + `pipeline.snapshot.json`。