# pg-skills AGENTS.md

> v0.8.2 — L1（Level 1）capability layer for AI-driven development workflows.
> 跨项目、语言无关的共享能力层，为 pg-* slash commands、agent 和 pipeline runner 提供底层支持。

---

## 1. 项目概述

pg-skills 是一个**共享运行时 + 技能框架**，嵌入到消费项目仓库的 `.pg/skills/` 目录下（通过 `git subtree`）。它本身不包含项目特定知识，提供两大类能力：

| 层 | 路径 | 职责 |
|----|------|------|
| **Runtime 层** | `src/runtime/` | CLI 入口（`pg`、`pg-invoke-hook.py`、`pg-run`）、hook 执行引擎、TUI、SSOT 规范 |
| **Skill 层** | `src/opencode/` | opencode 集成：8 个 slash command、11 个 SKILL.md、子 agent、配置解析脚本 |

**嵌入模型**：
```
pg-skills 仓库（独立远程）               您的项目仓库
  src/runtime/bin/pg          ── subtree ──→  .pg/skills/
  src/opencode/skills/        ── subtree ──→  .pg/skills/
                                           └── pg init 生成 symlink → .opencode/
```

---

## 2. 架构概览

### 2.1 两层架构

```
┌──────────────────────────────────────────────────────────────┐
│                    Skill 层 (src/opencode/)                   │
│  ┌──────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐  │
│  │ commands │  │  skills   │  │  agents   │  │  scripts  │  │
│  │ (8 个)   │  │ (11 个)   │  │ (sub-     │  │ (config/  │  │
│  │          │  │           │  │  agent)   │  │  test     │  │
│  └──────────┘  └───────────┘  └───────────┘  │  parser)  │  │
│                                               └───────────┘  │
├──────────────────────────────────────────────────────────────┤
│                   Runtime 层 (src/runtime/)                   │
│  ┌───────────┐  ┌─────────┐  ┌──────────┐  ┌────────────┐  │
│  │ bin/ (CLI)│  │ lib/    │  │ spec/    │  │ tests/     │  │
│  │ pg,       │  │ hook_   │  │ error-   │  │ SSOT 一致  │  │
│  │ pg-invoke │  │ runner, │  │ cats,    │  │ 性测试等   │  │
│  │ -hook.py  │  │ tui,    │  │ hook-env │  │            │  │
│  │ pg-run    │  │ helpers │  │ ,schema  │  │            │  │
│  └───────────┘  └─────────┘  └──────────┘  └────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Hook 协议边界

| 维度 | 走 hook 协议？ | 配置位置 | 调度方式 |
|------|---------------|----------|----------|
| **environments**（prepare_env / clean_env / role start/stop/logs） | ✅ | `.pg/hooks/<name>.sh` via `project.yaml` | `pg-invoke-hook.py`，注入 `PG_*` env vars |
| **modules**（build / lint / test） | ❌ | `project.yaml` `modules.<m>.{build,lint,test}` | 直接 `timeout N bash -c '<cmd>'` |

### 2.3 标准工作流

```
/1-pg-define → /2-pg-propose → /3-pg-build → pg-verify-and-merge
                            ↕
                    /2.1-pg-propose-refine
