# AGENTS.md Drift Patches

> 本文件由 pg-init-project Phase 5 生成。**不直接修改任何 AGENTS.md**，仅作为 review 清单。
> 用户 review 后，可按本文件手动修改 AGENTS.md，或写脚本批量应用。

## Drift 总览

| 文件 | 类别 a (重复) | 类别 b (硬编码) | 类别 c (旧路径) | 总计 |
|------|---------------|------------------|-------------------|------|
| <root> | 0 | 0 | 0 | 0 |
| <sub-module-1>/AGENTS.md | 0 | 0 | 0 | 0 |

（总计列 = a + b + c；c 类问题最严重，必须最先修）

## 类别定义

| 类别 | 判定标准 | 严重度 |
|------|----------|--------|
| **a. 重复** | sub-module / tests AGENTS.md 出现模块命令关键字，但未说"见 .pg/context/agent-protocol.md" | low |
| **b. 硬编码** | 命令后跟具体子命令（如 `pnpm openapi` / `mvn clean install`），而非通用占位符 | medium（drift 风险） |
| **c. 旧路径** | 引用 `pg-spec/scripts/` / `scripts/` / `scripts/logs/` 等非 `.pg/hooks/` 路径 | **high**（agent 跑就会失败） |

## Patch 清单（按严重度排序：c → b → a）

| # | 文件 | 行号 | 当前内容（节选） | 类别 | 建议改法 |
|---|------|------|------------------|------|----------|
| 1 | <example-file>/AGENTS.md | 60-95 | `<example-cmd>` 硬编码 | b | 替换为"见 .pg/context/agent-protocol.md §1" |
| 2 | <root>/AGENTS.md | 166-168 | `scripts/logs/backend.log` | c | 替换为"日志路径: 按 §3 路由（e.g. .pg/agent/<session>/dev-local/logs/）" |

## 应用建议

### 手动应用（推荐）

1. 按 Patch 清单顺序（c → b → a）逐条修改
2. 修改完成后跑 `pg doctor`，确保 `agents_md_protocol_link_present` 检查通过
3. commit 时建议加一行：`AGENTS.md: 跟随 .pg/context/agent-protocol.md SSOT`

### 脚本批量应用（高级）

如 drift 数量 > 10 条，可写一次性 sed 脚本：

```bash
# 示例: 把所有 `pnpm <subcmd>` 替换为"见 .pg/context/agent-protocol.md §1"
# 注意: 必须先 review, 不要无脑替换
find . -name AGENTS.md -not -path './.git/*' \
  -exec sed -i 's|`pnpm [^`]*`|见 .pg/context/agent-protocol.md §1|g' {} \;
```

⚠️ 脚本批量应用前必须**先 review**，不要让 sed 误伤代码块（AGENTS.md 里可能有合法的 `pnpm` 示例代码）。

## 验证

```bash
# 1. 跑 doctor 校验
python3 .pg/skills/src/runtime/bin/pg doctor

# 2. 检查根 AGENTS.md 含 agent-protocol 引用
grep -l "agent-protocol" AGENTS.md

# 3. 检查子 AGENTS.md 含相同引用（按需）
grep -l "agent-protocol" webvirt-*/AGENTS.md webvirt-*/tests/AGENTS.md
```