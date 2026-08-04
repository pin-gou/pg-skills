---
name: pg-fix-issue
description: 修订 BUG
license: MIT
compatibility: 需要 `.pg/project.yaml` 统一配置文件。
metadata:
  author: pg
  version: "3.0"
---

# pg-fix-issue

修复 BUG。LLM 自主完成：诊断 → 修复 → 真实环境验证。
本 SKILL 只提供 pg-* 基础设施协议（LLM 无法自行发现的约定）。

## 1. 启动：配置加载 + 环境探测

session 命名：`fix-<YYYY-MM-DD>-<slug>`（如 `fix-2026-07-30-metrics-1006`）

### 1.1 加载配置

```bash
python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py pg-fix-issue
```

输出 `modules` / `environments` / `tracks` / `stages` 四段。

模块命令解析（**禁止**直接读 `modules.<m>.test.<key>` 原始字段）：

| 用途 | 命令 |
|------|------|
| 构建命令 | `--resolve-module-build <module>` |
| 测试命令 | `--resolve-module-test <module> <test_key>` |
| Lint 命令 | `--resolve-module-lint <module>` |

返回 `{cmd, timeout_seconds}`，`cmd` 已是 `timeout N bash -c '<cmd>'` 形式。

环境详情（含每个 action 的 timeout）：`--resolve-env <env>`

### 1.2 环境探测（describe_env）

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
  --caller pg-fix-issue --session <S> --env <ENV> \
  --action describe_env --skill pg-fix-issue
```

输出 → `.pg/fix-issue/<session>/env-description.yaml`

只读探测，不启停服务。内容包括：

- infra 状态（postgres / libvirtd / s3 可达性）
- 业务服务可达性（backend / frontend / agent-grpc）
- DB sample 数据（host / tenant / project / instance / template_vm / object_storage_service）
- 配置文件 hash / OS / 网络 / 外部依赖 / 跨段依赖关系

读此 YAML 了解环境全貌，用于定位问题和构造验证命令。

## 2. 服务生命周期（invoke-hook 协议）

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
  --caller pg-fix-issue --session <S> --env <ENV> \
  --role <ROLE> --instance <INSTANCE> --action <ACTION> \
  [--tail-lines N] --skill pg-fix-issue
```

| 级别 | 可用 action |
|------|------------|
| per-role | `start` / `stop` / `restart` / `logs` / `tail` / `health_check` |
| env-level | `prepare_env` / `describe_env` / `clean_env` |

每个 action 的 timeout 从 `--resolve-env` 输出的 `action_metadata[role][action].timeout_seconds` 获取，用作 bash tool 的 timeout 参数。

**禁止**直接 `bash .pg/hooks/*.sh`（绕过审计/日志/超时）。

## 3. 工作流程

1. **诊断**：利用 env-description + 代码探索定位根因
2. **展示方案**：向用户展示根因分析 + 修复计划，等用户确认后再动手
3. **修复**：改代码
4. **验证**（硬性，见 §4）
5. **收尾**（见 §5）

## 4. 验证要求（硬性）

修完代码后**必须**做真实环境验证：

1. `invoke-hook --action start` 启动受影响服务
2. 用真实 API 调用验证修复生效（host/port 从 project.yaml 读）
3. `invoke-hook --action logs` 检查服务日志无异常
4. `--resolve-module-test` 跑受影响模块单元测试
5. 验证失败 → 继续修，直到通过或向用户报告无法修复

环境无法启动时：**必须**明确告知用户"未做端到端验证"。
验证命令的输出原样展示给用户。

## 5. 收尾要求

- 不留 DIAG / 临时日志 / 临时脚本
- `git diff` 只含目标文件变更
- 向用户输出：根因 + 改了什么 + 验证证据

## 6. 占位符规约

host / port / role / instance 一律从 project.yaml（通过 `pg-parse-config.py`）读取。
**禁止**硬编码 `localhost:9080` 等示例值。
