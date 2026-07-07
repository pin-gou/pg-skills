# pg-fix-issue-v2 共享协议

本文件包含与 pg-build 共享的内容（executor 协议、invoke-hook CLI、配置加载）。
v2 SKILL 主入口按需引用，编排器在 Phase 5 显式调用本节命令。

## 1. 配置加载

```bash
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-fix-issue
```

输出 `modules` / `environments` / `tracks` / `stages` / `fix_issue` 五段。v2 扩展字段：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `fix_issue.default_verify_level` | `L1` | 默认 verify_level |
| `fix_issue.default_completion_threshold` | `0.8` | 默认达成度阈值 |
| `fix_issue.default_sub_problem_strategy` | `auto_split` | 子问题处理策略 |
| `fix_issue.sub_problem_split_trigger` | `{min_exposed: 3, max_rate: 0.5}` | 子问题分拆触发条件 |
| `fix_issue.termination_conditions` | `{stagnant: 2, regression: 0, explosion: 10, same_cause: 3}` | 强制终止条件 |

## 2. 模块命令解析

**禁止**直接读 `modules.<m>.test.<key>` 字段（可能是 string 或 object）。
必须用 helper 解析：

```bash
# 拿 cmd + timeout:
RESOLVED=$(python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test <module> <test_key>)
CMD=$(echo "$RESOLVED" | python3 -c "import json,sys; print(json.load(sys.stdin)['cmd'])")
TIMEOUT=$(echo "$RESOLVED" | python3 -c "import json,sys; print(json.load(sys.stdin)['timeout_seconds'])")
```

helper 返回的 `cmd` 已是 `timeout N bash -c '<cmd>'` 形式。

| 引用 | 命令 |
|------|------|
| 模块 build | `--resolve-module-build <m>` |
| 模块 test | `--resolve-module-test <m> <test_key>` |
| 模块 lint | `--resolve-module-lint <m>` |

## 3. invoke-hook CLI（v3.0+ 协议）

service 启停（backend / frontend / agent start|stop|restart|logs|tail|health_check）
以及 environment-level prepare_env / clean_env，**统一由编排器 LLM** 调用：

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --session <S> --env <ENV> --role <ROLE> --instance <INSTANCE> --action <ACTION> \
  [--stage <ST>] [--tail-lines <N>] [--skill pg-fix-issue]
```

| 标志 | 必填 | 说明 |
|------|------|------|
| `--session` | ✅ | session 名（v4 canonical），pg-fix-issue = `fix-<YYYY-MM-DD>-<slug>` |
| `--env` | ✅ | 必须在 `project.yaml` 的 `environments` 列表中 |
| `--role` | ⚠️ | 仅 `--action start\|stop\|restart\|logs\|tail\|health_check` 必填 |
| `--instance` | ⚠️ | 必须在 `environments.<env>.roles.<role>.instances[]` 中 |
| `--action` | ✅ | per-role: `start`/`stop`/`restart`/`logs`/`tail`/`health_check`<br>env-level: `prepare_env`/`clean_env` |
| `--stage` | ❌ | 默认 `manual` |
| `--tail-lines` | ❌ | 仅 `--action logs\|tail` 生效 |
| `--skill` | ❌ | 硬缺省 `ad-hoc`，pg-fix-issue 调用必须显式 `--skill pg-fix-issue` |

**`next_call_timeout_seconds` 处理**：runner 返回的 `__CONFIG__` 段包含
`action_metadata[role][action].timeout_seconds`，LLM 把它作为下一次 bash tool 的
timeout 上限。

## 4. pg-fix-issue Phase 5 触发时机

| 触发时机 | 编排器动作 |
|---------|----------|
| Phase 4 修复前用户选"是 prepare" | `invoke-hook --session <S> --env <ENV> --action prepare_env --skill pg-fix-issue` |
| 修复后某 role 部署验证 | `invoke-hook --session <S> --env <ENV> --role <role> --instance <i> --action start --skill pg-fix-issue` |
| 修复后某 role 停止收尾 | `invoke-hook --session <S> --env <ENV> --role <role> --instance <i> --action stop --skill pg-fix-issue` |
| 看某 role 的日志 | `invoke-hook --session <S> --env <ENV> --role <role> --instance <i> --action logs --tail-lines 100 --skill pg-fix-issue` |
| 验证成功后用户选"是 clean" | `invoke-hook --session <S> --env <ENV> --action clean_env --skill pg-fix-issue` |

## 5. Executor Operations 协议

`pg-fix-issue/executor` 接受的 operation 类型：

| 类型 | 用途 | 备注 |
|------|------|------|
| `test` | 单元测试 | 需 `--resolve-module-test` 解析 |
| `lint` | lint / checkstyle | 需 `--resolve-module-lint` 解析 |
| `shell` | 通用 shell 命令 | service 启停**禁止**走此类型 |
| `api_call` | 调用 API | 含 method / url / expect_field / expect_value |
| `log_filter` | 日志匹配 | service + patterns + expect_found |
| `git_diff_check` | 校验 git diff | forbid_markers（如 "DIAG:"） |

executor 不接触 service 启停（v3.0 强制）；service 启停走 invoke-hook（编排器调用）。

## 6. 占位符替换规约

所有 `<...>` 占位符必须从 `project.yaml` 实际定义替换：

| 占位符 | 查找路径 |
|--------|---------|
| `<role>` | `environments.<env>.roles[*].name` |
| `<instance-name>` | `environments.<env>.roles[<role>].instances[*].name` |
| `<service-host>` | `environments.<env>.roles[<role>].instances[*].host` |
| `<port>` | `environments.<env>.roles[<role>].instances[*].port` |
| `<module-id>` | 顶层 `modules[*]` 的 key |
| `<fixed-symbol>` | 本次修复引入的独有可识别符号 |

**禁止**硬编码 `localhost:9080/api/...` 等示例值。