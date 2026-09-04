# pg-skills AGENTS.md

> v0.9.0 — L1（Level 1）capability layer for AI-driven development workflows.
> 跨项目、语言无关的共享能力层，为 pg-* slash commands、agent 和 pipeline runner 提供底层支持。

---

## 1. 项目概述

pg-skills 是一个**共享运行时 + 技能框架**，嵌入到消费项目仓库的 `.pg/skills/` 目录下（通过 `git subtree`）。它本身不包含项目特定知识，提供两大类能力：

| 层 | 路径 | 职责 |
|----|------|------|
| **Runtime 层** | `src/runtime/` | CLI 入口（`pg`、`pg-invoke-hook.py`、`pg-run`）、hook 执行引擎、TUI、SSOT 规范 |
| **Workflow 核心层** | `src/core/workflows/` | 与开发工具无关的 command、SKILL.md、agent 规范和配置解析脚本；由 `src/integrations/` 转换为各工具格式 |

**嵌入模型**：
```
pg-skills 仓库（独立远程）               您的项目仓库
  src/runtime/bin/pg          ── subtree ──→  .pg/skills/
  src/core/workflows/skills/        ── subtree ──→  .pg/skills/
                                           └── pg init 生成 symlink → .opencode/
```

---

## 2. 架构概览

### 2.1 两层架构

```
┌──────────────────────────────────────────────────────────────┐
│                    Skill 层 (src/core/workflows/)                   │
│  ┌──────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐  │
│  │ commands │  │  skills   │  │  agents   │  │  scripts  │  │
│  │ (9 个)   │  │ (12 个)   │  │ (sub-     │  │ (config/  │  │
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
/1-pg-define → /2-pg-propose → /3-pg-build → pg-verify-and-merge（仅用户显式触发，如"verify 并合并"）
```

> **v0.8.4 起**：`/2.1-pg-propose-refine` 已删除。5 项 common decisions 固化为 `pg-gen-tasks-skeleton.py` 常量块；产物生成后直接进入 `/3-pg-build`。

> **pg-* 工作流 skill 仅限用户显式触发**：pg-define / pg-propose / pg-build / pg-fix-issue / pg-quick-build / pg-regression / pg-verify-and-merge 只在用户通过对应 `/pg-*` 命令或明确自然语言请求时加载；pg-build 完成后**不自动触发** pg-verify-and-merge，由用户明确指示后执行。
>
> **例外：`pg-auto-pilot`（`/0-pg-auto-pilot`）** 是自动驾驶模式——LLM 可自主加载，不归入上述门控。它不限定 LLM 如何规划与执行，只要求实施计划含"启动实例并验证结果"步骤、执行前让用户选定环境并确认环境准备方式。

快捷流：`/2b-pg-quick-build` → `pg-verify-and-merge`（用户显式触发）
回归流：`/4-pg-regression`
修复流：`/5-pg-fix-issue`
归档：`/6-pg-archive`

---

## 3. 目录结构

