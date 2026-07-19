---
name: pg-build
description: 基于 Event Sourcing + Reducer 模式的 pipeline 编排引擎。取代旧版过程式状态机架构。
license: MIT
compatibility: 需要 .pg/project.yaml / execution-manifest.yaml 驱动编排。
metadata:
  author: pg-spec
  version: "1.0"
---

# pg-build

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
sub-agent (via Task tool)      ← test / dev / review / verify / gate / fix / fix-review / fix-gate
```

## v2.6 新增：Code View 阶段

每个 track 在 `dev → verify` 之间增加 **review** 阶段（dev 完成后、verify 前），由独立的 review agent 对代码做静态审查。目的是在集成/E2E 验证之前发现"实现与设计不一致 / scope creep / 模式不一致 / 测试契约弱"等**静态代码问题**，降低 fix cycle 成本。

完整流程：

```
test → dev → review → verify → gate
              ↓ escalate (R-* FAIL)
            [fix-review → review → ...] (max N 次, 独立计数 review_fix_cycles)
              ↓ fail
            accepted_gaps → gate
```

详见 [§v2.6 Code View 阶段](#v26-review-阶段)。

所有状态管理（`PipelineState` / `TrackState` / `PhaseState`）在 `scripts/pipeline/state.py` 中定义为 frozen dataclass。

## CLI 用法

```bash
RUNNER="python3 .opencode/skills/pg-build/scripts/pg-pipeline-runner.py"

$RUNNER bootstrap <change>                   # 正常启动 / 检测到失败时自动重置
$RUNNER bootstrap <change> --detect          # 只检测失败状态，不修改文件
$RUNNER bootstrap <change> --resume          # 从 workflow_failed 恢复，保留已完成 track
$RUNNER next <change>
$RUNNER record <change> --status <status> --report <path> --summary "<摘要>" [--outputs <p1>,<p2>] [--issues <i1>,<i2>] [--evidence <e1> [--evidence <e2> ...]] [--tasks-updated <t1> [--tasks-updated <t2> ...]]
  > **注意**：gate/final-gate 阶段的 `--summary` 必须包含 `gate_score: <0-100>`，例：`--summary "8/9 检查通过, gate_score: 91, p0_failures: []"`
$RUNNER progress <change>
$RUNNER env-action <change> --phase prepare_env|clean_env --stage <stage> --env <env> [--timeout <seconds>]
$RUNNER env-action-result <change> --phase prepare_env|clean_env --stage <stage> --env <env> --success true|false [--log-path <path>] [--exit-code <code>] [--started-ts <ts>] [--error <msg>]
  注：--success 是布尔值 true|false，表示 hook 是否成功执行。
      与 record 的 --status 字段（completed/failed/...）含义不同。
$RUNNER reset <change>                       # 手动清除 terminal failed 状态
$RUNNER reset <change> --resume              # 手动恢复（保留 snapshot，仅改 status 为 running）
```

**status**: `completed | failed | escalate | pass | fail`

## 编排器执行协议

编排器通过 runner CLI 与 pipeline 引擎交互。**编排器不得读取 change 目录下的任何文件**（tasks.md、design.md、proposal.md、2-build/ 等）——所有输入来自 runner stdout JSON，所有文件 I/O 由 runner 和 sub-agent 处理。

主循环：

```
循环:
  0. $RUNNER bootstrap <change> --detect  → 检查 response:
        • detected: true → **检测到上次 pipeline 因 terminal failure 终止**。
          用 question tool 询问用户选择 Reset 或 Resume：
          - 用户选 Reset → `$RUNNER bootstrap <change>`（自动清理 events + snapshot，从头开始）
          - 用户选 Resume → `$RUNNER bootstrap <change> --resume`（保留已完成 track 状态，从失败处继续）
        • detected: false → 进入 step 0b
  0b. $RUNNER bootstrap <change>  → 检查 response:
        • ok: false         → workflow_failed（fatal=true）。**禁止自动修复**（如 git checkout），展示 error 给用户。
        • ok: true + env_hook_plan=null → 进入 step 1
        • ok: true + env_hook_plan ≠ null → bash 执行 plan.command，然后 env-action-result（phase 填 prepare_env/clean_env）→ 调 bootstrap 再次检查 env_hook_plan
  1. $RUNNER next <change>       → 检查 action 字段
  2. switch(action):
        "env_switch"        → env-action → bash exec → env-action-result → 回 next
        "dispatch"          → 派遣 sub-agent，传 dispatch_file 路径（不可修改）
        "dispatch_final_gate" → 派遣 pg-build/gate agent
        "advance"           → 回步骤 1
        "done"              → 触发 verify-and-merge
        "workflow_failed"   → 终止
```

### 人工介入场景

编排器（LLM agent）**必须**在以下场景终止 pipeline 并交由人工处理（不可自动修复）：

| 场景 | bootstrap 返回 | 编排器动作 |
|------|---------------|-----------|
| 分支不匹配 | `ok: false, error: "当前本地分支..."` | 输出 error，**禁止** `git checkout` 或任何自动修复 |
| change 目录不存在 | `ok: false, error: "change 目录...不存在"` | 输出 error，提醒运行 pg-propose |
| project.yaml 配置错误 | `ok: false, error: "配置错误..."` | 输出 error |
| env hook 执行失败（`severity: fatal`） | `env-action-result` 返回 `success: false` + `severity: fatal` | 输出 error 与 log_path，提示人工修复环境，**终止 pipeline** |
| env hook 执行失败（`severity: recoverable`） | `env-action-result` 返回 `success: false` + `severity: recoverable` | 输出 WARN 与 log_path，**继续 next()**（hook 自身保证后续 idempotent） |
| env hook 执行失败（`severity` 缺失） | `env-action-result` 返回 `success: false` 无 severity 字段 | **视作 fatal**，保守处理 → 输出 error 与 log_path，终止 pipeline |

### env hook severity 三态协议

`env-action-result` 的 `success=false` 不再单一处理，按 `severity` 字段分流：

| `severity` | state 影响 | 编排器动作 | 实现位置 |
|-----------|-----------|-----------|---------|
| `fatal` | `stage_prepared` 不变，`current_stage` 不变 | 终止，输出 error | `bootstrap.py:1075-1078` (default) |
| `recoverable` | `stage_prepared` 不变，`current_stage` 不变 | 输出 WARN，继续 next() | 待扩展 |
| 缺失 | 视作 fatal | 终止 | `bootstrap.py:1075-1078`（无 severity 时默认 fatal） |

**CLI 协议扩展**：

```bash
# v3.x 推荐：env-action-result 增加 --severity 参数
python3 .opencode/skills/pg-build/scripts/pg-pipeline-runner.py \
    env-action-result <change> \
    --phase prepare_env|clean_env \
    --stage <stage> --env <env> \
    --success true|false \
    [--severity fatal|recoverable]  # 缺省 = fatal
    [--log-path <path>] [--exit-code <code>] [--started-ts <ts>] [--error <msg>]
