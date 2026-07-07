# pg-fix-issue-v2 Phase Progress

> **唯一真相源**（Single Source of Truth）。每次进入 phase 必须先 read，再 write。
> 路径：`.pg/fix-issue/<session>/phase-progress.md`

```yaml
# pg-fix-issue-v2 Phase Progress
session: fix-2026-07-07-monitoring-tab-1006
created_at: 2026-07-07T10:00:00+08:00
updated_at: 2026-07-07T10:30:00+08:00

# ===== 当前状态 =====
current_phase: 5
current_step: S5-L1-2
status: in_progress  # pending / in_progress / completed / failed

# ===== Phase 3 用户决策 =====
user_decisions:
  env: dev-local
  verify_level: L1   # L1 / L2 / L3
  completion_threshold: 0.8
  sub_problem_strategy: auto_split  # auto_split / in_session / known_issues
  prepare_env: true
  clean_env: false

# ===== Phase 0 产出 =====
affected_tracks: [backend]
affected_modules: [backend]

# ===== Phase 完成情况 =====
phases:
  - id: 0
    status: completed
  - id: 1
    status: completed
    root_cause_files: [MetricsSlotRouter.java:330]
  - id: 2
    status: completed
    success_criteria_count: 3
    failure_criteria_count: 2
  - id: 3
    status: completed
  - id: 4
    status: completed
    files_changed: [MetricsSlotRouter.java]
  - id: 5
    status: in_progress
    verify_level: L1
    iteration_count: 2
    steps_completed: [S5-L1-1]
    steps_pending: [S5-L1-2, S5-L1-3, S5-L1-4]
  - id: 6
    status: pending

# ===== 问题瀑布（v2 核心）=====
waterfall:
  exposed_problems:
    - id: P-1
      description: "metrics API 返回 1006"
      root_cause: "createTableWithPartitions DDL 缺 noise_cpu_percent"
      exposed_in_iteration: 0
      fixed_in_iteration: 1
      fixed: true
      severity: blocker

    - id: P-2
      description: "vm-disk-stats API 401"
      root_cause: "鉴权 token 过期"
      exposed_in_iteration: 2
      fixed: false
      severity: major

    - id: P-3
      description: "前端监控 tab 标题不更新"
      root_cause: "Vue watch 路径不全"
      exposed_in_iteration: 2
      fixed: false
      severity: minor

  # 子问题分拆记录
  split_sessions:
    - parent_session: fix-2026-07-07-monitoring-tab-1006
      child_session: fix-2026-07-08-vm-disk-stats-auth
      problem_ids: [P-2]
      created_at: 2026-07-07T15:00:00+08:00
      reason: "completion_rate < 0.5 AND exposed >= 3"

  # 达成度指标（每次 iteration 重算）
  completion_metrics:
    initial_problem_count: 1
    total_problems_exposed: 3
    problems_fixed: 1
    problems_unfixed: 2
    completion_rate: 0.33
    weighted_rate: 0.50
    is_good_enough_to_stop: false

# ===== 重试计数 =====
iteration_count: 2
max_iteration_count: 5

# ===== 强制终止检测 =====
termination_check:
  rate_stagnant_iterations: 0
  net_regression: false
  overflowed: false
  problem_explosion: false

# ===== 兜底产物路径 =====
artifacts:
  call_chain_analysis: .pg/fix-issue/fix-2026-07-07-monitoring-tab-1006/call-chain.md
  phase2_output: .pg/fix-issue/fix-2026-07-07-monitoring-tab-1006/phase2.md
  executor_json_history:
    - .pg/fix-issue/fix-2026-07-07-monitoring-tab-1006/iter-1.json
    - .pg/fix-issue/fix-2026-07-07-monitoring-tab-1006/iter-2.json
```

## 字段说明

### status

| 值 | 含义 |
|----|------|
| pending | 未开始 |
| in_progress | 进行中 |
| completed | 已完成 |
| failed | 已失败（Phase 5b 判定） |

### severity

| 值 | weight |
|----|--------|
| blocker | 1.0 |
| major | 0.7 |
| minor | 0.3 |

### termination_check

每个 iteration 后**必须**重算。任一为 true → ESCALATE。

## 使用规约

1. **进入 phase 前**：Read 当前 phase-progress.md
2. **phase 内变化**：Edit 字段
3. **phase 完成**：标记 `phases[i].status = completed` + 更新 `updated_at`
4. **iteration 结束**：更新 waterfall + completion_metrics + termination_check + iteration_count
5. **跨会话恢复**：新会话先读 phase-progress.md 决定从哪个 phase 继续