```

快捷流：`/2b-pg-quick-build` → `pg-verify-and-merge`
回归流：`/4-pg-regression`
修复流：`/5-pg-fix-issue`
归档：`/6-pg-archive`

---

## 3. 目录结构

```
pg-skills/
├── VERSION                       # semver: 0.8.2
├── CHANGELOG.md                  # 417 行完整变更日志
├── README.md                     # 761 行主文档
├── AGENTS.md                     # 本文件
│
├── src/
│   ├── opencode/                 # Skill & Agent 层（opencode 集成）
│   │   ├── commands/             # 8 个 slash command 定义
│   │   │   ├── pg-1-define.md          # 探索/设计/定界
│   │   │   ├── pg-2-propose.md          # 提出变更
│   │   │   ├── pg-2.1-propose-refine.md # 按评审意见精炼
│   │   │   ├── pg-2b-quick-build.md     # 跳过 proposal 直接实施
│   │   │   ├── pg-3-build.md            # 执行 tasks.md 构建代码
│   │   │   ├── pg-4-regression.md       # 回归测试
│   │   │   ├── pg-5-fix-issue.md        # 修复问题
│   │   │   └── pg-6-archive.md          # 手动归档
│   │   │
│   │   ├── skills/               # 11 个 SKILL.md 定义
│   │   │   ├── pg-archive/               # 变更归档
│   │   │   ├── pg-browser-testing-with-devtools/  # 浏览器 E2E 测试
│   │   │   ├── pg-build/                 # 事件溯源 pipeline 引擎（最大 skill）
│   │   │   ├── pg-fix-issue/             # Bug 修复工作流
│   │   │   ├── pg-init-project/          # 首次项目初始化
│   │   │   ├── pg-propose/               # 设计提案生成
│   │   │   ├── pg-propose-refine/        # 提案精炼
│   │   │   ├── pg-quick-build/           # 快速构建
│   │   │   ├── pg-regression/            # 回归测试与修复
│   │   │   ├── pg-systematic-diagnosing/ # 系统诊断调试
│   │   │   └── pg-verify-and-merge/      # 验证与合并
│   │   │
│   │   ├── agents/               # 子 agent 定义
│   │   │   └── explore.md               # 代码探索子 agent
│   │   │   # 更多 agent: pg-manager, pg-build/*, pg-fix-issue/* 等
│   │   │
│   │   └── scripts/              # 共享工具脚本
│   │       ├── pg-parse-config.py        # SSOT 查询工具
│   │       ├── pg-parse-test-results.py  # 测试结果解析
│   │       └── tests/                    # 脚本测试
│   │
│   └── runtime/                  # 运行时层
│       ├── bin/                  # CLI 入口点
│       │   ├── pg                      # 主 CLI（init/doctor/upgrade）
│       │   ├── pg-invoke-hook.py       # Hook 统一入口（LLM agent 必须通过此调用）
│       │   ├── pg-run                  # 交互式菜单
│       │   ├── pg-exit                 # 成功退出
│       │   └── pg-fail                 # 失败退出+错误分类
│       │
│       ├── lib/                  # 辅助库
│       │   ├── hook-helpers.sh         # Bash hook 库（pg_start_bg, pg_stop_bg 等）
│       │   ├── pg-run-hook.py          # Hook 执行引擎核心
│       │   └── tui.py                  # 终端 UI 库
│       │
│       ├── spec/                 # SSOT 规范
│       │   ├── error-categories.yaml   # 14 个错误分类（severity/recoverability）
│       │   ├── hook-env-vars.yaml      # PG_* 环境变量 SSOT
│       │   └── project.schema.json     # project.yaml JSON Schema
│       │
│       └── tests/               # 运行时层测试
│
├── examples/                    # 模板与示例
│   ├── shell/
│   │   ├── agent-protocol.md          # Agent 协议 SSOT（必读）
│   │   ├── agents-md-patches.md       # AGENTS.md 漂移检测与修补指南
│   │   └── hooks/                     # 默认 hook 模板（7 文件）
│   │       ├── env-prepare.sh
│   │       ├── env-clean.sh
│   │       ├── role-start.sh
│   │       ├── role-stop.sh
│   │       ├── role-logs.sh
│   │       ├── role-health-check.sh
│   │       ├── lib/common.sh          # 共享 hook 库（236 行）
│   │       └── tests/
│   │
│   └── code-review/            # 代码审查 profile 定义
│       ├── code-review.yaml          # 5 个 profile（default/go/java-spring/security/vue3）
│       ├── default/                  # 5 个检查项
│       ├── go/                       # Go 特定检查
│       ├── java-spring/              # Java/Spring 检查
│       ├── security/                 # 安全检查
│       └── vue3/                     # Vue3 检查
│
├── tools/                       # 开发者工具
│   ├── README.md
│   ├── project-editor.md
│   └── project-editor/              # Vue 3 GUI 编辑器
│       ├── src/
│       │   ├── App.vue
│       │   ├── views/               # Dashboard/FormView/CanvasView
│       │   ├── components/          # 14 个 section 编辑器 + 字段组件
│       │   ├── stores/              # Pinia 状态管理
│       │   ├── utils/               # 工具函数（yaml/diff/hash/coerce）
│       │   └── schema/              # 加载器
│       └── package.json
│
└── docs/
    └── index.html