```

**reducer 实现位置**：`scripts/bootstrap.py:1020-1105` (`cli_env_action_result`)。

**测试覆盖**：`scripts/tests/test_bootstrap.py::test_cli_env_action_result_failed_does_not_update_state`（验证失败时 state 不被破坏）。

**为何 severity 默认 fatal**：env hook 失败通常意味着环境异常，保守处理更安全；recoverable 是显式声明的"已知可恢复失败"，应由 hook 自身在 result.json 的 `severity` 字段中标注。

**不自动修复原则**：任何 `ok: false` 或 `workflow_failed`（fatal=true）都不应触发编排器的自动修复行为——包括但不限于 git checkout、git branch 创建、文件修改、配置修改。编排器只输出错误信息给用户，由用户决定下一步操作。

### Resume / Detect 协议（v3.10）

**入口**：编排器在主循环 step 0 先调 `bootstrap <change> --detect`，而非直接调 `bootstrap <change>`。

当 `bootstrap <change> --detect` 返回 `detected: true` 时，编排器必须：

1. **不要自动 reset** — 用 question tool 询问用户：

```
┌─────────────────────────────────────────────────────────┐
│ 检测到上次 pipeline 因 int.scr:scenario-execute 失败     │
│ 而终止。请选择恢复方式：                                 │
│                                                         │
│ ○ Reset（从头开始）— 清除所有状态，重新跑全部 track      │
│ ● Resume（从失败处继续）— 保留已完成 track 状态          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

2. 用户选择后：

| 用户选择 | 编排器操作 | 效果 |
|---------|-----------|------|
| **Reset** | `$RUNNER bootstrap <change>` | 全量重置（删除 events + snapshot），从头开始 |
| **Resume** | `$RUNNER bootstrap <change> --resume` | 保留 snapshot，仅将 status 从 "failed" 改为 "running" |

3. Resume 后的行为：
   - 已完成 track 的 `PhaseState` 保留在 snapshot 中
   - `next()` 自动跳过已完成的 track，从第一个未完成的 phase 开始 dispatch
   - 环境 hook 仍会执行（幂等）
   - 所有 2-build/ 下的工件（dispatch files、reports、logs）保留

**实现参考**：`bootstrap.py:_detect_failed_state`（只读检测）、`bootstrap.py:cli_auto_reset(resume=True)`（保留 snapshot）。

### Dispatch 协议

runner 返回 dispatch action 带 `dispatch_file` 字段。编排器：
1. **绝不允许读取 dispatch_file 内容**。只需把文件路径传给 sub-agent。
2. 正确用法：`task(prompt="读取 {dispatch_file} 并执行任务，完成后用 pg-build-result 脚本生成返回 JSON")`。
   错误用法：调用 task 工具时，提示词的内容比 `读取 {dispatch_file} 并执行任务，完成后用 pg-build-result 脚本生成返回 JSON` 更多，比如将 `dispatch_file` 进行摘要后放在提示词里。sub agent 会自己阅读 dispatch_file 全文，编排器不要对提示词做无意义的修改。

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

### v2.4 result.json 强制落盘

v2.4 起，sub-agent 不仅要返回 JSON，**还必须把 JSON 落盘到 dispatch_file 同前缀的 result.json**：

| dispatch_file | result.json |
|---------------|-------------|
| `2-build/002-dev.backend-dev-dispatch.md` | `2-build/002-dev.backend-dev-result.json` |
| `2-build/018-dev.frontend-fix-dispatch-2.md` | `2-build/018-dev.frontend-fix-result-2.json` |
| `2-build/028-final-gate-gate-dispatch.md` | `2-build/028-final-gate-gate-result.json` |

**保护流程**（编排器侧）：
1. 派遣 sub-agent（dispatch_file 路径传过去）
2. 检查 `expected_result_path` 是否落盘
3. **缺失 → 编排器自动重试一次**（重新派送同 dispatch_file，prompt 追加"上次未生成 result.json"）
4. **重试后仍缺失 → fatal** `result_json_missing_after_retry`，pipeline 停止

**sub-agent 必须执行的命令**（从 dispatch prompt 中 `{result_json_path}` 占位符取值）：

```bash
python3 .opencode/skills/pg-build/scripts/pg-build-result \
    --mode agent \
    --status <status> \
    --summary "<=200 字>" \
    --track <dev.xxx> --phase <phase> \
    --output-path {result_json_path} \
    --require-output \
    [--report <路径>] [--evidence <路径>] \
    [--outputs <p1>,<p2>] [--tasks-updated <id>] ...
```

**关键约束**：
- `--output-path` 与 `--require-output` 必须同时提供
- 写入失败 → 脚本 exit 2，sub-agent 应修复后重试
- **禁止**仅返回 stdout JSON 而不落盘（编排器会视为未完成）

### result.json fatal 后的编排器动作

当 `runner record` 返回 `result_json_missing_after_retry`（已 retry 1 次仍失败）时，编排器按以下顺序处理：

1. **不要重试第 3 次**：`result_json_missing_after_retry` 已经是 v2.4 协议的"硬终止"信号
2. **不要直接 patch snapshot**：会破坏 `event_log` + `snapshot` 一致性

**推荐路径**：

| 子 agent 失败原因 | 排查 | 恢复 |
|------------------|------|------|
| sub-agent 未执行 `pg-build-result --output-path` | 打开 task transcript，grep `pg-build-result` 调用 | 重 dispatch 同 `dispatch_file`，prompt 追加"上次未生成 result.json，请严格按 contract 落盘" |
| sub-agent 调了 `pg-build-result` 但 exit 2 | 检查 `--output-path` 目录是否存在、是否可写 | 修正路径，重 dispatch |
| sub-agent 任务卡死 / crash | 看 task transcript 是否在 `__ORCHESTRATOR_READY__` 之前退出 | 重新启动 sub-agent 并重 dispatch（dispatch_file 不变） |
| result.json 被外部清理 | 检查 `2-build/` 目录文件 | 重 dispatch，prompt 明确"不要清理 `<result.json>`" |
| pipeline.events 与 snapshot 状态机不一致 | `cat pipeline.events \| tail -5` 看最后事件类型 | 走 `pg-archive move <change>` 归档后重新触发 `pg-propose` |

