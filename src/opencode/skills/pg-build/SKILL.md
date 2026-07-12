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
| `result_json_missing_after_retry` (v2.4) | sub-agent 未调用 `pg-build-result --output-path` | 检查 dispatch prompt 中 `{result_json_path}` 占位符是否被替换；sub-agent 必须执行 `pg-build-result --mode agent --output-path <path> --require-output` |
| `pg-build-result` exit 2 (v2.4) | `--require-output` 模式下写入失败 | 检查 `--output-path` 目录是否存在、是否可写；路径必须是绝对路径 |
| `result_json_missing: ...` (v2.5) | `--result-json` 指向不存在路径 | 用 dispatch action 返回的 `expected_result_path`；或改回 v2.4 显式 CLI 形式 |
| `result_json_invalid: ...` (v2.5) | `--result-json` 文件 JSON 解析失败或顶层非 dict | 让 sub-agent 重跑 `pg-build-result` 落盘 |
| `status_missing: ...` (v2.5) | `--result-json` 与 `--status` 同时缺 status 字段 | 二选一必填（CLI `--status` 或文件 `status`） |

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
| escalate_threshold ≤ score < pass | `escalate` | → fix-review 循环 |
| < escalate_threshold | `failed` | → workflow_failed |

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