```

---

## 4. 核心文件与职责

### 4.1 Runtime 核心

| 文件 | 职责 |
|------|------|
| `src/runtime/bin/pg` | 主 CLI：`init`、`doctor`、`upgrade` |
| `src/runtime/bin/pg-invoke-hook.py` | **Hook 统一入口**。LLM agent 必须通过此工具调用 hook，禁止直接 bash hook 脚本 |
| `src/runtime/bin/pg-run` | 交互式菜单：一键启动/停止/构建/测试 |
| `src/runtime/lib/pg-run-hook.py` | 核心 hook 执行器：读取 JSON spec、注入 PG_* env vars、timeout 管理、tee 日志 |
| `src/runtime/lib/hook-helpers.sh` | Bash 库：`pg_start_bg`（setsid detach + PID 写文件）、`pg_stop_bg`（SIGTERM→SIGKILL）、`pg_fail_on_error`、`pg_exit`、`pg_fail` |
| `src/runtime/lib/tui.py` | 终端 UI 交互菜单 |

### 4.2 SSOT 规范

| 文件 | 职责 |
|------|------|
| `src/runtime/spec/error-categories.yaml` | 14 个错误分类：severity（recoverable/blocked）、agent-recoverable、retry_strategy |
| `src/runtime/spec/hook-env-vars.yaml` | PG_* 环境变量 SSOT（v5）：always_injected（3 个）+ spec_injected（9 个）+ removed（5 个） |
| `src/runtime/spec/project.schema.json` | `.pg/project.yaml` 的 JSON Schema（draft-07，556 行） |

### 4.3 Skill 层

| 文件 | 职责 |
|------|------|
| `src/opencode/scripts/pg-parse-config.py` | **SSOT 查询工具**。agent 通过此工具读取 project.yaml，禁止直接读 YAML |
| `src/opencode/agents/explore.md` | 代码探索子 agent（优先使用 CodeGraph） |

### 4.4 文档

| 文件 | 职责 |
|------|------|
| `examples/shell/agent-protocol.md` | **Agent 协议 SSOT**：SSOT 查询规则、hook 调用规则、session-id 约定、日志路由 |
| `examples/shell/agents-md-patches.md` | AGENTS.md 漂移检测与修补清单 |

---

## 5. SSOT 规则

**数据一致性是首要原则。** 以下是必须遵守的 SSOT 规则：

### 5.1 错误分类 SSOT
- **SSOT 位置**：`src/runtime/spec/error-categories.yaml`
- 任何 hook / runtime 代码引用 category，必须从此文件取值
- `pg-fail` 工具也使用此分类

### 5.2 Hook 环境变量 SSOT
- **SSOT 位置**：`src/runtime/spec/hook-env-vars.yaml`
- 改 SSOT 前必须同步：
  1. `src/runtime/lib/pg-run-hook.py:_PG_ENV_MAP`（注入实现）
  2. `README.md §7.1.5`（人类可读表格）
  3. `src/runtime/tests/test_hook_env_vars_ssot.py`（一致性测试）

### 5.3 project.yaml Schema SSOT
- **SSOT 位置**：`src/runtime/spec/project.schema.json`
- 所有 project.yaml 验证工具必须引用此 schema

### 5.4 Agent 协议 SSOT
- **SSOT 位置**：`examples/shell/agent-protocol.md`
- 消费项目通过 `pg init` 复制到 `.pg/context/agent-protocol.md`
- Agent 必须遵守协议规则（见第 7 节）

---

## 6. 开发指南

### 6.1 运行测试

```bash
# 运行时层测试
pytest src/runtime/tests/

# pg-build pipeline 测试（30+ 测试文件）
pytest src/opencode/skills/pg-build/scripts/tests/

# pg-propose 测试
pytest src/opencode/skills/pg-propose/scripts/tests/

# 配置解析测试
pytest src/opencode/scripts/tests/

# Hook 模板测试
pytest examples/shell/hooks/tests/