**如需中断**（重试超过 2 次仍失败）：

```bash
# 1. 归档当前 change
python3 .opencode/skills/pg-archive/scripts/pg-archive.py move <change> \
    --project-root /path/to/project

# 2. 提交 issue 跟踪根因
gh issue create --title "pg-build: sub-agent result.json 持续缺失"

# 3. 重新触发 pg-propose
# 注：原 change 的 tasks.md / design.md 可能仍可复用
```

### sub-agent `failed` 状态自动重试

#### 触发

sub-agent 返回 `status: "failed"` 时，runner 不会立即终止——会按 `max_fail_retries`（默认 3）自动重试同一 phase。

#### 重试协议

| Attempt | reducer 行为 | next 返回 | 编排器观察 |
|---------|------------|-----------|------------|
| 1 → 2 | attempt++, dispatch 同 phase | dispatch 同一 phase | 看到 `dispatch_seq` 不变、`attempt: 2` |
| 2 → 3 | attempt++, dispatch 同 phase | dispatch 同一 phase | 同上 |
| attempt > max_fail_retries | workflow_failed | workflow_failed | pipeline 终止，需人工介入 |

#### 关键约束

- **attempt 计数在 reducer 内部**（`PhaseState.attempt` 字段），不污染顶层 state
- **重试期间 sub-agent 看到的是新 dispatch_file**（dispatch_seq 不递增，但 cycle 内内容可能更新）
- **重试期间 cycle 不变**（避免 trace 文件名爆炸）
- **orchestrator 不需要感知重试**：每次 next() 返回的 dispatch_file 都应读取最新内容

#### max_fail_retries 配置

| Track 类型 | 默认 | 配置文件 |
|-----------|------|---------|
| standard | 3 | `tracks.<t>.max_fail_retries` |
| simple | 3 | 默认（无配置项） |
| scenario | 3 | 默认（scenario-prepare 与 scenario-execute 各自独立计数） |

#### 与 final-gate / gate 子 pipeline 的关系

gate-fix 循环（v2.3 unified re_verify）中，**fix agent 自身 `failed` 走同样的 retry 协议**，
耗尽 `max_gate_fix_retries` 后进 gate-fail → fix-gate 子 pipeline。详见 §子 Pipeline 机制。

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
| `action: error` + `No active item to record` | **真实场景**：当 track 已 `status=completed` 且无 active 子 pipeline 时，`state.current_track` / `state.current_phase` 为空（`Orchestrator.record` line 499 推断失败）。**与"reducer 报错"完全不是一回事**——此时 `runner record` 直接拒绝，state 不变。**禁止 patch snapshot 伪造 current_track**。 | 见下方决策表 |
| `action: error` + `invalid transition` | record status 与 phase 不匹配 | 对照 Record 状态守卫表 |
| sub-agent 告"任务不完整" | dispatch file 路径没传 | 检查 `dispatch_file` 路径 |
| `action: error` + `evidence_missing` | verify/gate 的 `evidence_paths` 为空 | 重跑验证，强调产出 evidence |
| `--report /tmp/nonexistent.md` → 报错 | 报告文件不存在 | 确认文件已写盘 |
| env hook 卡死循环 | 编排器忘调 `env-action-result` | 每次 bash 执行后必调此命令 |
| `错误: 无效 success: <X>` | `--success` 字段被填了非布尔值（如 true/false 之外的字符串） | `--success` 是布尔值（true\|false），与 `--status`（completed/failed/...）和 sub-agent 的 status 字段解耦；应明确区分两个字段 |
| `schema_violation: ... 要求 --outputs 非空` | dev/test/fix 没传 `--outputs` | sub-agent 必须返回产物列表 |
| `schema_violation: ... 要求 summary 中含 'gate_score: <0-100>'` | gate/final-gate 阶段 summary 缺评分 | 确保 summary 含 `gate_score: <0-100>, p0_failures: [...]` |
| `result_json_missing_after_retry` (v2.4) | sub-agent 未调用 `pg-build-result --output-path` | 检查 dispatch prompt 中 `{result_json_path}` 占位符是否被替换；sub-agent 必须执行 `pg-build-result --mode agent --output-path <path> --require-output` |
| `pg-build-result` exit 2 (v2.4) | `--require-output` 模式下写入失败 | 检查 `--output-path` 目录是否存在、是否可写；路径必须是绝对路径 |
| `result_json_missing: ...` (v2.5) | `--result-json` 指向不存在路径 | 用 dispatch action 返回的 `expected_result_path`；或改回 v2.4 显式 CLI 形式 |
| `result_json_invalid: ...` (v2.5) | `--result-json` 文件 JSON 解析失败或顶层非 dict | 让 sub-agent 重跑 `pg-build-result` 落盘 |
| `status_missing: ...` (v2.5) | `--result-json` 与 `--status` 同时缺 status 字段 | 二选一必填（CLI `--status` 或文件 `status`） |

### "No active item to record" 决策表

收到 `action: error + No active item to record` 时，编排器按以下顺序处理：

| 场景 | 排查 | 处理 |
|------|------|------|
| pipeline 已 `status=completed`（所有 track 都 done） | `python3 ... progress <change>` 看 status | 正常！直接触发 pg-verify-and-merge，不要尝试 record |
| 上一个 dispatch 还在 `attempt=N` 重试中 | 等 `max_fail_retries` 耗尽或 sub-agent 成功 | 继续调用 `next` 等 dispatch 回来 |
| snapshot 损坏 / `current_track` 为空字符串 | `cat .pg/changes/<change>/2-build/pipeline.snapshot.json \| jq .state.current_track` | 不要直接 patch — 调用 `pg-archive move` 归档后重新触发 pg-build |
| track 已 completed 但想补一个 sub-phase | 设计错误：track 完成应触发下一 phase dispatch | 走 `pg-propose-refine` 修改 design.md |

**关键**: 收到此 error **不要**调用 `python3 ... record --result-json ...`，因为 record 会再次因 `current_track` 为空失败。

