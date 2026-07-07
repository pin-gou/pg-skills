# Phase 3: 用户确认

## 必做动作（顺序固定）

- [ ] **S3-1**: 更新 phase-progress.md `current_phase: 3`
- [ ] **S3-2**: question 复现步骤 + 成功标准确认
- [ ] **S3-3**: question 环境选择
- [ ] **S3-4**: question verify_level 选择（**v2 新增**）
- [ ] **S3-5**: question 达成度阈值选择（**v2 新增**）
- [ ] **S3-6**: question 子问题处理策略（**v2 新增**）
- [ ] **S3-7**: question prepare_env 时机
- [ ] **S3-8**: question clean_env 时机
- [ ] **S3-9**: 更新 phase-progress.md `user_decisions` + `phases[3].status: completed`

## S3-2: 复现步骤 + 成功标准确认

```yaml
question:
  - "以下复现步骤和成功标准是否准确可行？
     问题描述：...
     复现步骤：
     1. ...
     2. ...
     成功标准：
     - [SC-FORCE-1] ...
     - [SC-1] ...
     反例标准：
     - [FC-1] ..."
  options:
    - "可以，开始执行"
    - "需要调整"
```

前端问题额外提示：要求用户在浏览器中硬刷新（Ctrl+Shift+R）。

## S3-3: 环境选择

```yaml
question:
  - "请选择修复环境（来自 config.yaml）：
     - dev-local: ...
     - multi-tier: ..."
  options: [env list from config]
```

**禁止**硬编码推荐环境。

## S3-4: verify_level 选择（v2 关键）

```yaml
question:
  - "请选择 verify_level：
     - L1: 完整部署验证（invoke-hook + api_call + log_filter）—— 默认推荐
     - L2: 编译 + 集成测试 + DB schema 验证（环境不可重启时选）
     - L3: 仅编译验证（仅有源码，无服务/DB）
     
     约束：选择 L1 后若 invoke-hook 失败，**必须升级到 L2**（禁止自动降级到 L3）"
  options:
    - "L1 (推荐)"
    - "L2"
    - "L3"
```

默认读取 `fix_issue.default_verify_level`，用户可覆盖。

**L1 → L2 升级触发**：
- invoke-hook 返回非零退出码
- invoke-hook 完成后 health_check 失败

**禁止 L1 → L3 自动降级**：必须在 phase-progress.md 中显式记录升级原因。

## S3-5: 达成度阈值选择（v2 新增）

```yaml
question:
  - "请选择修复达成度阈值：
     - 0.8 (推荐): completion_rate >= 80% 视为修复成功
     - 0.9: 严格（>= 90%）
     - 1.0: 完美（100%，所有暴露问题必须修复）"
  options:
    - "0.8 (推荐)"
    - "0.9"
    - "1.0"
```

达成度计算：

```python
completion_rate = sum(problems_fixed) / total_problems_exposed
weighted_rate   = sum(problems_fixed * severity_weight) / sum(total_severity_weight)
```

severity 权重：

| severity | weight |
|----------|--------|
| blocker | 1.0 |
| major | 0.7 |
| minor | 0.3 |

最终判定用 `weighted_rate >= threshold`。

## S3-6: 子问题处理策略（v2 新增）

```yaml
question:
  - "请选择瀑布式暴露问题的处理策略：
     - A: 自动分拆子 session（推荐）—— 本次 session 只处理初始问题，瀑布暴露的子问题自动创建新 fix-issue session
     - B: 当前 session 内继续 —— 所有子问题在本次 session 一起处理
     - C: 当前 session 终止 + KnownIssues —— 未修复问题列入 KnownIssues，不创建新 session"
  options:
    - "A (推荐)"
    - "B"
    - "C"
```

**分拆触发条件**（默认从 config 读）：
- `exposed_problems.length >= 3` AND
- `weighted_rate < 0.5`

分拆时：
- 创建新 session：`fix-<date>-<子问题-slug>`
- 在当前 session 的 `waterfall.split_sessions` 记录分拆事件
- 当前 session 继续处理剩余问题

## S3-7: prepare_env 时机

仅当 `fix_issue.ask_prepare_env == true` 才问。

## S3-8: clean_env 时机

仅当 `fix_issue.ask_clean_env == true` 才问。

## S3-9: 写入 user_decisions

```yaml
user_decisions:
  env: <用户选择>
  verify_level: L1  # 或 L2/L3
  completion_threshold: 0.8  # 或 0.9/1.0
  sub_problem_strategy: auto_split  # 或 in_session/known_issues
  prepare_env: true  # 用户回答
  clean_env: false
```

## Phase 3 出口检查

- [ ] 所有 question 已发出并得到用户回答
- [ ] phase-progress.md.user_decisions 已写入
- [ ] phase-progress.md.phases[3].status = completed

只有满足才能进入 Phase 4。