# Phase 5c+d: 架构验证 + 清理

## Phase 5c: 架构验证（强制）

对照检查点（全部必须通过）：

- [ ] 修复是否遵循项目 API scope 规范
- [ ] 修复是否使用与已有相似功能一致的 API/组件模式
- [ ] 修复是否引入了新的安全隐患
- [ ] 修复是否破坏了协议语义（WS / gRPC / HTTP）
- [ ] 修复是否与上下游契约一致

任一不通过 → 回到 Phase 4 修复。

## Phase 5d: 诊断产物清理（强制）

- [ ] 撤掉所有 `DIAG:` 临时日志
- [ ] `git diff --stat` 只显示目标文件变更
- [ ] `git_diff_check` operation 通过
- [ ] 临时脚本/复现脚本已清理

## 5d 自检命令

```bash
# 1. 查找 DIAG 残留
grep -rn "DIAG:" --include="*.java" --include="*.ts" --include="*.go" .
# 期望输出为空

# 2. git diff 范围
git diff --stat
# 期望只显示 phase-progress.md 记录的 files_changed

# 3. 临时文件清理
ls /tmp/repro* /tmp/fix-* 2>/dev/null
# 期望输出为空
```

任一项不通过 → 修复后重跑 Phase 5。

## Phase 5c+d → 最终结论

全部通过后进入「最终结论」输出（见 templates/final-report.md）。