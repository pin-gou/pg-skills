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

端到端实现变更的 pipeline 编排引擎。**事件溯源** + **纯函数 Reducer** 取代旧架构的过程式状态机。

## 架构概览

```
event_log (append-only JSONL)  ← 唯一持久化入口
    │
    ▼
reduce_state(pure function)    ← 状态转换（无 I/O）
    │
    ▼
PipelineAction                 ← dispatch / advance / done / failed / env_switch
    │
    ▼
orchestrator                   ← next() / record() / progress()
    │
    ▼
sub-agent (via Task tool)      ← test / dev / verify / gate / fix / fix-gate
```

所有状态管理（`PipelineState` / `TrackState` / `PhaseState`）在 `scripts/pipeline/state.py` 中定义为 frozen dataclass。

## CLI 用法

```bash
RUNNER="python3 .opencode/skills/pg-build-v2/scripts/pg-pipeline-runner.py"

$RUNNER bootstrap <change>
$RUNNER next <change>
$RUNNER record <change> --status <status> --report <path> --summary "<摘要>" [--outputs <p1>,<p2>] [--issues <i1>,<i2>] [--evidence <e1> [--evidence <e2> ...]] [--tasks-updated <t1> [--tasks-updated <t2> ...]]
  > **注意**：gate/final-gate 阶段的 `--summary` 必须包含 `gate_score: <0-100>`，例：`--summary "8/9 检查通过, gate_score: 91, p0_failures: []"`
$RUNNER progress <change>
$RUNNER env-action <change> --phase prepare_env|clean_env --stage <stage> --env <env> [--timeout <seconds>]
$RUNNER env-action-result <change> --phase prepare_env|clean_env --stage <stage> --env <env> --success true|false [--log-path <path>] [--exit-code <code>] [--started-ts <ts>] [--error <msg>]
  注：--success 是布尔值 true|false，表示 hook 是否成功执行。
      与 record 的 --status 字段（completed/failed/...）和 sub-agent 返回 JSON 的 status 字段含义都不同。
  > **注意**：`--phase` 参数必须填 `prepare_env` 或 `clean_env`（不是 stage 名）
```

**status**: `completed | failed | escalate | pass | fail`

## 编排器执行协议

编排器通过 runner CLI 与 pipeline 引擎交互。**编排器不得读取 change 目录下的任何文件**（tasks.md、design.md、proposal.md、2-build/ 等）——所有输入来自 runner stdout JSON，所有文件 I/O 由 runner 和 sub-agent 处理。

主循环：

```
循环:
  0. $RUNNER bootstrap <change>  → 若返回 env_hook_plan，bash 执行后调 env-action-result（phase 填 prepare_env/clean_env）
  1. $RUNNER next <change>       → 检查 action 字段
  2. switch(action):
       "env_switch"        → env-action → bash exec → env-action-result → 回 next
       "dispatch"          → 派遣 sub-agent，传 dispatch_file 路径（不可修改）
       "dispatch_final_gate" → 派遣 pg-build/gate agent
       "advance"           → 回步骤 1
       "done"              → 触发 verify-and-merge
       "workflow_failed"   → 终止
```

### Dispatch 协议

runner 返回 dispatch action 带 `dispatch_file` 字段。编排器：
1. **绝不允许读取或修改 dispatch_file 内容**。只需把文件路径传给 sub-agent。
2. 正确用法：`task(prompt="读取 {dispatch_file} 并执行任务，完成后用 pg-build-result 脚本生成返回 JSON")`

### Sub-agent 返回契约

所有 sub-agent 必须返回 JSON：
```json
{
  "summary": "<= 200 字",
  "outputs": ["<产物绝对路径>"],
  "tasks_updated": ["<task_id>"],
  "status": "completed | failed | escalate | pass | fail",
  "evidence_paths": ["<证据>"],
  "report_path": "<必须存在且可读>"
}
```

## Record 状态守卫

| sub | 允许 status |
|-----|------------|
| test/dev/simple | completed, failed |
| verify | completed, escalate, failed |
| fix/fix-gate | completed, failed |
| gate | pass, fail |
| final-gate | pass, fail |

## 子 Pipeline 机制（v2.3 unified re_verify）

