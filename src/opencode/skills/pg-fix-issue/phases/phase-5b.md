# Phase 5b: 审核 + 问题瀑布更新

## 必做动作（顺序固定）

- [ ] **S5b-1**: executor 伪造检测（对端到端 operation）
- [ ] **S5b-2**: 逐项检查 success_criteria
- [ ] **S5b-3**: 逐项检查 failure_criteria
- [ ] **S5b-4**: 更新 waterfall（暴露新问题 + 标记已修）
- [ ] **S5b-5**: 重算 completion_metrics
- [ ] **S5b-6**: 强制终止检测
- [ ] **S5b-7**: 写入 `.pg/fix-issue/<session>/iter-N.json`

## S5b-1: executor 伪造检测

对涉及真实系统状态的 operation，编排器**必须**亲自交叉验证 1-2 条：

```
针对每条涉及系统状态（非编译/非 git diff）的 operation:
  - api_call → 检查 evidence.response_first_line 是否包含真实 API 响应结构
  - shell virsh list → 证据必须有 stdout_tail，不能只写 "matched"
  - log_filter → 证据必须有 raw_matches 原始匹配行（含时间戳）
```

关键检验：当 executor JSON 显示全部通过时，编排器必须自己执行 1 条关键验证命令
（如 curl API），验证 executor 没有伪造结果。

如果编排器自己的命令结果与 executor JSON 矛盾 → 判定 executor 伪造，进入 Phase 6。

## S5b-2: 逐项检查 success_criteria

```python
for sc in phase2_output.success_criteria:
    executor_result = run_executor_op(sc.verify_method, sc.verify_args)
    if executor_result.meets_criterion:
        sc.status = "PASS"
    else:
        sc.status = "FAIL"
        sc.actual = executor_result.actual_value
        sc.expected = sc.verify_args.expect_value
```

## S5b-3: 逐项检查 failure_criteria

每个 FC 必须为 NOT TRIGGERED 才能算通过。

## S5b-4: 更新 waterfall

```yaml
waterfall:
  exposed_problems:
    - id: P-1
      description: ...
      root_cause: ...
      exposed_in_iteration: 0
      fixed_in_iteration: 1   # 本次修的
      fixed: true              # 标记
      severity: blocker
    - id: P-NEW
      description: ...        # 新暴露
      root_cause: ...
      exposed_in_iteration: 1
      fixed: false
      severity: major
```

## S5b-5: 重算 completion_metrics

```yaml
completion_metrics:
  initial_problem_count: 1
  total_problems_exposed: N   # 当前总暴露
  problems_fixed: M           # 已修
  problems_unfixed: K         # 未修
  completion_rate: M/N        # 简单比
  weighted_rate: <按 severity 权重>
  is_good_enough_to_stop: weighted_rate >= user_threshold
```

severity 权重：

| severity | weight |
|----------|--------|
| blocker | 1.0 |
| major | 0.7 |
| minor | 0.3 |

## S5b-6: 强制终止检测

```yaml
termination_check:
  rate_stagnant_iterations: <连续 N iteration completion_rate 不变>
  net_regression: <新引入 >= 已修>
  overflowed: <iteration_count > max_iteration_count>
  problem_explosion: <exposed > threshold>
  same_root_cause: <同一 root_cause 出现 >= threshold>
```

任一条件触发 → **立即停止** → 进入 ESCALATE。

## S5b-7: 写入 iter-N.json

每次 iteration 后保存 executor JSON 到 `.pg/fix-issue/<session>/iter-N.json`，便于后续诊断。

## 判断矩阵

| weighted_rate | failure_criteria | 判定 |
|--------------|------------------|------|
| >= threshold | 全部未触发 | ✅ 修复成功 |
| < threshold 但 >= 0.5 | 全部未触发 | ⚠️ 继续 iteration |
| < 0.5 | 全部未触发 | ❌ 修复失败（除非有分拆策略）|
| 任意值 | 任一触发 | ❌ 修复失败 |

**反例标准（failure_criteria）优先级高于 success_criteria**。