### scenario track 常见错误

| 现象 | 根因 | 处理 |
|------|------|------|
| `action: workflow_failed` + `missing_gate_assessments` 含 `real-integration.<track>` | scenario track 在 final-gate 前置门控被误报（v3.6 前 known bug） | **升级到 v3.6** 后 bootstrap 自动豁免。如已发生，按 §final-gate 前置门控协议 重跑该 track 或 pg-archive 归档 |
| `action: dispatch` + `sub: scenario-fix` | scenario-execute escalate 触发子 pipeline | 正常行为，dispatch 路径正确，编排器无需干预 |
| `track_types[tid] = "scenario"` 但 `gate_enabled=True` | 旧 snapshot 未迁移（v3.6 前遗留） | 运行 `python3 ... bootstrap <change>` 重新派生（bootstrap 会覆盖 gate_enabled） |
| scenario-execute 报告 `failed_scenarios` 非空但 `status=completed` | sub-agent 未正确填写 escalate 协议 | 检查 result.json 的 `status` 字段是否应为 `escalate` 而非 `completed` |
| scenario-prepare 返回 `failed`（模块已就绪） | scenario-prepare 误判 role 启动失败 | 检查 `invoke-hook --action health_check` 是否返回非 0；重 dispatch 同 dispatch_file |

## 完整代码参考

- **CLI 入口**: `scripts/pg-pipeline-runner.py`
- **Orchestrator**: `scripts/pipeline/orchestrator.py` (next/record/progress)
- **Reducer**: `scripts/pipeline/reducer.py` (纯函数状态转换)
- **State**: `scripts/pipeline/state.py` (frozen dataclass)
- **Event Schema**: `scripts/pipeline/events.py` (所有 event type 定义)
- **Dispatch**: `scripts/pipeline/dispatch.py` (构建 action JSON + dispatch file)
- **Sub Pipeline**: `scripts/pipeline/sub_pipeline.py` (递归子 pipeline，含 review-cycle)
- **Profile Loader**: `scripts/pipeline/profile_loader.py` (v2.6: profile 加载 + Union 合并)
- **Bootstrap**: `scripts/bootstrap.py` (pipeline 启动副作用)
- **Templates**: `prompt-templates/*.yaml` (9 个 phase 模板，含 review / fix-review)
- **v2.6 Code View**:
  - `.pg/code-review/code-review.yaml` (profile 索引)
  - `.pg/code-review/<profile>/*.md` (检查项执行细则)
  - `.opencode/agents/pg-build/review.md` (sub-agent 定义)
  - `.opencode/agents/pg-build/fix-review.md` (fix sub-agent 定义)
- **v2.4 result.json 落盘**:
  - `scripts/pg-build-result` (`--output-path` / `--require-output` 参数)
  - `scripts/pipeline/orchestrator.py:_derive_result_path` (dispatch_file → result.json 派生)
  - `scripts/pipeline/dispatch.py` (返回 `expected_result_path` 字段)
  - `prompt-templates/blocks/sub_agent_contract.yaml` (强制落盘指令块)

## v2.6 Code View 阶段

### 位置

```
test → dev → review → verify → gate
```

review 是**新 phase**（不是 verify 内部的步骤），由独立的 `pg-build/review` agent 执行。

### 与 verify 的区别

| 维度 | review（静态） | verify（运行时） |
|------|-------------------|------------------|
| 视角 | 代码静态属性 | 运行时行为 |
| 检查项 | design 对齐 / scope creep / 模式一致 / 文件位置 / 测试契约 | V-* 验证项 |
| 不做的事 | 跑测试 / 启服务 | 改代码 |
| 触发 fix | R-*（设计/模式类） | V-*（功能/集成类） |

### Profile 配置

`review` 检查项由 **profile** 控制，位于 `.pg/code-review/code-review.yaml`：

```yaml
profiles:
  default:           # 兜底
    checks: { design_alignment: ..., scope_creep: ..., ... }
    pass_threshold: 80
    escalate_threshold: 60
  java-spring:       # 语言 profile（自动派发）
    inherit: default
    checks: { pattern_consistency: ..., null_safety: ... }
    pass_threshold: 85
  security:          # 显式 profile（需用户指定）
    checks: { secret_leak: ..., auth_bypass: ... }
    pass_threshold: 90
```

每项检查的执行细则在 `.pg/code-review/<profile_name>/<check_name>.md`。

### Profile 选择优先级（高 → 低）

1. `track.code_review_profiles: [...]` — 用户显式指定（按顺序 = 优先级）
2. `track.code_review_profile: "..."` — legacy 单 profile
3. 按 `module_details[].language` 自动派发（java → java-spring, go → go, ...）
4. `default` 兜底

### Union 合并语义

| 字段 | 合并策略 |
|------|----------|
| `checks` | 并集（包含 inherit 链） |
| `weight` | `max(各 profile 同名项)` |
| `enabled` | `OR`（任一为 true 即 true） |
| `pass_threshold` / `escalate_threshold` | `min`（更严格）— **仅取显式 profile，不含 inherit 链** |

### Track 配置

```yaml
# .pg/project.yaml
tracks:
  backend:
    code_review_enabled: true          # v3.x: 决定 tasks.md 含/不含 review 章节
    max_review_fix_retries: 3       # 默认 3
  auth-service:
    code_review_enabled: true
  proto-gen:
    # simple track 自动 code_review_enabled=false（无需配置）
```

### v3.x 变化：execution-manifest.yaml 为唯一 SSOT

**v2.6 → v3.x 重大重构**：

- pg-build 内部 `TrackState.code_review_*` 字段已**全部删除**（`code_review_enabled` / `code_review_profiles` / `code_review_profile` / `code_review_languages`）
- 改由 **execution-manifest.yaml** 的 `phase_prompts.review` 是否存在作为**唯一 SSOT**
- orchestrator bootstrap 时从 manifest 派生 `code_review_enabled: bool` 字段

```
execution-manifest.yaml
  phases:
    review: present  → code_review_enabled=True  → 派发 pg-build/review agent
                         → 缺失   → code_review_enabled=False → reducer 自动完成 review phase（silent skip）
```

**兼容 v2.6**：旧 snapshot 含 `code_review_enabled` 字段 → `from_dict` 自动派生到 `code_review_enabled`（True/False 一致迁移）。

