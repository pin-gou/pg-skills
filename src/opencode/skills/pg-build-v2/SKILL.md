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

---

## 架构概览

```
event_log (append-only JSONL)  ← 唯一持久化入口
    │
    ▼
reduce_state(pure function)    ← 状态转换（无 I/O）
    │
    ▼
PipelineAction                  ← 下一步动作（dispatch / advance / done / failed）
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
| 审计 | ~~context-chain.md 人读~~ (v2.1 弃用) | event log 可编程回放 |

---

## CLI 用法

```bash
RUNNER="python3 .opencode/skills/pg-build-v2/scripts/pg-pipeline-runner.py"

# 执行 5 步 bootstrap（首次）：migrate / branch / init-commit / prepare_env
$RUNNER bootstrap <change>

# 获取下一步 action
$RUNNER next <change>

# 记录 sub-agent 结果
$RUNNER record <change> <status> [report_path] [summary] [outputs] [issues]

# 查看进度
$RUNNER progress <change>

# 执行 env hook（prepare_env / clean_env），由编排器在收到 env_switch 时调用
$RUNNER env-action <change> <phase> <stage> <env>
```

**status**: `completed | failed | escalate | pass | fail`

**env-action phase**: `prepare_env | clean_env`

---

## 编排器执行协议

### 主循环

编排器（调用 SKILL 的 LLM）通过调用 runner CLI 实现 pipeline 推进，每一步都必须遵守以下协议：

```
循环:
  0. [首次] $RUNNER bootstrap <change> (600s timeout)

  1. 调 `next <change>` (30s timeout) → 检查 action 字段
  2. switch(action):
       "env_switch"        → $RUNNER env-action <change> <phase> <stage> <env_name>
                             (timeout=hook_timeout_seconds, 由 action 返回)
                             失败 → 提示用户修复环境或终止
                             成功 → 回步骤 1

       "dispatch"          → 派遣 sub-agent (见下方协议)
       "dispatch_final_gate" → 与 dispatch 等价，但 item="final-gate"。派遣 gate agent
                               执行跨 track 依赖审查。**注意**：与 "dispatch" 共享派遣协议，
                               区别仅在于 dispatch_file 路径与 agent 名。
                               派遣成功后编排器仍按正常 record 协议处理结果。
       "advance"           → 回步骤 1 (调 next)
       "done"              → 检查 result.next_action:
                               - "verify_and_merge" → 加载 pg-verify-and-merge skill，按 PHASE 0-4 执行
                               - 无此字段 → pipeline 完成，终止
       "workflow_failed"   → pipeline 失败, 终止
```

**action 字段完整清单**（防漏）：

| action | 触发时机 | 编排器响应 |
|--------|---------|-----------|
| `env_hook_failed` | bootstrap / env-action 中 hook 脚本失败 | 终止循环，提示修复 |
| `env_switch` | stage 边界切换 | 编排器调 `$RUNNER env-action <change> <phase> <stage> <env_name>` 并设 timeout=`hook_timeout_seconds`，完成后回步骤 1 |
| `dispatch` | sub-agent 派遣（普通 track） | 派遣对应 agent |
| `dispatch_final_gate` | final-gate 阶段派遣 | 派遣 pg-build/gate agent |
| `advance` | 当前 step 完成，继续推进 | 自动调 next |
| `done` | pipeline 全部完成 | 触发 verify-and-merge |
| `workflow_failed` | 重试耗尽 / fatal | 终止循环 |

pipeline 完成时 runner 返回的 `done` action 还包含以下字段，供编排器在 verify-and-merge 阶段使用：

```json
{
  "action": "done",
  "status": "completed",
  "next_action": "verify_and_merge",
  "affected_tracks": ["backend", "frontend"],
  "archive": {"ok": true, "target": ".pg/changes/archive/2026-07-01-xxx"}
}
```

### verify-and-merge 集成

当 `result.next_action == "verify_and_merge"` 时，编排器按以下流程执行：

1. **加载 skill**：`skill("pg-verify-and-merge")`
2. **Setup**：`mkdir -p temp && python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-verify-and-merge --change-dir ".pg/changes/archive/<date>-<change>" > temp/vm-context.json`
3. **Phase 0**：在 feature branch 上运行 flyway renumber + 受影响 track lint → 提交并推送
4. **Phase 1**：切换到 default_branch，`git merge --squash` feature branch

   > ### 编排期间 commit 策略（噪音 commit 并非 bug）
   >
   > 编排每一步 `record` 都会 `git commit --all` 产生 `chore(<change>): auto-record <track>:<phase> <status>`。
   > 这些 commit 作为 **编排期的原子化审计痕迹**，让每一步修改都可追溯。
   > 最终 `pg-verify-and-merge` Phase 1 的 `git merge --squash` 会将所有编排期 commit 压平为单条 `feat(…): …` commit 进入 master。
   > **master 历史零噪音**——squash 合并自动消除中间 commit。

5. **Phase 1.5**：判定是否跳过测试（无冲突 + `skip_tests_if_no_conflict=true` 时跳过）
6. **Phase 2**：按 affected_tracks 运行测试套件
7. **Phase 3**：git commit（squash 合并） + git push
8. **Phase 4**：清理（提示删除 feature branch）

具体执行细节（envSetup / verifySetup / outputFormat 推断等）遵循 `pg-verify-and-merge` SKILL 的 phase 定义。

### 环境准备验证

`bootstrap` 和 `env-action` 是独立 CLI 命令，编排器自行控制 bash timeout：

- **首次**：编排器调用 `$RUNNER bootstrap <change>`（600s timeout），执行 migrate / feature branch / init commit / prepare_env
- `bootstrap` 返回 `ok=false` 时：环境准备失败，编排器必须终止循环并提示用户修复环境
- `bootstrap` 返回 `ok=true` 时：编排器开始正常 `next` → `dispatch` → `record` 循环

### 多 Stage 环境切换

当 pipeline 包含多个 stage 且 stage 间使用不同环境时，runner 自动检测 stage 边界：

```
pipeline_order = ["dev.backend", "dev.frontend", "integration.backend"]
                         ↑ stage 边界 ↑
