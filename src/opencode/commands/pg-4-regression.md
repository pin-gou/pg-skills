---
name: 4-pg-regression
description: 4. 自动初始化环境并执行回归测试，自动修复失败测试脚本（无 PR），自动修复生产代码（创建 PR）
trigger: slash
agent: pg-manager
---
# /4-pg-regression <suite>

自动初始化环境并执行回归测试，自动修复失败测试脚本（无 PR），自动修复生产代码（创建 PR）

suite: $1

## 参数

- `suite` — 测试套件名（= `.pg/project.yaml` 中 `regression.suite` 段的 key），可选。留空时通过 question tool 询问用户。

## 用户交互

<script>
if (!suite) {
    let config_path = resolve(".pg/project.yaml");
    let cfg_text = read(config_path);
    let suites = {};
    // 从 regression.suite 段推导可用 suite
    // 匹配 regression.suite 下所有 2 空格缩进的 key (suite 名)
    let re = /^regression:\s*\n\s+suite:\s*\n((?:\s{4}\w+:[^\n]*\n(?:\s{6,}[^\n]*\n)*)+)/m;
    let m = cfg_text.match(re);
    if (m) {
        let block = m[1];
        let sub = /^\s{4}(\w+):\s*$/gm;
        let match;
        while ((match = sub.exec(block)) !== null) {
            suites[match[1]] = match[1];
        }
    }
    let options = Object.entries(suites).map(([key, label]) => ({
        label: key,
        description: `regression.suite.${key}`
    }));
    if (options.length === 0) {
        throw new Error("未在 .pg/project.yaml 找到 regression.suite 段");
    }
    suite = question({
        header: "选择测试套件",
        question: "请选择要运行的测试套件（suite = regression.suite 的 key）：",
        options: options
    });
}
</script>

## 执行步骤

1. 使用 Skill tool 加载 `pg-regression` SKILL，传入 suite 名 `{suite}`
2. 按 SKILL 定义执行：前置检查 → 环境初始化 → 启动服务 → 测试 → 逐例修复 → 汇总报告
3. 维护对应 suite 的问题清单（`.pg/regression/<suite>.json`，runner 修复完成后会自动从该文件移除已修 issue）

**触发词**:
```
/4-pg-regression frontend
/4-pg-regression backend
/4-pg-regression agent
```