```
pg-skills/
├── VERSION                       # semver: 0.9.0
├── CHANGELOG.md                  # 417 行完整变更日志
├── README.md                     # 761 行主文档
├── AGENTS.md                     # 本文件
│
├── src/
│   ├── opencode/                 # Skill & Agent 层（opencode 集成）
│   │   ├── commands/             # 9 个 slash command 定义
│   │   │   ├── pg-0-auto-pilot.md      # 自动驾驶模式（壳子，调用 pg-auto-pilot skill）
│   │   │   ├── pg-1-define.md          # 探索/设计/定界（壳子，调用 pg-define skill）
│   │   │   ├── pg-1-grill.md           # 设计树拷问模式（壳子，调用 pg-define skill 的 grill 模式）
│   │   │   ├── pg-2-propose.md          # 提出变更
│   │   │   ├── pg-2b-quick-build.md     # 跳过 proposal 直接实施
│   │   │   ├── pg-3-build.md            # 执行 tasks.md 构建代码
│   │   │   ├── pg-4-regression.md       # 回归测试
│   │   │   ├── pg-5-fix-issue.md        # 修复问题
│   │   │   └── pg-6-archive.md          # 手动归档
│   │   │
│   │   ├── skills/               # 12 个 SKILL.md 定义
│   │   │   ├── pg-archive/               # 变更归档
│   │   │   ├── pg-browser-testing-with-devtools/  # 浏览器 E2E 测试
│   │   │   ├── pg-build/                 # 事件溯源 pipeline 引擎（最大 skill）
│   │   │   ├── pg-define/                # 探索/设计/定界
│   │   │   ├── pg-fix-issue/             # Bug 修复工作流
│   │   │   ├── pg-init-project/          # 首次项目初始化
│   │   │   ├── pg-propose/               # 设计提案生成
│   │   │   ├── pg-quick-build/           # 快速构建
│   │   │   ├── pg-regression/            # 回归测试与修复
│   │   │   ├── pg-systematic-diagnosing/ # 系统诊断调试
│   │   │   ├── pg-verify-and-merge/      # 验证与合并
│   │   │   └── pg-auto-pilot/          # 自动驾驶模式：不限定 LLM 编排，仅要求计划含验证、执行前确认环境
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
| `src/runtime/spec/env-description.schema.json` | `.pg/changes/<change-id>/env-description.yaml` 的 JSON Schema（6 段 + relations，474 行） |
| `src/runtime/spec/define-summary.schema.json` | `.pg/changes/<change-id>/0-define/define-summary.yaml` 的 JSON Schema（schema v1，163 行）。pg-propose 阶段 1.8 校验，pg-1-define「定界后环境验证」环节落盘 |

### 4.3 Skill 层

| 文件 | 职责 |
|------|------|
| `src/core/workflows/scripts/pg-parse-config.py` | **SSOT 查询工具**。agent 通过此工具读取 project.yaml，禁止直接读 YAML |
| `src/core/workflows/agents/explore.md` | 代码探索子 agent（优先使用 CodeGraph） |

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
pytest src/core/workflows/skills/pg-build/scripts/tests/

# pg-propose 测试
pytest src/core/workflows/skills/pg-propose/scripts/tests/

# 配置解析测试
pytest src/core/workflows/scripts/tests/

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
- **语言兼容性**：所有 Python 代码必须兼容 **Python 3.7+**（包括 3.7、3.8、3.9、3.10、3.11、3.12）。所有文件已统一添加 `from __future__ import annotations`，使 PEP 604 `X | Y` 联合类型语法（如 `str | None`）在注解中安全可用。禁止使用以下仅在更高版本引入的语法或标准库 API：
  - `match`/`case` 结构模式匹配（3.10+）
  - `dataclass` 的 `slots=True` 参数（3.10+）
  - `zip(strict=True)` 参数（3.10+）
  - `int.bit_count()` 方法（3.8+）
  - `exceptiongroup` / `except*`（3.11+）
  - `enum.StrEnum`（3.11+）
  - `tomllib`（3.11+）
  - `typing.Self`、`typing.TypeAlias`、`typing.Literal` 等 3.10+ 新增的 typing 类型（需从 `typing_extensions` 导入）
  - `functools.cached_property`、`math.prod`、`importlib.metadata`（3.8+）
  - 海象运算符 `:=` 和仅位置参数 `/`（3.8+）

  如有疑问，在 CI 或本地 Python 3.7 环境验证。
- **测试要求**：所有 Python 代码使用 pytest，SSOT 变更必须更新对应一致性测试
- **命名规范**：全小写 + 下划线（snake_case），见 v0.5.x 迁移
- **hook 脚本**：`set -uo pipefail`（不加 `-e`），由 `hook-helpers.sh` trap ERR 控制
- **版本管理**：semver，见 `VERSION` 文件
  - **同步要求**：更新 VERSION 时，必须同步修改所有文档/代码中 `git subtree add --prefix=.pg/skills pg-skills v<old> --squash` 命令中的版本号为新版本。当前受影响文件：`README.md`、`docs/index.html`、`docs/pg-skills.md`、`docs/cards/07-onboarding.svg`、`src/core/init.py`。
- **变更日志**：每个版本在 `CHANGELOG.md` 顶部新增一个 section，标题为 `[<版本>] - <发布日期>`，版本号与日期和 `VERSION` 文件保持一致。撰写要求：
  - **从用户视角撰写**：简明扼要，写"变更对用户的影响"（用户得到什么、需要做什么），不要罗列实现细节、函数名或内部机制
  - **破坏性变更优先**：需要用户主动修改的内容（如 project.yaml 格式、命令/字段变更）放在最前，标注"升级前必读"，明确说明改什么
  - **发布前核对**：用 `git log --oneline v<prev-tag>..HEAD` 列出全部 commit，确保每个用户可见变更都有对应条目，不遗漏、不夸大；写完后用 `git diff CHANGELOG.md` 自查

---

## 7. Agent 协议（与 pg-skills 交互）

> 完整 SSOT 见 `examples/shell/agent-protocol.md`。以下为摘要，agent 必须遵守。

### 7.1 SSOT 查询（必须通过 pg-parse-config.py）

| 目的 | 命令 |
|------|------|
| 拿全部 modules + environments | `python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py pg-agent` |
| 拿单个模块 build 命令 | `python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py --resolve-module-build <module>` |
| 拿单个模块 test_key | `python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py --resolve-module-test <module> <test_key>` |
| 拿环境的 role 信息 | `python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py --resolve-env <env>` |
| 拿单值 | `python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py --key <dotted.path>` |
| 拿子树 | `python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py --prefix <top-level-key>` |

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