**profile 选择**：pg-build 不再读 `track.code_review_profiles` / `code_review_profile`，完全由 `.pg/code-review/code-review.yaml` 全局 + `module_details[].language` 自动派发（java→java-spring, ts→vue3, go→go）。

### Score 协议

`review` agent 返回时 `summary` 必须包含：

```
review_score: <0-100>, p0_failures: [R-1, R-3]
```

| review_score 范围 | disposition | 下一步 |
|--------------|-------------|--------|
| ≥ pass_threshold | `completed` | → verify |
| escalate_threshold ≤ score < pass | `escalate` | → fix-review 循环（独立计数 `review_fix_cycles`） |
| < escalate_threshold | `failed` | → reducer 自动重试 review phase（同 dispatch_file，`attempt++`，`attempt ≤ max_fail_retries`），耗尽 → `workflow_failed` |

### review failed 重试协议

`status=failed` 触发 reducer 自动重试，与 verify/test/dev failed 复用同一 `attempt` 计数器（来自 `PhaseState.attempt`）：

| Attempt | reducer 行为 | next 返回 |
|---------|------------|-----------|
| 1 → 2 | `attempt++`，dispatch 同 phase（`dispatch_file` 不变，`cycle` 不变） | `dispatch` 同一 phase，`attempt: 2` |
| 2 → 3 | 同上 | 同上 |
| `attempt > max_fail_retries` | `workflow_failed` | `workflow_failed` |

**与 fix-review 循环的区别**：

| 维度 | review failed 重试 | review escalate 修复 |
|------|---------------------|----------------------|
| 触发 | `status=failed`（score < escalate_threshold） | `status=escalate`（score ≥ escalate_threshold） |
| 计数字段 | `PhaseState.attempt`（与 verify/test/dev 共享） | `review_fix_cycles`（独立计数） |
| 子 agent | 同 dispatch_file 重派 | `pg-build/fix-review` agent |
| 终止 | `attempt > max_fail_retries` → `workflow_failed` | `review_fix_cycles >= max_review_fix_retries` → force verify |

**reducer 实现位置**：`scripts/pipeline/reducer.py:591-601` (`_handle_review` 的 `STATUS_FAILED` 分支)。

**测试覆盖**：`scripts/tests/test_reducer.py::test_review_failed_retries` + `test_review_failed_retries_within_limit`。

### fix-review 循环

`escalate` 触发独立子 pipeline `review-cycle`（phases = `fix-review`, `review`），与 verify 的 fix 循环**不共享计数**：

```
verify.fix_cycles        ← verify escalate 计数
review.review_fix_cycles  ← review escalate 计数（独立）
```

`max_review_fix_retries` 默认为 3，耗尽后强制进 verify。

### 报告文件命名

| 文件 | 内容 |
|------|------|
| `2-build/{seq}-{track}-review.md` | review agent 产出的审查报告 |
| `2-build/{seq}-{track}-fix-review-{cycle}.md` | fix-review agent 产出的修复记录 |

### 关闭方式

- `track.code_review_enabled: false` — 关闭单个 track（在 propose 阶段生效，决定 manifest 是否含 review sub）
- simple track 自动跳过（manifest 不生成 phase_prompts，pg-build 派生 `code_review_enabled=False`）
- `track_types[tid] == "simple"` — simple track 自动关闭（无需配置）

---

## v3.4 Verify / Gate 阶段也支持按 track 关闭

**背景**：v2.6 引入 review 阶段按 track 关闭的能力，但 verify / gate 始终派发。某些场景下用户希望跳过这些阶段：

- 简单改动（如 isolated docs / README typo fix）不需要 verify
- 配置 / 元数据变更不需要 gate
- 跨 track 的 final-gate 仍派发（不在此范围内）

### 配置

完全沿用 review 关闭模式 —— `project.yaml` 配置 + execution-manifest.yaml 派生 SSOT：

```yaml
tracks:
  backend:
    modules: [backend]
    code_review_enabled: true   # 旧字段
    verify_enabled: true        # v3.4 新增（默认 true，向后兼容）
    gate_enabled: true          # v3.4 新增（默认 true，向后兼容）
```

propose 阶段：

| 开关 | tasks.md | manifest phase_prompts |
|------|----------|------------------------|
| `code_review_enabled=false` | 去除 `:review` 章节 | 不含 review sub |
| `verify_enabled=false` | 去除 `:verify` 章节 | 不含 verify sub |
| `gate_enabled=false` | 去除 `:gate` 章节 | 不含 gate sub |

**SSOT**：与 review 完全一致 —— orchestrator bootstrap 时从 `execution-manifest.yaml` 的 `phase_prompts` 是否含 `verify` / `gate` 派生 `TrackState.verify_enabled` / `gate_enabled`。

### Phase 数量约束

manifest 的 `phase_prompts` 允许 **2-5 个 sub**（test / dev 强必填，review/verify/gate 可选）：

| phase_prompts 组成 | 含义 |
|--------------------|------|
| test + dev + review + verify + gate（5） | 默认全开 |
| test + dev + review + verify（4） | gate_enabled=false |
| test + dev + review + gate（4） | verify_enabled=false |
| test + dev + review（3） | verify+gate 双关（**review 单独存在不是质量门**） |
| test + dev + verify（3） | review+gate 双关（保留质量门） |
| test + dev + gate（3） | review+verify 双关（保留质量门） |
| test + dev（2） | 三关（review+verify+gate）—— **必须 ≥1 个质量门（verify/gate）** |

**质量门强制**：validate-proposal.py 校验 manifest 必须含 `verify` 或 `gate` 至少一项。`review` 单独存在不算质量门 —— 静态审查不替代运行时验证。

### TrackState *  enabled 字段派生规则（v3.6 更新）

`TrackState` 三个 `*_enabled` 字段由 `bootstrap.py` 在 orchestrator 初始化时根据 `execution-manifest.yaml` 的 `phase_prompts` 派生：

| Track 类别 | `code_review_enabled` | `verify_enabled` | `gate_enabled` |
|------------|--------------------|----------------|--------------|
| standard（manifest 5 个 phase_prompts 全有） | True | True | True |
| standard（manifest 缺 `phase_prompts.review`） | False | True | True |
| standard（manifest 缺 `phase_prompts.verify`） | True | False | True |
| standard（manifest 缺 `phase_prompts.gate`） | True | True | False |
| simple（type=simple） | False | False | False |
| **scenario**（type=scenario，phase_prompts 只有 scenario-*） | **False** | **False** | **False** |
| 缺省（任何缺失字段） | True | True | True |

