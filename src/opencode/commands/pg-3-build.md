---
name: 3-pg-build
description: 3. 执行 tasks.md 的任务条目，构建代码并验证构建结果
trigger: slash
agent: pg-manager
---

# /3-pg-build <change-name>

change-name: $1

## 参数

- `change-name` — 变更名称，对应 `.pg/changes/<change-name>/` 目录。留空时通过 question tool 询问用户

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

1. 使用 Skill tool 加载 `pg-build` skill
2. 读取 `.pg/changes/{change_name}/tasks.md` 获取任务清单
3. 按 SKILL 定义的工作流依次执行各个 phase
4. 管理 context-chain.md 和 tasks.md 的状态更新
5. 输出最终报告

**示例**:
```
/3-pg-build add-user-api
/3-pg-build fix-login-bug
```
