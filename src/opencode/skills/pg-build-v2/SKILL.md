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
$RUNNER record <change> <status> --report <path> --summary "<摘要>" [--outputs <p1>,<p2>] [--issues <i1>,<i2>] [--evidence <e1> [--evidence <e2> ...]] [--tasks-updated <t1> [--tasks-updated <t2> ...]]
$RUNNER progress <change>
$RUNNER env-action <change> <phase> <stage> <env> [hook_timeout_seconds]
$RUNNER env-action-result <change> <phase> <stage> <env> <ok> [log_path] [exit_code] [started_ts] [error]
```

**status**: `completed | failed | escalate | pass | fail`

## 编排器执行协议

编排器通过 runner CLI 与 pipeline 引擎交互。**编排器不得读取 change 目录下的任何文件**（tasks.md、design.md、proposal.md、2-build/ 等）——所有输入来自 runner stdout JSON，所有文件 I/O 由 runner 和 sub-agent 处理。

主循环：

```
循环:
  0. $RUNNER bootstrap <change>  → 若返回 env_hook_plan，bash 执行后调 env-action-result
  1. $RUNNER next <change>       → 检查 action 字段
  2. switch(action):
       "env_switch"        → env-action → bash exec → env-action-result → 回 next
       "dispatch"          → 派遣 sub-agent，传 dispatch_file 路径（绝不读其内容）
       "dispatch_final_gate" → 派遣 pg-build/gate agent
       "advance"           → 回步骤 1
       "done"              → 触发 verify-and-merge
       "workflow_failed"   → 终止
```

### Dispatch 协议

runner 返回 dispatch action 带 `dispatch_file` 字段。编排器：
1. **绝不读取 dispatch_file 内容**
2. 正确用法：`task(prompt="任务指令在 {dispatch_file} 中，请读取并执行，完成后返回 JSON")`

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

## 子 Pipeline 机制

| 循环 | 触发条件 | 流向 |
|------|---------|------|
| fix 循环 | verify escalate → SubPipeline(fix) | fix 完成后默认直接进 gate（`direct_to_gate`）；`re_verify` 时回 verify |
| gate-fix 循环 | gate fail → SubPipeline(fix-gate, verify, gate) | 回到主 pipeline |

## 常见错误排查

| 现象 | 原因 | 修复 |
|------|------|------|
| `action: error` + `No active item` | 连续两次 record 未调 next | 每次 record 后调 next |
| `action: error` + `invalid transition` | record status 用错 | 对照 Record 状态守卫表 |
| sub-agent 告"任务不完整" | dispatch file 路径没传 | 检查 `dispatch_file` 路径 |
| `action: error` + `evidence_missing` | verify/gate 的 `evidence_paths` 为空 | 重跑验证，强调产出 evidence |
| `--report /tmp/nonexistent.md` → 报错 | 报告文件不存在 | 确认文件已写盘 |
| env hook 卡死循环 | 编排器忘调 `env-action-result` | 每次 bash 执行后必调此命令 |
| `schema_violation: ... 要求 --outputs 非空` | dev/test/fix 没传 `--outputs` | sub-agent 必须返回产物列表 |

## 完整代码参考

- **CLI 入口**: `scripts/pg-pipeline-runner.py`
- **Orchestrator**: `scripts/pipeline/orchestrator.py` (next/record/progress)
- **Reducer**: `scripts/pipeline/reducer.py` (纯函数状态转换)
- **State**: `scripts/pipeline/state.py` (frozen dataclass)
- **Event Schema**: `scripts/pipeline/events.py` (所有 event type 定义)
- **Dispatch**: `scripts/pipeline/dispatch.py` (构建 action JSON + dispatch file)
- **Bootstrap**: `scripts/bootstrap.py` (pipeline 启动副作用)
- **Templates**: `prompt-templates/*.yaml` (8 个 phase 模板)