**SSOT 路径**：`execution-manifest.yaml` 的 `phase_prompts.{phase}` 是否存在（`bootstrap.py:294-323`）。

**回退**：
- v2.6 旧 snapshot 含 `code_review_enabled` 字段 → `TrackState.from_dict` 自动迁移
- v3.4 旧 snapshot 含 `verify_enabled` / `gate_enabled` → 同样自动迁移
- 任何缺失字段 → 默认 `True`（保守，触发更多质量门）

**v3.6 起 scenario track 派生**：`bootstrap.py:312-323` 显式 `replace gate_enabled=False` 等三个字段，
与 simple track 行为对称。这是 P0-1 修复的核心改动。

### reducer / detect 行为

关闭后的 phase 沿用 review 的 **silent-skip** 模式：

- **reducer**：`_handle_linear_phase`（test / dev 完成分支）通用 silent-skip 循环，任一被禁用的 phase（review / verify / gate）直接标记 completed 跳过，summary 写明 `<phase> disabled by manifest (no phase_prompts.<phase>)`
- **detect**：`next_pending` 在每条 SUB_PHASES 循环里检查 `track.{code_review,verify,gate}_enabled`，禁用则 continue

**fix 循环自洽**：verify 关闭 → verify.escalate 永不触发 → 无 fix 循环。gate 关闭 → gate.fail 永不触发 → 无 fix-gate 循环。这与 review 关闭后 review.escalate 不触发 fix-review 循环的行为对齐。

### simple track 行为

simple track 仍然三关全闭（orchestrator bootstrap 后 `code_review_enabled=False` 且 `verify_enabled=False` 且 `gate_enabled=False`），但这不影响 simple 走 `simple` phase 的派发路径。

### final-gate 不受影响

final-gate（跨 track 的最终 gate）始终派发，不在本开关范围内。final-gate 由 `detect.py:175` 的 `FINAL_GATE_TRACK` 分支硬派发，不读 track 开关。

### 关闭方式

- `track.verify_enabled: false` / `track.gate_enabled: false` — 关闭单个 track（propose 阶段生效，决定 manifest 是否含 verify/gate sub）
- simple track 自动跳过（同 v2.6）

### 测试覆盖

| 测试文件 | 覆盖 |
|----------|------|
| `scripts/tests/test_state_verify_gate.py` | TrackState verify_enabled / gate_enabled 字段、序列化、legacy 兼容 |
| `scripts/tests/test_detect_skip_disabled.py` | detect 跳过禁用 phase 的多种组合 |
| `scripts/tests/test_reducer_silent_skip.py` | reducer 通用 silent-skip |
| `scripts/tests/.../test_phase_gate_section.py` | propose 端 tasks.md + manifest + validator 联动 |

---

## v3.6 scenario track (real-integration E2E)

### 适用场景

scenario track 用于跨 backend/frontend/agent 的真实环境端到端验证，常见用例：

- 单一单元测试无法覆盖的多服务协作流
- 触达 pg-build 之外的"非功能性"验证（auth 流、菜单权限、UI 渲染链路）
- 黑盒场景测试（按 Gherkin 6 段结构 Given/When/Then/And/Evidence 写 Scenario）

### 与 standard track 的差异

| 维度 | standard | scenario |
|------|----------|----------|
| 阶段序列 | test → dev → review → verify → gate | scenario-prepare → scenario-execute |
| 单元测试 | TDD 红 phase 先写 | 无（场景即测试） |
| 修复循环 | dev / review / verify 各自有 fix 子 pipeline | 仅 scenario-fix（由 scenario-execute escalate 触发） |
| 代码审查 | review 阶段静态 review | 无 review phase（scenario 自身带 verify 语义） |
| 质量门 | gate + final-gate | scenario-execute PASS 即等价（v3.6 起自动 `gate_enabled=False`） |
| 失败重试 | sub-agent failed → `max_fail_retries` 计数 | sub-agent failed → `max_fail_retries` 计数（独立） |
| Sub-agent | test / dev / review / verify / gate / fix-* | scenario-prepare / scenario-execute / scenario-fix |

### 状态机

```
pending → scenario-prepare (running) → completed | failed
   ↓
scenario-execute (running) → completed | escalate | failed
   ↓
escalate → scenario-fix (子 pipeline) → completed | failed
   ↓
scenario-execute (重跑) → ...
   ↓
completed → track.status=completed → final-gate 豁免
```

### SSOT：scenario.yaml

`{change-dir}/scenario.yaml` 由 pg-propose 阶段生成，是 scenario-execute 的唯一输入。**禁止重写或修改**。
如需修改场景，须走 `pg-propose-refine` 流程回到 propose 阶段。

### final-gate 交互（v3.6 修复）

scenario track 的 manifest 显式 `gate_enabled=False`（v3.6 派生），
final-gate 前置门控 `_collect_missing_gate_assessments` 自动豁免。
编排器**不需要**为 scenario track 单独 dispatch gate phase。

兜底防线：`_collect_missing_gate_assessments` 还会检查 `_is_scenario_track` 和 `TrackState.gate_enabled=False`，
即使 bootstrap 漏设或外部修改 snapshot，scenario track 也不会被误报为缺 gate。

### 编排器注意

- scenario-execute 的 result.json 必含 `failed_scenarios` 列表（即使全 pass 也要空数组）
- scenario-fix 的 result.json 必含 `fix_cycle` 字段
- scenario-execute 多次重试时，`fix_cycle` 独立计数（不与 verify.fix_cycles 共享）
- scenario track 不参与 final-gate 评分（scenario-execute 通过即视为该 track 质量合格）

### 修复历史

v3.6 之前 scenario track 在 final-gate 前置门控被误报为缺 gate，触发 `workflow_failed (fatal=true)`。
本次修复采用 defense in depth：bootstrap 显式设 `gate_enabled=False` + 检查函数兜底。
详见 commit `c86c51c` 与 `b195a6f`。

---

## final-gate 前置门控协议

### 触发时机

final-gate dispatch 之前，runner 强制检查所有非 simple、非 scenario track 的 gate assessment 报告是否就绪。

### 检查逻辑（v2.7 + v3.6）

`_collect_missing_gate_assessments` 按以下顺序过滤：

