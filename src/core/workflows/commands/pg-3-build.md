---
name: 3-pg-build
description: 3. 执行 tasks.md 的任务条目，构建代码并验证构建结果
trigger: slash
agent: pg-manager
---

# /3-pg-build <change-name>

change-name: $1

## 参数

- `change-name` — 变更名称，对应 `.pg/changes/<change-name>/` 目录。留空时通过 {{pg:action.user_question}} 询问用户

## 用户交互

<script>
if (!change_name) {
    let changes_dir = resolve(".pg/changes");
    let entries = read(changes_dir).filter(e => e.endsWith("/") && e !== "archive/");
    if (entries.length === 0) {
        print("没有待处理的 change。");
        exit();
    }
    let options = entries.map(e => ({
        label: e.replace("/", ""),
        description: "待实现变更"
    }));
    change_name = question({
        header: "选择变更",
        question: "请选择要实现的变更：",
        options: options
    });
}
</script>

## 执行步骤

1. 使用 {{pg:action.skill_loader}} 加载 `pg-build` skill
2. 执行 bootstrap 初始化：
   `python3 .pg/skills/src/core/workflows/skills/pg-build/scripts/pg-pipeline-runner.py bootstrap {change_name}`
   - `ok: false` → 直接输出 error 给用户，终止流程（**禁止**自动修复）
   - `env_hook_plan` 非 null → bash 执行 plan.command，然后 `env-action-result` 记录 → 再次 bootstrap
   - `env_hook_plan=null` → 进入步骤 2.5
2.5 初始化 TODO 列表（机械派生，不自行概括）：
   `python3 .pg/skills/src/core/workflows/skills/pg-build/scripts/pg-list-phases.py {change_name}`
   按 stdout JSON 中 `items` 数组的 `label` 字段，逐项调用 todowrite 工具。
3. 进入主循环：`python3 ... next {change_name}` → 按 action 派送 sub-agent / record 结果 / 循环
   - **每次 record 完成后**：调 `pg-list-phases.py {change_name} --with-progress` 刷新 TODO 状态
   - **每次 next 返回后**：调 `pg-list-phases.py {change_name} --detect-sub-pipelines` 检测是否有 fix/review/scenario-fix 子 pipeline 需追加
4. 循环至 pipeline 完成（`action: done` → 触发 pg-verify-and-merge）

**示例**:
```
/3-pg-build add-user-api
/3-pg-build fix-login-bug
```
