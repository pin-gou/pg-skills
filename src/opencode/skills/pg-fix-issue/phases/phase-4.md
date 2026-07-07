# Phase 4: 编排器自己修复

## 必做动作（顺序固定）

- [ ] **S4-1**: 更新 phase-progress.md `current_phase: 4`
- [ ] **S4-2**: （可选）Phase 4.0 TDD 写失败测试
- [ ] **S4-3**: 设计修复
- [ ] **S4-4**: 用 Edit/Write 改代码
- [ ] **S4-5**: 运行修复点附近测试（compile + unit test）
- [ ] **S4-6**: 更新 phase-progress.md `phases[4].status: completed, files_changed: [...]`

## 操作流程

1. **读代码** — 使用 codegraph / Read / Grep
2. **设计修复** — 基于 Phase 1 候选故障点
3. **Edit/Write** — 实际修改代码
4. **诊断日志** — 按需添加（遵守规约）

## 诊断日志规约

1. 位置：只打在入口/边界/消息分发器
2. 形式：稳定前缀 `DIAG:`
3. 数量：单次诊断不超过 3 处
4. 生命周期：Phase 5b 验证通过后**必须清理**
5. 不替代根因分析：加日志前清楚"我在验证什么假设"

## TDD 建议（如适用）

- 修复前先加失败测试
- 跑测试确认红 phase
- 修复后跑测试确认绿 phase
- 测试由编排器自己写（不派遣 test agent）

## 修复禁止操作

| 操作 | 正确做法 |
|------|---------|
| 手写 mvn compile | 走 Phase 5 |
| 手启服务 | 走 invoke-hook |
| 加 DIAG 日志超过 3 处 | 收窄到具体假设 |
| 改代码不更新 phase-progress | 强制同步 |

## Phase 4 出口检查

- [ ] files_changed 已写入 phase-progress.md
- [ ] git diff 干净（无 DIAG 残留）
- [ ] 编译通过
- [ ] 改动文件附近单元测试通过

只有满足才能进入 Phase 5。