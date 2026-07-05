# pg-build 实施计划

> **目标**：把当前 `pg-pipeline-runner.py` (4637 LOC, 96 函数, 305 分支, 51 散落 save_state) 重写为事件驱动的 pipeline 引擎（~1000 LOC），同时为未来流程查看器/审计工具提供数据契约。

---

## 0. 关键设计决定（先对齐再实施）

| # | 决定 | 反对方案（已否决） |
|---|------|-----------------|
| D1 | **单一 append-only event log**（`pipeline.events` JSONL）作为状态唯一源 | 保留 mutable `.pipeline-state.json` |
| D2 | **Reducer 纯函数**做状态转换（无 I/O） | 过程式状态机 + 散落 save_state |
| D3 | **删除 v1 路径**（不再维护 `_legacy_cmd_next`） | 保留 v1/v2 双轨 |
| D4 | **Prompt 模板拆到 YAML 文件** | 继续嵌入 `.py` 字符串常量 |
| D5 | **子 pipeline 复用 reducer**（fix / gate-fix 递归） | `in_fix_cycle` 状态 flag + 散落计数 |
| D6 | **报告命名改用时间戳** | 全局递增 seq 编号 |
| D7 | **保留 `execution-manifest.yaml` + `project.yaml` 输入不变** | 改动 pg-propose 衔接 |
| D8 | **保留 `tasks.md` checkbox 作为派生视图** | 完全删除 tasks.md |

---

## 1. 目录结构（最终落地形态）

```
.pg/skills/src/opencode/skills/pg-build/
├── SKILL.md                       # 替换旧 SKILL.md（精简到 ~250 行）
├── plan.md                        # 本文件
├── scripts/
│   ├── __init__.py
│   ├── pg-pipeline-runner.py      # CLI 入口，~150 LOC（仅 main + 参数解析 + 委托）
│   ├── bootstrap.py               # 启动 4 步（migrate / context-chain / branch / init-commit）
│   ├── event_log.py               # append-only 读写 + replay + tail
│   ├── snapshot.py                # 最新快照（由 event log 重建）
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # next() / record() / progress() 编排循环
│   │   ├── reducer.py             # 纯函数 reduce_state
│   │   ├── state.py               # PipelineState 数据类（frozen dataclass）
│   │   ├── events.py              # PipelineRecord / PipelineAction dataclass
│   │   ├── detect.py              # next_pending() 纯函数
│   │   ├── sub_pipeline.py        # SubPipeline 类（递归子 pipeline）
│   │   └── dispatch.py            # 构建 action JSON + 写 dispatch_file
│   ├── template_engine/
│   │   ├── __init__.py
│   │   ├── renderer.py            # 加载 YAML 模板 → Jinja2 渲染
│   │   └── manifest.py            # 读 execution-manifest.yaml
│   ├── context_chain.py           # 写 context-chain.md（仅供 audit，主要信息进 event log）
│   └── tasks_md.py                # 任务复选框同步（pipeline_mark 替代品）
└── prompt-templates/
    ├── base.yaml
    ├── blocks/
    │   ├── hooks.yaml             # invoke-hook 调用约定
    │   ├── rollback.yaml          # [ROLLBACK CONTEXT] 块
    │   └── tasks.yaml             # tasks_preformatted 渲染
    ├── test.yaml
    ├── dev.yaml
    ├── verify.yaml
    ├── gate.yaml
    ├── fix.yaml
    ├── fix-gate.yaml
    ├── simple.yaml
    └── final-gate.yaml
```

**测试目录**：

```
pg-build/scripts/tests/
├── __init__.py
├── test_event_log.py              # append-only 写入 + replay + tail 正确性
├── test_snapshot.py               # 快照重建一致性
├── test_reducer.py                # 所有 (phase, status) 组合的 reducer 行为
├── test_detect.py                 # next_pending 在各种 state 下的输出
├── test_sub_pipeline.py           # 子 pipeline 创建 / 完成 / 嵌套
├── test_orchestrator.py           # next / record / progress 集成测试
├── test_renderer.py               # 模板渲染产物正确性
├── test_prompt_template.py        # 旧 prompt 字段在新模板中仍可用
├── test_integration_tdvg.py       # 完整 track 跑通 test→dev→verify→gate
├── test_integration_fix.py        # verify escalate → fix → verify 循环
├── test_integration_gate_fix.py   # gate fail → fix-gate → gate 循环
├── test_integration_final.py      # final-gate 单次派遣 + archive
├── test_integration_simple.py     # simple track 路由
├── test_replay_v1.py              # 旧 v1 .pipeline-state.json → 新 event log 回放
└── fixtures/
    ├── v1_sessions/               # 真实旧 session 快照
    └── manifests/                 # 各场景的 execution-manifest.yaml
```