| 循环 | 触发条件 | 流向 |
|------|---------|------|
| **fix 循环** | verify escalate → SubPipeline(fix, verify) | fix 完成后**总是** re_verify（→ verify）。verify.completed 在子 pipeline 中 → gate。verify.escalate → 再 dispatch fix（计数 verify.fix_cycles）。`len(fix_cycles) >= max_fix_retries` 时强制进 gate。|
| gate-fix 循环 | gate fail → SubPipeline(fix-gate, verify, gate) | 回到主 pipeline |

### max_fix_retries 语义（v2.3）

- **不再**是 fix agent 的内部重试次数
- **现在是** verify→fix 循环的总次数（verify escalate 触发的 fix 子 pipeline 总数）
- 缺省值：5（在 `.pg/project.yaml` `tracks.<name>.max_fix_retries` 配置）

### fix 失败处理（v2.3）

- fix agent 自身 `STATUS_FAILED` 不再 retry fix 自身
- 立即 `_sub_pipeline_advance` 到 verify，让 verify 复测决定下一步
- 这样 `max_fix_retries` 真正成为"verify→fix 循环"的限制
- 取消了 v2.1 的 `accept_gap` 协议（不再适用）

### 移除的 v2.2 概念

- ❌ `fix_routing` 配置（`direct_to_gate` / `re_verify`）：统一为 re_verify
- ❌ fix 内部 retry → accept_gap 流程：统一为 re_verify
- ❌ `EVT_FIX_SKIPPED_VERIFY` 事件：fix 永远不跳过 verify
- ❌ `MAX_FIX_CYCLES = 4` 硬编码：改读 `track.max_fix_retries`

## 错误路径无副作用（v2.3）

reducer 返回 `kind="error"` 时：

- **保留**：传入的 state（tracks 内容、current_track、current_phase 等）
- **不写** event_log（无 record_received）
- **不写** snapshot.json（避免被空 state 覆盖）
- **不跑** `_auto_commit`（避免脏 commit）
- 返回 error JSON 给编排器，编排器可重新 record

这是解决 v2.2 中"reducer error 时 snapshot 被清空"的 root cause bug。

## 常见错误排查

| 现象 | 原因 | 修复 |
|------|------|------|
| `action: error` + `No active item to record` | reducer 报错（缺 tasks_updated / 未知 phase / track not found 等） | state 未变，可补全参数后重新 record |
| `action: error` + `invalid transition` | record status 与 phase 不匹配 | 对照 Record 状态守卫表 |
| sub-agent 告"任务不完整" | dispatch file 路径没传 | 检查 `dispatch_file` 路径 |
| `action: error` + `evidence_missing` | verify/gate 的 `evidence_paths` 为空 | 重跑验证，强调产出 evidence |
| `--report /tmp/nonexistent.md` → 报错 | 报告文件不存在 | 确认文件已写盘 |
| env hook 卡死循环 | 编排器忘调 `env-action-result` | 每次 bash 执行后必调此命令 |
| `错误: 无效 success: <X>` | `--success` 字段被填了非布尔值（如 true/false 之外的字符串） | `--success` 是布尔值（true\|false），与 `--status`（completed/failed/...）和 sub-agent 的 status 字段解耦；应明确区分两个字段 |
| `schema_violation: ... 要求 --outputs 非空` | dev/test/fix 没传 `--outputs` | sub-agent 必须返回产物列表 |
| `schema_violation: ... 要求 summary 中含 'gate_score: <0-100>'` | gate/final-gate 阶段 summary 缺评分 | 确保 summary 含 `gate_score: <0-100>, p0_failures: [...]` |

## 完整代码参考

- **CLI 入口**: `scripts/pg-pipeline-runner.py`
- **Orchestrator**: `scripts/pipeline/orchestrator.py` (next/record/progress)
- **Reducer**: `scripts/pipeline/reducer.py` (纯函数状态转换)
- **State**: `scripts/pipeline/state.py` (frozen dataclass)
- **Event Schema**: `scripts/pipeline/events.py` (所有 event type 定义)
- **Dispatch**: `scripts/pipeline/dispatch.py` (构建 action JSON + dispatch file)
- **Bootstrap**: `scripts/bootstrap.py` (pipeline 启动副作用)
- **Templates**: `prompt-templates/*.yaml` (8 个 phase 模板)