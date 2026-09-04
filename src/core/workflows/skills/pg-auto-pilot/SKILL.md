---
name: pg-auto-pilot
description: 自动驾驶模式——不限定 LLM 如何规划与执行，只提两条要求：(1) 实施计划中必须包含"启动实例并验证编码结果是否达到预期"的步骤；(2) 执行计划前，让用户选定环境，并确认是需要准备环境，还是环境已就绪（跳过准备）。适合在 pg-skills 项目中做一次会话内即可完成的实现任务。
license: MIT
compatibility: 需要 .pg/project.yaml 与 pg-skills 运行时（pg-parse-config.py + pg-invoke-hook.py）。
metadata:
  author: pg
  version: "1.0"
---

# pg-auto-pilot

给 LLM 的**两条要求**，不规定怎么规划、怎么分工、用什么手段——怎么做完全由 LLM 自主决定（包括与用户交互确定范围）。

---

## 两条要求

### 要求 1：实施计划必须包含"启动实例并验证结果"

无论任务怎么规划，**实施计划里必须要有启动实例并验证编码结果是否达到预期这一步**：

- 启动实例：在选定的环境中把对应 role 的实例跑起来（走 hooks 协议，见下）
- 验证结果：用你能想到的手段确认编码结果达到预期（build/lint/test、health_check、运行时检查、回归等，手段不限）
- 验证不通过 → 修复 → 重验，直到通过或上报用户

### 要求 2：执行计划前，先让用户选定环境并确认准备方式

**执行计划之前**，必须先和用户对齐两件事：

1. **选定环境**（哪个 env，如 dev-local）
2. **是否需要准备环境**：
   - 需要准备 → 执行环境准备（prepare_env）后再启动实例
   - 环境已就绪 → **跳过环境准备步骤**，直接进入启动实例

---

## 使用边界

本 SKILL 不要求落盘 proposal/design/tasks、不派发 sub-agent、不定义阶段状态机。**不负责合并**（pg-verify-and-merge 的职责）、不修历史、不落变更文档。

---

## 走 hooks 协议时的基操（仅供选用，不强制步骤顺序）

> 若你选择用 pg-skills 的钩子协议来启动实例 / 验证，遵循以下纪律。你也可以用项目里现成的其他手段——只要实现上面两条要求即可。

### 配置必须走 SSOT

```bash
python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py pg-agent
```

- `pg-agent` 模式只暴露 modules + environments，无 skill 内部噪声
- 需要具体值用 `--resolve-env <env>` / `--resolve-module-build <module>` / `--resolve-module-test <module> <key>` / `--key <dotted.path>` / `--prefix <top-level-key>`
- 禁止直接读 `.pg/project.yaml`（绕过 SSOT 会拿到 stale 副本）

### Hook 必须走 pg-invoke-hook.py

```bash
SESSION_ID="$(date -u +%Y-%m-%d)-<keyword>"   # 一次任务复用，如 2026-09-04-fix-login

python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
  --caller pg-agent --session "$SESSION_ID" \
  --env <env> --role <role> --action <action> --instance <inst>
```

- `--caller` **固定 `pg-agent`**，日志路由到 `.pg/agent/<session>/<env>/logs/`
- action 分两类：
  - per-role：`start / stop / restart / logs / tail / health_check`（需 `--role` + `--instance`）
  - env-level：`prepare_env / clean_env / describe_env / restart_all_instances`（忽略 role/instance）
- session-id 格式 `<iso-date>-<keyword>`，一次任务复用同一个，任务结束换新（否则污染审计目录）

### 错误分类

hook 失败时先识别 category，再决定重试 / 上报：

| category | severity | agent-recoverable | 处理 |
|----------|----------|-------------------|------|
| `port_in_use` | recoverable | true | 解除端口冲突后重试 |
| `timeout` | recoverable | true | 指数退避重试 |
| `health_check_fail` | recoverable | true | 等待再重试 |
| `dependency_not_ready` | recoverable | true | 等依赖就绪 |
| `network` | recoverable | true | 指数退避重试 |
| `test_failure` / `build_failure` | recoverable | true | 定位修复后可重试 |
| `prereq_missing` / `config_invalid` / `permission_denied` / `resource_exhausted` | blocked | false | 上报用户，不自行绕过 |
| `unknown` | recoverable | false | 上报用户 |

---

## 典型流程（仅供参考，可自由裁剪）

```
0. 明确任务范围 → 形成实施计划（必须含"启动实例 + 验证结果"）
1. 执行前：请用户选定环境 + 确认是否需要准备环境
   → 需要准备 → prepare_env（env-level）
   → 已就绪 → 跳过准备，直接下一步
2. 启动所需实例：--action start（--role ... --instance ...）
   → health_check 通过才算就绪；失败按上表分类处理
3. 执行编码工作 + 验证：build/lint/test、运行时检查等（手段不限）
   → 失败 → 修复 → 重验
4. [可选] 收尾：--action logs / tail 确认真实运行日志无异常
   → 询问用户是否停止实例 / clean_env
5. 汇总报告：改动内容、验证结果、hook 日志位置
```