1. **类型豁免**：`FINAL_GATE_TRACK` 自身、`_is_simple_track`、`_is_scenario_track` 全部跳过
2. **状态过滤**：`track.status != "completed"` 的 track 跳过（pipeline 还在跑）
3. **gate_enabled 信任**：`TrackState.gate_enabled=False` 跳过（v3.6 兜底防线）
4. **report_path 信任**：`snapshot.phases.gate.report_path` 指向真实文件 → 通过
5. **glob 退化**：匹配 `2-build/*{track_bare}-gate.md` 或 `*-{qualified_track}-gate.md`

通过所有 filter 仍未匹配的 track → 进入 `missing_gate_assessments` 列表。

### 阻断行为

`missing_gate_assessments` 非空时，runner 返回：

```json
{
  "action": "workflow_failed",
  "fatal": true,
  "reason": "final-gate 派遣前门控失败：以下 N 个 track 缺少 gate assessment 报告: ...",
  "missing_gate_assessments": ["<track-id>", ...]
}
```

**不** dispatch final-gate，`state.status` 设为 `failed`。

### 编排器遇到 workflow_failed 时的处理

| 场景 | 处理 |
|------|------|
| 某 standard track 确实漏跑 gate | 用 `pg-build/gate` agent 重跑该 track 的 gate phase |
| 某 track 是 simple/scenario（不该有 gate） | **不应发生**——v3.6 起 bootstrap 自动 gate_enabled=False |
| 报告文件被误删 | 重新跑该 track 的 gate phase |
| design 漏定义 gate phase | 走 pg-propose-refine 修改 design.md |

### 禁止绕过

**不要直接 patch `pipeline.snapshot.json` 伪造 gate 记录**。原因：
- 破坏 `event_log` + `snapshot` 一致性
- 后续 verify-and-merge 可能误判
- 隐藏真实问题，bug 累积后下次重跑仍会卡住

若必须紧急推进（例如生产事故），应：
1. 在 PR 描述里明确标注"snapshot patch 原因"
2. 同步 push 一条 `chore(<change>): emergency gate patch` commit
3. 提交后续 issue 跟踪根因修复

---

## v3.x 集成验证硬性约束

### 背景

当 `stages[*].environment.required=true` 时（如 `int` stage），该 stage 的集成验证 V-* 项**不允许 SKIP**。环境启动失败必须修复而非跳过。

### 约束规则

| 字段 | 行为 | 实现 |
|------|------|------|
| `required=true` | 集成验证不可 SKIP；服务必须启动 | verify agent 协议 + verify_mandatory.yaml 块 |
| `required=false` | 允许 SKIP 豁免 | 当前行为 |
| 缺省 | 视作 false | 保守 |

### 启动失败处理

1. 第 1-2 次失败：检查启动日志，尝试修复后再启动
2. 第 3 次失败：`verify` agent 返回 `status: "fail"`，错误码 `ENV_STARTUP_RETRY_EXCEEDED`
3. 编排器收到 `status: "fail"` → `workflow_failed` → 终止 pipeline

### V-* 失败处理

`required=true` 时 V-* 项发现代码 bug → **立即 escalate**（不 accumulate）。每次 escalate 触发一次 fix cycle，不浪费修复效率。

### 实现位置

- verify agent 协议：`opencode/agents/pg-build/verify.md` — §2 / §4.1
- verify_mandatory 块：`prompt-templates/blocks/verify_mandatory.yaml`
- renderer 注入：`scripts/template_engine/renderer.py` — §L199 / §L230

## v3.x Design Drift 协议

### 背景

build 过程中（如 scenario-fix 阶段）可能发现设计文档（design.md）未声明的契约、字段或行为。
**不修改 design.md**（build 流中 design 是 SSOT），而是记录到 `drift.md` 供审计回溯。

### 触发条件

`scenario-fix` agent 在修复过程中发现以下情况之一时，应记录 design drift：

- 设计文档未声明但代码必须实现的 API 字段
- 设计文档未覆盖的边界条件
- 设计文档与实现之间的语义偏差

### 协议流程

1. **scenario-fix agent 检测**：在修复过程中发现 design 偏移
2. **记录到 result.json**：调用 `pg-build-result` 时传 `--design-drift '<json>'`
3. **reducer 处理**：`PipelineRecord.design_drift` 字段在 record 事件中保留
4. **orchestrator I/O**：`record()` 方法调用 `_accumulate_design_drift` 写入 `drift.md`
5. **archive 迁移**：`bootstrap.py` 的 archive 逻辑自动将 `drift.md` 随 change 目录一起归档

### drift.md 格式

纯 Markdown，仅人工审计使用，不做程序化处理：

```markdown
# Design Drift Log

## Drift #1
- **发现阶段**: scenario-fix
- **场景**: S-create-with-custom-cidr
- **位置**: ProjectResponse 缺 network 字段
- **原因**: design.md 仅声明了 Request 含 network，但未声明 Response 也包含
- **决策**: ACCEPT
```

### 路径规则

- build 期间生成 → `{change-dir}/drift.md`
- archive 后追加 → `{archive-dir}/drift.md`
- orchestrator 自动按时机选路径

### 实现位置

- `design_drift` 字段：`scripts/pipeline/events.py` — `PipelineRecord.design_drift`
- 累积函数：`scripts/pipeline/reducer.py` — `_accumulate_design_drift`
- 写入触发：`scripts/pipeline/orchestrator.py` — `record()` 方法
- CLI 扩展：`scripts/pg-build-result` — `--design-drift` 参数
- agent 协议：`opencode/agents/pg-build/scenario-fix.md` — §Step 3.5

## final-gate 派发条件

`final-gate` 是跨 track 的最终质量门，**但并非所有 pipeline 都会派发**。

### 派发 vs 跳过的判定矩阵

| 条件 | 行为 | 编排器下一步 |
|------|------|-------------|
| 至少一个 standard track 处于非 completed 状态 | detect 返回该 track 的 dispatch | 继续跑当前 track |
| 所有 track（standard + simple + scenario）均 `status=completed` | **不派发**，detect.py:39 `state.status == "completed"` 直接返回 `done` | 直接触发 pg-verify-and-merge |
| standard track gate 缺失（`missing_gate_assessments` 非空） | 不派发，返回 `workflow_failed` | 按 §final-gate 前置门控协议 处理 |
| scenario track 无 gate phase | 不派发（v3.6 起 `gate_enabled=False`，豁免） | 直接进入下一步 |
| 当前处于 fix 子 pipeline 活跃状态 | 不派发，dispatch 子 pipeline 的当前 phase | 继续跑 fix 循环 |

