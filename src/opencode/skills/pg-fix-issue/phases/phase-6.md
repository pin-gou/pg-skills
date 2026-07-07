# Phase 6: 失败处理 / 滚动修复 / 子问题分拆

## 决策树（Phase 5b 后）

```
iteration 完成后:
  ├── weighted_rate >= user_threshold (默认 0.8)
  │   AND 无 failure_criteria 触发
  │   AND 无强制终止条件触发
  │   → ✅ 修复成功 → 最终结论
  │
  ├── weighted_rate < threshold 但 iteration_count < max
  │   AND 暴露新问题数 < sub_problem_split_trigger.min_exposed_problems
  │   → ⚠️ 继续 iteration
  │     → 回到 Phase 4 修复新问题
  │
  ├── weighted_rate < threshold AND 满足分拆触发条件
  │   AND sub_problem_strategy == auto_split
  │   → 🔀 子问题分拆
  │     → 创建新 session: fix-<date>-<子问题-slug>
  │     → 在 phase-progress.md.split_sessions 记录
  │     → 当前 session 继续处理剩余问题
  │
  └── iteration_count >= max OR 任一强制终止条件触发
      → ESCALATE_WITH_MENU
```

## 滚动修复

每次验证发现"原根因已修但暴露新问题" → 滚动修复：

1. 编排器继续修（不派遣 subagent）
2. 修完**重跑 executor 验证**
3. **计入 iteration_count**
4. 更新 phase-progress.md

```yaml
iteration_count: N + 1
max_iteration_count: <从 config 读>
```

## 子问题分拆触发条件

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

分拆步骤：

1. **识别待分拆问题**：从 exposed_problems 中找出 root_cause 与初始问题无关的
2. **生成子 session 名**：`fix-<date>-<子问题-slug>`
3. **写入分拆记录**：

```yaml
waterfall:
  split_sessions:
    - parent_session: <当前 session>
      child_session: fix-<date>-<子问题-slug>
      problem_ids: [P-2, P-3]
      created_at: <timestamp>
      reason: "completion_rate < 0.5 AND exposed >= 3"
```

4. **更新本 session 状态**：标记已分拆问题为 `splitted=true`，不再计入当前 completion_rate

## 强制终止条件（立即 ESCALATE）

读取 `fix_issue.termination_conditions`：

| 条件 | 阈值 | 检测 |
|------|------|------|
| `rate_stagnant_iterations` | 2 | 连续 N iteration `weighted_rate` 不变 |
| `net_regression_max` | 0 | 新引入问题数 > 已修问题数 |
| `problem_explosion_threshold` | 10 | `exposed_problems.length > 10` |
| `same_root_cause_threshold` | 3 | 同一 root_cause 出现 >= 3 次 |
| `iteration_count > max` | — | `iteration_count > max_iteration_count` |

任一触发 → **立即 ESCALATE**。

## 失败类型分类

| 失败类型 | 处理 | 计入 retry |
|---------|------|----------|
| 编译错误（新引入） | 编排器修复 | ✅ |
| 测试失败（actual ≠ expected） | 编排器判断改代码还是改测试 | ✅ |
| verify 失败（运行版本不对） | 检查上一次 invoke-hook | ✅ |
| 端到端 API 失败 | 看 response body | ✅ |
| 日志 PANIC | 看 panic stack | ✅ |
| success_criteria 未满足 | 重新诊断 | ✅ |
| failure_criteria 触发 | 重新诊断 | ✅ |
| executor 机械失败 | executor 自决 | ❌ |
| 端口占用 | executor 自决 | ❌ |
| 环境问题 | 记 KnownIssues | ❌ |

## ESCALATE_WITH_MENU（3-5 选项）

```yaml
question:
  - "已迭代 {N} 次仍未能修复。请选择下一步：
     A: 再给一次机会（推荐）
     B: 切人工修复
     C: 缩范围重试
     D: （如 allow_manual_verification）手动验证后回报
     E: 用户实测仍失败"
  options:
    - "再给一次机会 (推荐)"
    - "切人工修复"
    - "缩范围重试"
    - "手动验证后回报"
    - "用户实测仍失败"
```

选项行为映射：

| 选项 | iteration_count | max | 其他 |
|------|----------------|----|----|
| 再给一次机会 | 不重置 | 增加到 N+2 | 继续 Phase 1 |
| 切人工修复 | 保留 | 不变 | 输出 report，停止 |
| 缩范围重试 | 不重置 | 不变 | 编辑 success_criteria |
| 手动验证后回报 | 不重置 | 不变 | 等用户回报 |
| 用户实测仍失败 | +1 | 不变 | 回到 Phase 1 |

## 用户实测 fail 分支

触发：主 agent 已报告"修复成功"，用户回复"bug 仍存在"。

处理：
1. `iteration_count += 1`
2. 超出 max → ESCALATE_MENU
3. 否则 → 回到 Phase 1 重诊断
4. 优先检查假阳性：
   - 修复代码已编译但未部署（最常见）
   - invoke-hook 未执行或失败
   - 前端缓存导致旧代码仍运行