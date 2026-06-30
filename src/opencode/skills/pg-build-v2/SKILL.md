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
| 审计 | context-chain.md 人读 | event log 可编程回放 |

---

## CLI 用法

```bash
RUNNER="python3 .opencode/skills/pg-build-v2/scripts/pg-pipeline-runner.py"

# 获取下一步 action
$RUNNER next <change>

# 记录 sub-agent 结果
$RUNNER record <change> <status> [report_path] [summary] [outputs] [issues]

# 查看进度
$RUNNER progress <change>
```

**status**: `completed | failed | escalate | pass | fail`

---

## Event Schema

所有 event 写入 `{change}/2-build/pipeline.events`，JSONL 格式。

| Event type | 触发时机 | data 关键字段 |
|---|---|---|
| `pipeline_started` | 首次 next | change, pipeline_order |
| `bootstrap_step_completed` | bootstrap 子步 | step, detail |
| `prepare_env_started/completed` | env-hook | env_name, exit_code, log_path |
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

从旧 `.pipeline-state.json` + `context-chain.md` 重建 `pipeline.events` + `pipeline.snapshot.json`。
旧文件不会被修改。