---

## 2. 数据契约：Event Log Schema

**位置**：`{change}/2-build/pipeline.events`（JSONL 格式，每行一个 event）

### Event 通用结构

```json
{
  "ts": "2026-06-30T10:30:00+08:00",
  "type": "dispatch_started",
  "data": { /* 类型相关字段 */ },
  "snapshot_after": { /* 该时刻的完整 state（可选，供查看器快速渲染） */ }
}
```

### Event 类型清单

| Event type | 触发时机 | data 字段 |
|---|---|---|
| `pipeline_started` | 首次 `next`（bootstrap 后） | `change`, `pipeline_order[]`, `manifest_ref` |
| `bootstrap_step_completed` | bootstrap 每个子步 | `step`, `ok`, `detail` |
| `prepare_env_started` | env-hook 启动 | `stage`, `env_name`, `script_path`, `log_path` |
| `prepare_env_completed` | env-hook 完成 | `stage`, `exit_code`, `log_path`, `duration_seconds` |
| `clean_env_started` | clean_env 启动 | `stage`, `env_name` |
| `clean_env_completed` | clean_env 完成 | `stage`, `exit_code` |
| `dispatch_started` | 每次派送 sub-agent | `track`, `phase`, `agent`, `attempt`, `cycle`, `dispatch_file` |
| `record_received` | LLM 调 `record` | `track`, `phase`, `status`, `summary`, `report_path`, `issues` |
| `fix_cycle_started` | verify escalate | `track`, `cycle`, `verify_report_path` |
| `gate_cycle_started` | gate fail | `track`, `cycle`, `gate_report_path`, `cycles_remaining` |
| `sub_pipeline_completed` | 子 pipeline 完成 | `sub_pipeline_id`, `result` |
| `track_completed` | gate pass / 耗尽接受 gap | `track`, `status` (pass/exhausted), `accepted_gaps[]` |
| `pipeline_completed` | final-gate pass | `final_status`, `archive_path`, `duration_seconds` |
| `workflow_failed` | fatal | `reason`, `failed_at`, `last_event` |
| `git_commit` | init / record / archive | `sha`, `message`, `branch` |

### State 快照结构

`snapshot_after` 字段记录 reducer 处理当前 event 后的完整 state：

```json
{
  "pipeline": {
    "status": "running|completed|failed",
    "current_track": "dev.backend",
    "current_phase": "verify",
    "current_cycle": 1,
    "feature_branch": "feat/pg/add-host-list-export"
  },
  "tracks": {
    "dev.backend": {
      "status": "running",
      "phases": {
        "test":    { "status": "completed", "attempt": 1, "report_path": "...", "completed_at": "..." },
        "dev":     { "status": "completed", "attempt": 1, "report_path": "...", "completed_at": "..." },
        "verify":  { "status": "running",   "attempt": 1, "cycles": [], "fix_cycles": [] },
        "gate":    { "status": "pending",   "gate_cycles": [] }
      },
      "sub_pipelines": [
        { "id": "dev.backend.fix-1", "status": "pending", "phases": ["fix", "verify"] }
      ]
    }
  },
  "context": {
    "init_committed": true,
    "init_commit_sha": "abc1234",
    "manifest_ref": "execution-manifest.yaml"
  }
}
```

---

## 3. 实施阶段

### Phase 1: 数据层（无业务逻辑）

**目标**：实现 event log、snapshot、state dataclass，可独立测试。

**交付**：
- `event_log.py`：append / replay / tail / as_sse_stream 方法
- `snapshot.py`：rebuild_from_events / save / load
- `pipeline/state.py`：`PipelineState` frozen dataclass + `from_dict()` / `to_dict()`
- `pipeline/events.py`：所有 Event 类型 dataclass

**测试**：
- `test_event_log.py`：append 后 replay 完整、tail 正确倒读 N 条、并发写入安全
- `test_snapshot.py`：state 字典 ↔ dataclass ↔ 重建一致性
- 用 `pytest --cov=scripts/event_log --cov-fail-under=90` 验证覆盖率