### 编排器注意

- 当所有 track 完成后直接拿到 `{"action": "done"}` 是**正常行为**，不是 bug
- **不要尝试强制派发 final-gate**（如直接修改 state 或重复调用 `next`）
- 直接触发 pg-verify-and-merge 即可
- 若 `done` 出现但你怀疑漏了 final-gate，检查：
  - `state.status` 是否真的是 `completed`（而非 `failed`/`running`）
  - 是否有未完成 track 隐藏在子 pipeline 里
  - `progress <change>` 输出应显示 `event_count > 0` 且 `tracks.*.status == "completed"`

### 设计意图

final-gate 仅在 cross-track 质量审视有实际意义时派发：
- gate 缺失需要补跑
- 跨 track 一致性需要 review
- 评分聚合需要 cross-track 视角

正常 case（所有 track gate pass）下，single-track gate 已经覆盖质量门，final-gate 是冗余的。
detect.py 的"先 completed 终态判断、再 final-gate"设计反映了此意图。

---

## v2.5 record 命令支持 --result-json

### 背景

v2.4 强制 sub-agent 把 result.json 落盘后，编排器在调 `runner record` 时仍要把 7 字段（status/summary/report/outputs/issues/evidence/tasks_updated）**逐个**通过 CLI flag 重传——LLM 在长 prompt 下极易漏传 `--tasks-updated` 等关键字段，触发 schema_violation。

v2.5 起，record 命令新增 `--result-json <path>` 可选参数，**编排器只需传 result.json 路径，7 字段自动从文件加载**：

```bash
# v2.5 推荐用法（编排器侧最少传参）
$RUNNER record <change> --result-json <result.json 绝对路径>

# 也可与显式 CLI 参数混用：CLI 非空值优先于文件内容
$RUNNER record <change> --result-json <abs/006-dev.backend-dev-result.json> \
                       --status pass        # 显式覆盖文件中的 status

# 完全兼容 v2.4 调用形式（不传 --result-json 时行为完全一致）
$RUNNER record <change> --status completed --summary "..." --report ... --tasks-updated 2.1,2.3 ...
```

### 字段映射（result.json → record CLI）

result.json 文件由 `pg-build-result --mode agent --output-path <path>` 生成，文件顶层 dict 的 7 个 key 与 record CLI 一一对应：

| result.json key | record CLI 参数 | 合并策略 |
|---|---|---|
| `status` | `--status` | CLI 非空 > 文件 |
| `summary` | `--summary` | CLI 非空 > 文件 |
| `report_path` | `--report` | CLI 非空 > 文件 |
| `outputs` | `--outputs` | CLI 非空 > 文件 |
| `issues` | `--issues` | CLI 非空 > 文件 |
| `evidence_paths` | `--evidence` | CLI list + 文件 list 拼接去空 |
| `tasks_updated` | `--tasks-updated` | CLI list + 文件 list 拼接去空（CLI 已先做逗号归一） |

### 新增 fatal 错误

| 触发条件 | reason | 修复 |
|---|---|---|
| `--result-json` 路径不存在 | `result_json_missing: ...` | 用 dispatch action 中的 `expected_result_path`；或改回显式 CLI 形式 |
| `--result-json` 内容非合法 JSON | `result_json_invalid: ...` | 重新让 sub-agent 跑 `pg-build-result` 落盘 |
| `--result-json` 顶层不是 dict | `result_json_invalid: ...` | 检查文件来源（pg-build-result 输出顶层就是 object） |
| `--result-json` 缺 status 且 CLI 也未传 `--status` | `status_missing: ...` | 二选一必填 |

### 与 v2.4 兼容

- 不传 `--result-json` 时，record 命令行为与 v2.4 完全一致（回归保护）
- sub-agent 侧的 `pg-build-result --output-path --require-output` 落盘要求不变
- orchestrator.py 内部的 `result_json_missing_after_retry` fatal（基于 `expected_result_path`）依然独立生效，不受 CLI `--result-json` 影响

### 完整代码参考

- **CLI 入口**: `scripts/pg-pipeline-runner.py` (record 分支：`--result-json` + 7 字段合并)
- **测试**: `scripts/tests/test_record_result_json.py` (6 个 case)

---

## v2.7 design.md 缺陷协议

### 触发条件

fix-review agent 在 source review 报告中识别到 R-* 项的根因位于 **设计层**（design.md / tasks.md 文档错误），而非代码实现错误。

典型场景：
- proto 字段编号已被占用（design.md 与代码实际占用冲突）
- API 契约与物理约束矛盾（如 size 字段类型不匹配实际存储）
- tasks.md 任务编号与 design.md 章节引用不一致

### 协议流程

1. **fix-review agent 检测**（在执行代码修复前）
   - 标注 `status: failed`
   - 在 result.json 写入 `design_md_fault: true`
   - 写入 `design_md_fault_location: "<file>:<line>"`

2. **reducer 响应**（`_handle_fix_review`）
   - 检测 `design_md_fault == True`
   - 立即触发 `workflow_failed` action
   - `reason` 包含 fault location + 修复指引

3. **编排器响应**
   - 收到 `action: workflow_failed, fatal: true`
   - 展示 reason 给用户
   - 提示运行 `pg-propose-refine` 修复 design.md
   - 用户修正后重新触发 pg-build

### 与 review-fix 循环的关系

`design_md_fault` 路径**完全跳过** review 重审与 `max_review_fix_retries` 计数
（design.md bug 是客观文档错误，review agent 复核无意义）。

### 重置方式

修正 design.md 后，用户重跑 `pg-propose-refine` → `pg-build`，无需手动重置 pipeline state。

### 相关代码位置

| 组件 | 文件 | 行号 |
|------|------|------|
| `PipelineRecord.design_md_fault` 字段 | `scripts/pipeline/events.py` | 67 |
| `_handle_fix_review` 检测逻辑 | `scripts/pipeline/reducer.py` | 558 |
| fix-review agent prompt 模板 | `prompt-templates/fix-review.yaml` | 13-28 |
| `pg-build-result --design-md-fault` CLI | `scripts/pg-build-result` | 86-91 |
| `pg-pipeline-runner.py record` CLI | `scripts/pg-pipeline-runner.py` | 170-175 |