# 全部测试
pytest
```

### 6.2 验证安装

```bash
python3 src/runtime/bin/pg doctor
```

### 6.3 启动项目编辑器

```bash
cd tools/project-editor && pnpm install && pnpm dev    # 端口 3028
cd tools/project-editor && pnpm build                   # 生产构建
```

### 6.4 开发约定

- **分支策略**：1.0 之前使用单一线形分支（linear branch），所有变更直接提交到 master
- **语言兼容性**：所有 Python 代码必须兼容 **Python 3.7+**（包括 3.7、3.8、3.9、3.10、3.11、3.12）。禁止使用仅在 Python 3.8+ 引入的语法或标准库 API（如 `:=` walrus operator、`functools.cached_property`、`math.prod`、`importlib.metadata`、`Literal` 类型提示等）。如有疑问，在 CI 或本地 Python 3.7 环境验证
- **测试要求**：所有 Python 代码使用 pytest，SSOT 变更必须更新对应一致性测试
- **命名规范**：全小写 + 下划线（snake_case），见 v0.5.x 迁移
- **hook 脚本**：`set -uo pipefail`（不加 `-e`），由 `hook-helpers.sh` trap ERR 控制
- **版本管理**：semver，见 `VERSION` 文件

---

## 7. Agent 协议（与 pg-skills 交互）

> 完整 SSOT 见 `examples/shell/agent-protocol.md`。以下为摘要，agent 必须遵守。

### 7.1 SSOT 查询（必须通过 pg-parse-config.py）

| 目的 | 命令 |
|------|------|
| 拿全部 modules + environments | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-agent` |
| 拿单个模块 build 命令 | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-build <module>` |
| 拿单个模块 test_key | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test <module> <test_key>` |
| 拿环境的 role 信息 | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-env <env>` |
| 拿单值 | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --key <dotted.path>` |
| 拿子树 | `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --prefix <top-level-key>` |

**禁止**：直接读 `.pg/project.yaml`、使用 `pg-parse-config.py pg-build` 等 skill 模式（有噪声）。

### 7.2 Hook 调用（必须通过 pg-invoke-hook.py）

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
  --caller pg-agent \
  --session "$SESSION_ID" \
  --env dev-local \
  --role backend \
  --action start \
  --instance backend-1
```

**session-id 格式**：`<iso-date>-<keyword>`（如 `2026-06-29-fix-bug-42`），一次任务复用同一个。

**禁止**：直接 `bash .pg/hooks/role-backend-start.sh`（绕过审计/日志/超时）。

### 7.3 日志路由

| caller | session 格式 | 日志路径 |
|--------|--------------|----------|
| `pg-agent` | `<iso-date>-<keyword>` | `.pg/agent/<session>/<env>/logs/` |
| `pg-build` | `<change-id>` | `.pg/changes/<change-id>/2-build/<env>/logs/` |
| `pg-fix-issue` | `<change-id>` | `.pg/fix-issue/<change-id>/<env>/logs/` |
| `pg-regression` | `<suite>-<date>-<seq>` | `.pg/regression/<session>/<env>/logs/` |
| `ad-hoc` | `auto-<date>-<pid>` | `.pg/ad-hoc/<session>/<env>/logs/` |

### 7.4 错误分类参考

| category | severity | agent-recoverable | retry |
|----------|----------|-------------------|-------|
| `prereq_missing` | blocked | false | none |
| `port_in_use` | recoverable | true | after_fix |
| `timeout` | recoverable | true | exponential_backoff |
| `health_check_fail` | recoverable | true | wait_and_retry |
| `dependency_not_ready` | recoverable | true | wait_and_retry |
| `network` | recoverable | true | exponential_backoff |
| `permission_denied` | blocked | false | none |
| `config_invalid` | blocked | false | none |
| `resource_exhausted` | blocked | false | none |
| `test_failure` | recoverable | true | none |
| `build_failure` | recoverable | true | none |
| `db_migration_fail` | blocked | true | none |
| `invariant_violation` | blocked | true | none |
| `unknown` | recoverable | false | none |

---

## 8. 常见错误排查

| 错误 | 原因 | 修复 |
|------|------|------|
| `environment not found` | env 名写错 | `pg-parse-config.py --prefix environments` 查看列表 |
| `role 'xxx' not defined` | role 名写错 | `pg-parse-config.py --prefix environments.<env>.roles` |
| `instance 'xxx' not found` | instance 名写错 | 检查 `environments.<env>.roles.<r>.instances` |
| `--caller=pg-agent requires explicit --session` | 忘了传 `--session` | 按 §7.2 生成 session-id |
| 日志找不到 | session-id 拼错或跨任务复用 | 检查 `$SESSION_ID` 是否唯一且正确 |
| `--caller ad-hoc` 总是缺省 | 没显式传 `--caller` | 必须显式 `--caller pg-agent` |