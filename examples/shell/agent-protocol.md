# Agent ↔ pg-skills 协议速查

> 本文件由 pg-init-project Phase 5 生成。Agent 拿到 SSOT、调用 hook、找日志时, 按本文件指引操作。
> 项目自有 AGENTS.md 可以引用本文件作为 SSOT 来源, 但**不在本文件记录项目专属约定**。

## §1 SSOT 查询（pg-parse-config.py pg-agent）

LLM agent **必须**通过 `pg-parse-config.py pg-agent` workflow 拿 SSOT——这是为 agent 设计的专用入口, 只暴露 `modules` + `environments` 两段顶层数据, 不混入 `tracks` / `stages` / `fix_issue` 等 skill 内部状态。

| 想做的事 | 命令 |
|---|---|
| 拿全部 modules + environments | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-agent` |
| 拿单个模块的 build 命令 | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-build <module>` |
| 拿单个模块的某 test_key | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test <module> <test_key>` |
| 拿环境的 start/stop/logs | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-env <env>` |
| 拿单值（如 backend port） | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --key environments.<env>.roles.backend.instances.0.port` |
| 拿子树（如所有 tracks） | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --prefix tracks` |

⚠️ **不要**用 `pg-parse-config.py pg-fix-issue` / `pg-build` / `pg-quick-build` / `pg-regression` 等带 skill 名的调用——那些是给 skill 编排器用的, agent 用会被迫看到噪声（如 `fix_issue.escalation_artifacts`）。

⚠️ **不要** `pg-parse-config.py --prefix modules.backend.test.unit` 后手动 parse JSON——直接用 `--resolve-module-test <m> <key>`。

⚠️ **不要**直接读 `.pg/project.yaml`——那样绕过了 SSOT, agent 拿到的可能是 stale 副本。

## §2 Hook 调用（仅一个入口: pg-invoke-hook.py）

**LLM agent 调 hook 必须**通过 `pg-invoke-hook.py`——禁止直接 `bash .pg/hooks/<x>.sh`（那样不写 result.json, 日志回落到 `scripts/logs/`, 审计不可见）。

```bash
# 1. 一次任务会话用一个 session-id (见 §2.5)
PG_AGENT_SESSION="$(date -u +%Y-%m-%d)-fix-bug-42"

# 2. 调 hook (示例: dev-local 环境的 backend 实例 start)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
  --caller pg-agent \
  --session "$PG_AGENT_SESSION" \
  --env dev-local \
  --role backend \
  --action start \
  --instance backend-1
```

**关键参数说明**：
- `--caller pg-agent` 固定值, 标识此调用来自 LLM agent, 路由日志到 `.pg/agent/<session>/<env>/logs/`。
- `--session` 由 agent 自己生成（见 §2.5），一次任务用同一个。
- `--env` / `--role` / `--action` / `--instance` 必须先通过 `pg-parse-config.py pg-agent` 拿到 SSOT，再具体填。
- action 取值：`start` / `stop` / `restart` / `logs` / `tail` / `health_check`（如已声明）。

⚠️ **禁止**直接 `bash .pg/hooks/role-backend-start.sh backend backend-1`——审计员 `grep "pg-agent" .pg/agent/<session>/...` 找不到这条记录。

## §2.5 session-id 约定

- **格式**: `<iso-date>-<session-keyword>`
  - `<iso-date>` = `date -u +%Y-%m-%d`（UTC 日期）
  - `<session-keyword>` = 限 12 字符 ASCII（`[a-z0-9-]+`），推荐用本次任务语义化短语
- **示例**: `2026-06-29-fix-bug-42` / `2026-06-29-add-dark-mode` / `2026-06-29-regress`
- **复用规则**: agent 收到任务时生成, 整个任务期间复用同一个；任务结束不再用
- **审计用法**: `grep -r "<session-id>" .pg/agent/` 可拉出该任务所有 hook 记录

⚠️ **任务结束必须换新 session-id**——否则新任务的日志会污染旧 session 的归档目录。

## §3 日志路径（按 caller × session × env）

| caller | session 格式 | 环境 | 日志路径 |
|---|---|---|---|
| `pg-agent` | `<iso-date>-<keyword>` | dev-local | `.pg/agent/<session>/dev-local/logs/` |
| `pg-agent` | `<iso-date>-<keyword>` | multi-tier | `.pg/agent/<session>/multi-tier/logs/` |
| `pg-build` | `<change-id>` | dev-local | `.pg/changes/<change-id>/2-build/dev-local/logs/` |
| `pg-fix-issue` | `<change-id>` | dev-local | `.pg/fix-issue/<change-id>/dev-local/logs/` |
| `pg-regression` | `<suite>-<date>-<seq>` | dev-local | `.pg/regression/<session>/dev-local/logs/` |

⚠️ **不要去 `scripts/logs/`**——那只是兜底路径, 不属于 pg-skills 标准路由（hook 没走 `pg-invoke-hook.py` 才会落这里）。

## §5 常见错误

| 错误 | 原因 | 修复 |
|---|---|---|
| `environment not found` | env 名写错 | 先 `pg-parse-config.py --prefix environments` 看可用 env 列表 |
| `role 'xxx' not defined` | role 名写错 | 先 `pg-parse-config.py --prefix environments.<env>.roles` 看可用 role |
| `action 'xxx' not defined` | action 名不是 start/stop/restart/logs/tail/health_check | 检查 `.pg/project.yaml` 的 `environments.<env>.roles.<r>.actions` |
| `instance 'xxx' not found` | instance 名写错 | 检查 `environments.<env>.roles.<r>.instances` 列表 |
| `Error: --caller=pg-agent requires explicit --session` | agent 调时忘了传 `--session` | 按 §2.5 生成 session-id 后传入 |
| 日志找不到 | session-id 拼错或跨任务复用 | 检查 `$PG_AGENT_SESSION` 变量值是否唯一 |
| `--caller ad-hoc` 总是缺省 | 没显式传 `--caller` | 必须显式 `--caller pg-agent` |