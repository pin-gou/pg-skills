# 问题瀑布管理规则

## 问题瀑布的本质

修复一个 bug → 部署 → 验证 → **暴露下一个 bug** → 修 → 暴露下一个 → ...

这是修复 bug 的正常现象，不是异常。v2 必须**显式承认并支持**这一点。

## 问题分类

| severity | 含义 | 权重 |
|----------|------|------|
| blocker | 完全阻塞主功能 | 1.0 |
| major | 影响主要功能但有 workaround | 0.7 |
| minor | 小问题，不影响主流程 | 0.3 |

## 达成度计算

```python
def calc_completion_rate(problems):
    total_severity_weight = sum(p.severity.weight for p in problems if not p.splitted)
    fixed_severity_weight  = sum(p.severity.weight for p in problems if p.fixed)
    
    weighted_rate = fixed_severity_weight / total_severity_weight
    
    return {
        'completion_rate': fixed_count / total_count,  # 简单比
        'weighted_rate': weighted_rate,                 # 加权比
        'is_good_enough_to_stop': weighted_rate >= user_threshold
    }
```

## 瀑布更新时机

每次 Phase 5b 完成后**必须**更新 phase-progress.md.waterfall：

1. 新发现的问题 → 加入 `exposed_problems`，`fixed=false`
2. 已修的问题 → 标记 `fixed=true`, `fixed_in_iteration=N`
3. 已分拆的问题 → 标记 `splitted=true`（不再计入当前 session 的达成度）
4. 重算 `completion_metrics`

## 子问题分拆触发

读取 `fix_issue.sub_problem_split_trigger`：

```yaml
sub_problem_split_trigger:
  min_exposed_problems: 3
  max_completion_rate: 0.5
```

触发条件：

```
exposed_problems.length >= min_exposed_problems
AND weighted_rate < max_completion_rate
AND sub_problem_strategy == auto_split
```

## 子问题分拆步骤

### 1. 识别待分拆问题

从 `exposed_problems` 中找出 `root_cause` 与初始问题（P-1）无关的：

```python
def should_split(problem, initial_root_cause):
    return problem.root_cause != initial_root_cause
```

### 2. 生成子 session 名

```
fix-<YYYY-MM-DD>-<slug>
```

slug 来源：problem.description 的前 3-5 个关键词。

### 3. 写入分拆记录

```yaml
waterfall:
  split_sessions:
    - parent_session: <当前 session>
      child_session: fix-2026-07-08-vm-disk-stats-auth
      problem_ids: [P-2]
      created_at: 2026-07-08T10:00:00+08:00
      reason: "completion_rate < 0.5 AND exposed >= 3"
      expected_resolved_by: 2026-07-09  # 期望完成日期
```

### 4. 更新本 session 状态

被分拆的问题标记 `splitted=true`：

```yaml
- id: P-2
  description: "vm-disk-stats 401"
  fixed: false
  splitted: true
  splitted_to: fix-2026-07-08-vm-disk-stats-auth
```

`splitted=true` 的问题**不再计入**当前 session 的 `completion_rate`。

## 强制终止条件

读取 `fix_issue.termination_conditions`：

| 条件 | 阈值（默认） | 检测 |
|------|------------|------|
| `rate_stagnant_iterations` | 2 | 连续 N iteration `weighted_rate` 不变 |
| `net_regression_max` | 0 | 新引入问题数 > 已修问题数 |
| `problem_explosion_threshold` | 10 | `exposed_problems.length > 10` |
| `same_root_cause_threshold` | 3 | 同一 root_cause 出现 >= 3 次 |

任一触发 → **立即 ESCALATE**（不经过 ESCALATE_MENU 的常规分支）。

## 终止检测算法

```python
def check_termination(progress, conditions):
    return {
        'rate_stagnant': (
            progress.waterfall.completion_metrics.weighted_rate 
            == progress.iteration_history[-conditions.rate_stagnant_iterations].weighted_rate
            if len(progress.iteration_history) >= conditions.rate_stagnant_iterations
            else False
        ),
        'net_regression': (
            count(problems where fixed=true and fixed_in_iteration == current)
            < count(problems where fixed=false and exposed_in_iteration == current)
        ),
        'problem_explosion': (
            len(progress.waterfall.exposed_problems) > conditions.problem_explosion_threshold
        ),
        'same_root_cause': (
            max(count(root_cause) for root_cause in unique_root_causes)
            >= conditions.same_root_cause_threshold
        ),
        'overflowed': (
            progress.iteration_count > progress.max_iteration_count
        )
    }
```

## 瀑布可视化（最终结论时）

```
P-1 (blocker, fixed=✅) ─────┐
                              ├─ iter 1 → 修复 + 暴露 P-2, P-3
P-2 (major, fixed=❌, split) ─┤   weighted_rate: 1.0 → 0.5
                              │
P-3 (minor, fixed=❌) ────────┘
```

## 与 v1 的关键差异

| 维度 | v1 | v2 |
|------|----|----|
| 问题追踪 | 隐式，每次迭代重头 | 显式 waterfall 表 |
| 完成判定 | 二元（pass/fail） | 达成度百分比 + 阈值 |
| 子问题 | 必须在当前 session 处理 | auto_split 自动分拆 |
| 强制终止 | 无 | 5 个条件 |
| 跨会话恢复 | 无 | phase-progress.md |