```

`detect.py:next_pending()` 返回 `env_switch` action，编排器自行调用：

```bash
$RUNNER env-action <change> prepare_env <stage> <env_name>
```

`env_switch` action 包含 `hook_timeout_seconds` 字段，编排器应将其作为 bash timeout 值。

**编排器 vs 旧架构的责任转移**：

| 责任 | 旧架构（orchestrator 内联） | 新架构（编排器控制） |
|------|---------------------------|-------------------|
| bash timeout 控制 | runner 无法控制 | 编排器设 `hook_timeout_seconds` |
| hook 失败重试 | 隐式在 `next` 内 | 编排器显式处理 |
| stage_prepared 更新 | orchestrator 内部 | 编排器在 `env-action` 成功后调 `next`，由 _first_next 自动设置 |

### Dispatch 派遣协议（重要）

runner 返回 dispatch action 时，携带字段：

```json
{
  "action": "dispatch",
  "item": "dev.backend",
  "sub": "test",
  "agent": "pg-build/test",
  "dispatch_file": ".pg/changes/<change>/2-build/dev.backend-test-dispatch.md"
}
```

**编排器必须遵守以下规则**：

1. **绝不读取 dispatch_file 内容进行加工**。runner 的模板引擎已生成完整提示词，编排器任何二次加工（摘要、重写、翻译、合并上下文）都会引入 LLM 间差异和内容漂移。
2. **只告诉 sub-agent dispatch 文件路径**，让 sub-agent 自己读取。
3. **正确用法**：
   ```
   task(prompt="你的任务指令在 {dispatch_file} 中，请读取该文件并严格按指示执行。
              完成后返回 { summary, outputs, tasks_updated, status }")
   ```
4. **错误用法（已禁止）**：
   ```
   task(prompt="...我读了文件内容后为你总结如下...请做XYZ...")  ← 禁止
   ```

Dispatch 文件路径始终是 `dispatch_file` 字段的值（绝对值或相对于项目根）。

### Record 协议

sub-agent 完成后，编排器调 `record` 记录结果：

```bash
RUNNER="python3 .opencode/skills/pg-build-v2/scripts/pg-pipeline-runner.py"
$RUNNER record <change> <status> [report_path] [summary] [outputs] [issues]
```

- `<status>` 必须从 Record 状态守卫表选择（见下文）
- `[report_path]`：sub-agent 输出的验证/审查报告路径
- `[summary]`：一句话摘要
- `[outputs]`：产物文件列表（逗号分隔）
- `[issues]`：问题列表（逗号分隔，仅 gate 提交 gap 时用）

Record 完成后 runner 自动执行 `next` 推进 pipeline，返回下一步 action。
编排器回归到步骤 1。

### Sub-agent 返回契约（强制）

**所有 sub-agent 必须**返回以下 JSON 结构（缺一不可）。编排器在收到 record 前会先校验 schema：

```json
{
  "summary": "<= 200 字字符串",
  "outputs": ["<产物文件绝对路径>", ...],
  "tasks_updated": ["<tasks.md 中的 task_id>", ...],
  "status": "completed | failed | escalate | pass | fail",
  "evidence_paths": ["<证据文件绝对路径>", ...],
  "report_path": "<必须存在且可读>"
}
```

**字段约束**：

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|---------|
| `summary` | str | ✅ | 长度 1-200 |
| `outputs` | list[str] | ✅ | 文件必须存在 |
| `tasks_updated` | list[str] | ✅ | 元素为字符串，可为空 |
| `status` | str | ✅ | 见 Record 状态守卫表 |
| `evidence_paths` | list[str] | ✅ | 每个路径必须存在 |
| `report_path` | str | ✅ | 文件必须存在 |

**verify/gate agent 额外要求**：
- `evidence_paths` 不能为空数组（空 = 触发 hard fail，详见 reducer 错误协议）
- `report_path` 必须存在
- `report_path` 文件内容首行必须包含 "PASS" 或 "FAIL" 标记

### 常见错误排查

| 现象 | 原因 | 修复 |
|------|------|------|
| `action: error` + `No active item` | 连续两次 record 未调 next | 每次 record 后调 next 获取下一步 |
| `action: error` + `invalid transition` | record status 用错 | 对照 Record 状态守卫表 |
| sub-agent 告"任务不完整" | dispatch 文件没传或路径错 | 检查 `dispatch_file` 路径是否可读 |
| send sub-agent 后返回格式不对 | 未约束 sub-agent 返回格式 | 在 prompt 中明确要求返回 JSON |
| `action: error` + `schema_violation` | sub-agent 返回 JSON 缺少必填字段或类型错误 | 重新派遣，prompt 显式引用 Sub-agent 返回契约 |
| `action: error` + `evidence_missing` | verify/gate 的 `evidence_paths` 为空 | 重跑验证，prompt 强调必须产出 evidence |
| `action: error` + `report_missing` | `report_path` 指向的文件不存在 | 让 sub-agent 实际写盘后再 record |

### Evidence 强制规则（v2.1 引入）

verify 和 gate agent 在 record 时若 `evidence_paths` 为空数组，reducer 直接返回
`{"action": "error", "reason": "evidence_missing", "fatal": True}`，跳过状态推进，
编排器必须终止循环并要求 sub-agent 补交 evidence。这是 hard fail，无 skip 选项。

## Event Schema

所有 event 写入 `{change}/2-build/pipeline.events`，JSONL 格式。

| Event type | 触发时机 | data 关键字段 |
|---|---|---|
| `pipeline_started` | 首次 next | change, pipeline_order |
| `bootstrap_step_completed` | bootstrap 子步 | step, detail |
| `prepare_env_started/completed` | env-hook（bootstrap 或 stage 切换） | env_name, exit_code, log_path |
| `clean_env_started/completed` | stage 切换时清理环境 | env_name, exit_code, log_path |
| `dispatch_started` | 派送 sub-agent | track, phase, agent, attempt |
| `record_received` | LLM 调 record | track, phase, status, summary, report_path |
| `fix_cycle_started` | verify escalate | track, cycle, source_report |
| `gate_cycle_started` | gate fail | track, cycle, cycles_remaining |
| `track_completed` | gate pass / exhausted | track, status |
| `pipeline_completed` | final-gate pass | final_status |
| `workflow_failed` | fatal | reason |

---

## Record 状态守卫

reducer match 穷举了所有 `(phase, status)` 组合，无效组合返回 `error` action（不污染 state）。

| sub | 允许 status |
|-----|------------|
| test/dev/simple | completed, failed |
| verify | completed, escalate, failed |
| fix/fix-gate | completed, failed |
| gate | pass, fail |
| final-gate | pass, fail |

---

## 子 Pipeline 机制

fix 循环 / gate-fix 循环不再使用 `in_fix_cycle` 状态 flag，
而是创建 SubPipeline 对象递归复用 reducer：

- **fix 循环**：`verify escalate` → SubPipeline(fix, verify) → fix → verify → 回到主 pipeline
- **gate-fix 循环**：`gate fail` → SubPipeline(fix-gate, verify, gate) → fix-gate → verify → gate → 回到主 pipeline

耗尽规则：
- fix 循环 x > 4 → 强制 gate
- gate-fix 循环 x > 2 → 接受 gap 到 known-issues.md，track 完成

---

## 故障排查

| 错误 | 原因 | 修复 |
|------|------|------|
| `action: error` + `reason: No active item` | 未先调 `next` | 先 `next` 再 `record` |
| `action: error` + `reason: invalid transition` | 用了错误的 record status | 检查 record 状态守卫表 |
| `action: env_hook_failed` | prepare_env 脚本失败 | 检查日志 fix env 后重试 |
| `action: workflow_failed` | 重试耗尽 / final-gate fail | 查看 pipeline.events 最后几条 |

---

## 查看器数据接口

event log 的格式设计为查看器的直接数据源：

- **实时**：`tail -f pipeline.events` → WebSocket/SSE
- **回放**：`event_log.replay()` 按时间线渲染
- **中间产物**：每个 `dispatch_started` event 带 `dispatch_file` / `report_path`
- **数据契约**：event schema 版本化（`schema_version` 字段），向后兼容

---

## V1 兼容脚本

```bash
python3 .opencode/skills/pg-build-v2/scripts/migrations/v1_to_events.py <change_root>
```

从旧 `.pipeline-state.json` 重建 `pipeline.events` + `pipeline.snapshot.json`
（v2.1 起 context-chain.md 已弃用，v1 兼容脚本从 dispatch_history + completed_items 重建，无需 context-chain.md 数据）。
旧文件不会被修改。