**完成定义**：
- [ ] `python3 -m pytest tests/test_event_log.py tests/test_snapshot.py` 全绿
- [ ] 覆盖率 ≥ 90%
- [ ] 对照 `pg_pipeline_state_v2._empty_state()` 字段一致

---

### Phase 2: Reducer + Detect（纯函数）

**目标**：实现 reducer match 块和 next_pending 函数。

**交付**：
- `pipeline/reducer.py`：`reduce_state(state, event) -> (state, action)` 纯函数
- `pipeline/detect.py`：`next_pending(state) -> NextAction` 纯函数
- `pipeline/sub_pipeline.py`：`SubPipeline` dataclass + 创建/完成方法

**关键 match 分支**（reducer 必须覆盖以下所有组合）：

```python
def reduce_state(state: PipelineState, record: PipelineRecord) -> tuple[PipelineState, PipelineAction]:
    match (record.phase, record.status):
        # test/dev/verify 失败 → 重试或 workflow_failed
        case (phase, "failed") if phase in ("test", "dev"):
            ...
        case ("verify", "failed"):
            ...

        # 正常推进
        case ("test",    "completed"):  return advance_to(state, "dev")
        case ("dev",     "completed"):  return advance_to(state, "verify")
        case ("verify",  "completed"):  return advance_to(state, "gate")
        case ("gate",    "pass"):       return mark_track_done(state)

        # fix 循环
        case ("verify", "escalate"):
            if cycles >= MAX_FIX_CYCLES:
                return advance_to(state, "gate", force=True)  # 强制走 gate
            return create_sub_pipeline(state, "fix", cycle=cycles+1)

        # 子 pipeline 完成 → 回到主 pipeline
        case ("fix", "completed"):
            return advance_to(state, "verify", resume=True)
        case ("fix-gate", "completed"):
            return advance_to(state, "gate", resume=True)

        # gate 失败 → fix-gate 子 pipeline 或耗尽
        case ("gate", "fail"):
            if gate_cycles >= MAX_GATE_FIX:
                return mark_track_done(state, status="exhausted")
            return create_sub_pipeline(state, "fix-gate", cycle=gate_cycles+1)

        # final-gate
        case ("final-gate", "completed"):
            return mark_pipeline_done(state)  # 等同 pass
        case ("final-gate", "pass"):
            return mark_pipeline_done(state)
        case ("final-gate", "fail"):
            return mark_pipeline_failed(state)

        case _:
            return error_action(f"invalid transition: {record.phase} + {record.status}")
```

**测试**：
- `test_reducer.py`：所有 (phase, status) 组合的 reducer 行为 + 边界（cycle 超限 / 子 pipeline 嵌套）
- `test_detect.py`：state 输入 → next_action 输出（含 resume 逻辑、simple track 短路、final-gate 特殊）
- `test_sub_pipeline.py`：子 pipeline 创建、推进、嵌套完成

**完成定义**：
- [ ] match 分支覆盖所有 5 × 8 = 40 种 (status × sub) 组合
- [ ] reducer 单元测试 ≥ 50 个 case
- [ ] `test_detect.py` 覆盖 simple track / final-gate / 子 pipeline 中的 resume 路径
- [ ] 0 个条件分支中的 fallback 走到 default（必须穷举）

---

### Phase 3: Bootstrap + Persistence 集成

**目标**：把 reducer / event log 接入 bootstrap 流程。

**交付**：
- `bootstrap.py`：5 个副作用步骤，每个步骤完成后 `event_log.append(bootstrap_step_completed)`
- `pg-pipeline-runner.py`（CLI）：main() 解析 + 委托给 orchestrator
- `context_chain.py`：基于 event log 生成 `context-chain.md`（用于人读）
- `tasks_md.py`：`pipeline_mark` 替代实现，订阅 record 事件

**关键设计**：
- `bootstrap.py` 不依赖 reducer，直接调 event_log
- bootstrap 内联执行 prepare_env（v1 path 兼容），失败写 `prepare_env_completed` (exit_code != 0) + 返回 `env_hook_failed` action

**测试**：
- `test_orchestrator.py`：mock event log 验证 next / record 行为
- 集成：完整跑一个空 change 验证 pipeline_started event 正确写入

**完成定义**：
- [ ] `pg-pipeline-runner.py next <change>` 在空 change 上能写入 `pipeline_started` event
- [ ] bootstrap 失败时返回 `env_hook_failed` 不污染 state
- [ ] 对照旧 `pg_build_bootstrap` 行为一致

