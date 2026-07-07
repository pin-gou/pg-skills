# Phase 0: 加载配置 + 初始化进度文件

## 必做动作（顺序固定，不可跳过）

- [ ] **S0-1**: 执行 `pg-parse-config.py pg-fix-issue`
- [ ] **S0-2**: 反查 affected_tracks 和 affected_modules
- [ ] **S0-3**: 创建 `.pg/fix-issue/<session>/phase-progress.md`
- [ ] **S0-4**: 初始化 waterfall 段为 `[exposed_problems: [P-1]]`

## S0-1: 执行 pg-parse-config.py

```bash
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-fix-issue
```

输出五段：`modules` / `environments` / `tracks` / `stages` / `fix_issue`。
失败 → ESCALATE（v1 行为一致）。

## S0-2: 反查 affected_tracks

算法：

```
1. bug_files = [Phase 1 链路分析中识别的所有相关文件路径]
2. for each file in bug_files:
     for each (track_id, track_def) in tracks:
       for each module_id in track_def.modules:
         if file.startswith(modules[module_id].root + "/"):
            affected_tracks.add(track_id)
            affected_modules.add(module_id)
3. affected_tracks = sort by config.yaml 中 tracks 的声明顺序
```

**禁止**根据"复杂度"分类（不存在简单/复杂分支）。

## S0-3: 创建 phase-progress.md

session 名格式：`fix-<YYYY-MM-DD>-<slug>`

例：`fix-2026-07-07-monitoring-tab-1006`

路径：`.pg/fix-issue/<session>/phase-progress.md`

使用 `templates/phase-progress.md` 模板。

## S0-4: 初始化 waterfall

初始 P-1 = 用户报告的 bug 描述：

```yaml
waterfall:
  exposed_problems:
    - id: P-1
      description: "<用户原始问题描述>"
      root_cause: null
      exposed_in_iteration: 0
      fixed_in_iteration: null
      fixed: false
      severity: blocker
```

## Phase 0 出口检查

```yaml
phases:
  - id: 0
    status: completed

waterfall.exposed_problems.length >= 1  # 至少 P-1

user_decisions: {}  # 等待 Phase 3 填入
```

只有全部满足才能进入 Phase 1。