---

### Phase 4: Dispatch 构建层

**目标**：把 NextAction 转成 LLM 可消费的 action JSON + dispatch file。

**交付**：
- `pipeline/dispatch.py`：
  - `build_action(track, phase, ...) -> dict`：返回标准 action JSON
  - `write_dispatch_file(change, track, phase, content) -> str`：写 dispatch file，返回路径
- `template_engine/renderer.py`：
  - `render(phase, ctx) -> str`：加载 `prompt-templates/{phase}.yaml` + blocks/* + Jinja2 渲染
- `template_engine/manifest.py`：
  - `read_manifest(change) -> dict`：读 `execution-manifest.yaml`
- `prompt-templates/*.yaml`：8 个 phase 模板 + 3 个公共 block

**关键设计**：
- dispatch file 命名：`{timestamp}-{track}-{phase}-dispatch.md`（废弃 seq）
- 模板内的 `{{ctx.field}}` 与旧 `_PROMPT_TEMPLATE_BASE` 字段名兼容（`_change` / `id` / `modules` / `module_details` 等）
- `dispatch_file` 路径写入 event 的 `data.dispatch_file` 字段

**测试**：
- `test_renderer.py`：每个 phase 模板渲染无报错，所有占位符替换正确
- `test_prompt_template.py`：对比旧 runner 的 `_PROMPT_TEMPLATE_BASE` 输出，新模板渲染结果必须覆盖所有相同字段
- 视觉检查：渲染 `test/dev/verify/gate/fix/fix-gate/simple/final-gate` 8 个 dispatch file

**完成定义**：
- [ ] 所有 11 个 YAML 文件存在且语法正确（`python3 -c "import yaml; yaml.safe_load(open(f))"`）
- [ ] 模板渲染覆盖旧 runner `_PROMPT_TEMPLATE_BASE` 100% 字段
- [ ] dispatch file 命名规则统一（无 seq 编号）

---

### Phase 5: Orchestrator 集成

**目标**：完整的 next / record / progress 主循环。

**交付**：
- `pipeline/orchestrator.py`：
  ```python
  class Orchestrator:
      def next(self) -> dict:
          """返回 action JSON or terminal state."""
          if not state.bootstrapped:
              bootstrap.run()
              return {"action": "bootstrap_done"}  # 立刻 next 一次返回真 action
          nd = detect.next_pending(state)
          if nd is None:
              return {"action": "done", "status": "completed"}
          action = dispatch.build_action(nd)
          event_log.append({"type": "dispatch_started", "data": ...})
          return action

      def record(self, status, ...) -> dict:
          record_evt = events.PipelineRecord(...)
          new_state, action = reducer.reduce_state(state, record_evt)
          event_log.append({"type": "record_received", ...})
          snapshot.save(new_state)
          return action  # 下一步要 dispatch 的 action，或 done/failed
  ```
- `pg-pipeline-runner.py` main()：CLI 参数解析 → 调用 orchestrator

**测试**：
- `test_integration_tdvg.py`：完整跑通 `backend: test → dev → verify → gate → pass`
- `test_integration_fix.py`：`verify escalate → fix → verify → pass`
- `test_integration_gate_fix.py`：`gate fail → fix-gate → gate pass`
- `test_integration_final.py`：`final-gate pass → archive`
- `test_integration_simple.py`：`type: simple track → 单次派遣`

**完成定义**：
- [ ] 5 个集成测试全绿
- [ ] `python3 pg-pipeline-runner.py next <test-change>` 在 fixture 上跑通完整 TDVG
- [ ] event log 包含完整的从 pipeline_started 到 pipeline_completed 序列

---

### Phase 6: V1 兼容性 / 迁移

**目标**：从旧 `.pipeline-state.json` 重建 event log。

**交付**：
- `scripts/migrations/v1_to_events.py`：CLI 工具，读旧 `.pipeline-state.json` + `context-chain.md` → 生成新的 `pipeline.events`
- `tests/test_replay_v1.py`：用 fixtures/v1_sessions/ 验证回放正确性

**关键设计**：
- 不直接改写旧文件；新 v2 写到新路径，旧文件保留
- 迁移脚本是**一次性**的：旧 change 升级时调用一次，生成 event log，然后归 v2 接管

**测试**：
- 用 3 个真实旧 session fixture 验证：迁移后用 v2 orchestrator 重放能继续推进
- 比对迁移前后的最终 state 一致

**完成定义**：
- [ ] `python3 scripts/migrations/v1_to_events.py <change>` 在 3 个 fixture 上产出有效 event log
- [ ] v2 orchestrator 在迁移后的 change 上能从中断点继续推进
- [ ] 旧 `.pipeline-state.json` / `context-chain.md` 不被修改（只读）

---

### Phase 7: 旧代码退役

**前提**：Phase 5 全部测试通过 + Phase 6 迁移验证完成。

**操作**：
- 在 `pg-build/scripts/` 标记以下文件 deprecated（移动到 `_deprecated/`）：
  - `pg-pipeline-runner.py`（仅 CLI 入口保留 thin wrapper）
  - `pg_pipeline_common.py`
  - `pg_pipeline_state_v2.py`
  - `pg_runner_v2.py`
- `pg-build/scripts/_deprecated/` 添加 README 说明保留原因（archived change 回放需要）
- 删除旧 `pg-build/scripts/` 中所有 `_use_state_v2()` 路由逻辑

**完成定义**：
- [ ] `pg-build/scripts/` 不再被任何 active session 引用（grep 验证）
- [ ] 旧文件保留 1 个 release cycle 后再彻底删除
- [ ] `project.yaml` 的 `state_v2.enabled` 配置项标记 deprecated

---

## 4. 单元测试规范

**强制要求**：
- 每个公共函数必须有 ≥ 1 个单测
- reducer 的所有 match 分支必须有 ≥ 1 个对应 test case
- 测试覆盖率（`pytest --cov`）：
  - `reducer.py` ≥ 95%
  - `event_log.py` ≥ 90%
  - 其他模块 ≥ 85%
- 集成测试必须用真实 fixture（不允许全 mock）

**禁止的测试模式**：
- ❌ 只断言 action 字段名而不验证值的语义
- ❌ 跨多个 reducer 调用堆叠断言（单测只测一个转换）
- ❌ 用 `mock.patch` 模拟 reducer 自身的输入（应直接喂 state dict）

**推荐模式**：
- ✅ reducer 测试用 `pytest.mark.parametrize` 覆盖所有 (phase, status) 组合
- ✅ fixture 用 JSON 文件存储预期 snapshot，断言 `snapshot_after` 与 fixture 一致

---

## 5. 集成测试 Fixtures

需要在 `tests/fixtures/` 准备以下场景：

| Fixture | 场景 | 预期 event 序列 |
|---------|------|----------------|
| `tdvg_happy.json` | 单 track 一次通过 | pipeline_started → 4×dispatch_started → 5×record_received → track_completed |
| `fix_one_cycle.json` | verify 1 轮 fix | ... → verify record_received(escalate) → fix_cycle_started → fix dispatch → fix record_received(completed) → verify dispatch → verify record_received(completed) → ... |
| `gate_fix_exhausted.json` | gate-fix 耗尽 | ... → gate_cycles[3] → known-issues → track_completed(exhausted) |
| `final_gate.json` | 多 track + final-gate | 4×track_completed → final-gate dispatch → final-gate pass → pipeline_completed |
| `simple_track.json` | type: simple | pipeline_started → 1×dispatch_started(simple) → 1×record_received(completed) |
| `workflow_failed.json` | test 重试耗尽 | ... → 3×test record_received(failed) → workflow_failed |

每个 fixture 是**录制好的真实 event log 序列**。集成测试读取 fixture 注入 event_log mock，验证 orchestrator 行为。

---

## 6. 迁移验证清单

实施完成后必须验证：

- [ ] **覆盖率**：reducer 100% match 分支被测试
- [ ] **行为对等**：旧 v2 在 5 个 fixture session 上跑出的 event 序列，与新 v2 跑出的 event 序列语义一致（允许字段顺序不同、ts 不同）
- [ ] **数据契约稳定**：event schema 版本化（`schema_version` 字段），未来可演进
- [ ] **查看器 hook 点**：event log 包含 `dispatch_file` / `report_path` / `log_path` 字段，查看器可直接消费
- [ ] **回滚安全**：旧 `.pipeline-state.json` 不被修改，旧 runner 仍可读（deprecated 期间）
- [ ] **文档同步**：新 `SKILL.md` 删除"v1/v2 行为对齐"章节（v1 不再存在）

---

## 7. SKILL.md 重写要点

新 SKILL.md 必须精简到 ~250 行，包含：

1. **总览**（20 行）：架构图（引用 mermaid）+ 核心设计决定
2. **CLI 用法**（30 行）：next / record / progress / migrate 命令
3. **Event Schema**（80 行）：所有 event 类型的表格（type / 触发时机 / 字段）
4. **模板系统**（30 行）：如何添加新 phase 模板
5. **查看器数据接口**（40 行）：event log 的 HTTP/SSE 暴露约定
6. **故障排查**（50 行）：常见错误码 + 修复指引

**删除的内容**：
- ❌ "v1/v2 行为对齐"（v1 不存在）
- ❌ "共享 helper 抽取"（不再需要）
- ❌ "sub-status 强制对应表"（reducer match 穷举保证）
- ❌ "MAX_FIX_CYCLES 强制 gate" 警告（reducer 行为而非文档约束）

---

## 8. 实施顺序与里程碑

| Milestone | 完成 Phase | 验证标准 | 状态 |
|-----------|-----------|---------|------|
| **M1** | Phase 1+2 完成 | reducer + event_log + state 全绿，覆盖率达标 | 🟡 Phase 1 完成，Phase 2 待开始 |
| **M2** | Phase 3+4 完成 | bootstrap + dispatcher 集成，跑通单个 dispatch | ⬜ |
| **M3** | Phase 5 完成 | 5 个集成测试全绿，端到端跑通完整 TDVG | ⬜ |
| **M4** | Phase 6 完成 | 旧 session 迁移 + 回放验证 | ⬜ |
| **M5** | Phase 7 完成 | 旧代码 deprecated，新 SKILL.md 上线 | ⬜ |

### Phase 1 完成明细

✅ **已实现**：
- `scripts/pipeline/events.py` — Event / Record / Action dataclass 与常量
- `scripts/pipeline/state.py` — PhaseState / TrackState / SubPipelineState / PipelineState frozen dataclass
- `scripts/pipeline/event_log.py` — append-only JSONL 写入 + replay + tail + filter
- `scripts/pipeline/snapshot.py` — snapshot 持久化 + 从 event log 重建

✅ **测试**（48 tests pass）：
- `tests/test_event_log.py` — 18 tests（append / replay / tail / query / 路径解析）
- `tests/test_state.py` — 21 tests（PhaseState / TrackState / SubPipelineState / PipelineState / 事件常量兼容性）
- `tests/test_snapshot.py` — 9 tests（save/load 原子性 / rebuild 容错）

✅ **设计约束**：
- 零依赖：`scripts/pipeline/*` 仅依赖标准库与彼此
- 不引用 pg-build 现有任何代码
- frozen dataclass + replace() 模式 → 严格不可变
- `os.fsync` 默认关闭（性能优先），可通过 `EventLog(fsync=True)` 启用

---

## 9. 已知约束与风险

| 风险 | 缓解措施 |
|------|---------|
| Jinja2 模板与旧 `_PROMPT_TEMPLATE_BASE` 字段名漂移 | Phase 4 测试用 `test_prompt_template.py` 比对所有字段 |
| reducer match 分支漏掉某组合导致 `error_action` | `test_reducer.py` 用 parametrize 显式覆盖所有 40 组合 + `match` exhaustiveness 注释 |
| 旧 v1 session 的 `.pipeline-state.json` 数据格式与新 event 不对齐 | Phase 6 迁移测试覆盖 3+ 真实 fixture |
| event log 写入并发安全（runner LLM 端并发调用） | 用 `flock` 文件锁或单写者假设（runner 是单进程） |
| 查看器未来 API 变更 | event schema 加 `schema_version` 字段 + 兼容策略 |

---

## 10. 文档与代码评审要求

每次 Phase 提交必须包含：
1. 代码本身
2. 对应单元测试（覆盖率达标）
3. `plan.md` 的完成 checkbox 更新
4. **事件 schema 变更说明**（如果新增 event type 或字段）
5. **API 兼容性说明**（如果改动 CLI 接口）

评审 checklist：
- [ ] reducer 无 I/O（`grep -l "open\|read\|write" reducer.py` 应为空）
- [ ] save_state 不存在（`grep -r "save_state\|load_state" scripts/` 应为空）
- [ ] 所有 dispatch_file 命名含 timestamp
- [ ] 新模块不反向 import 已有模块（避免循环依赖）