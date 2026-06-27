# pg-skills

> L1 (Level 1) capability layer for AI-driven development workflows.
> Extracted from `oc3-web-virt` project. Cross-project, language-agnostic.

## 1. 概述

`pg-skills` 是 **共享能力层**，为 pg-* slash commands、agent 和 pipeline runner 提供底层支持。设计原则：

- **Language-agnostic** — 适用于 Java/Go/TypeScript/Python/混合栈
- **Project-independent** — 不含项目特定知识（无 `webvirt-*`、无 `pangee`）
- **Embeddable** — 项目通过 `git subtree` 嵌入到 `.pg/skills/` 下消费

### 与本项目的关系

```text
pg-skills 仓库（独立远程）               您的项目仓库
  src/runtime/bin/pg          ── subtree ──→  .pg/skills/
  src/opencode/skills/        ── subtree ──→  .pg/skills/
                                           └── pg init 生成 symlink → .opencode/
```

`pg-skills` 作为 `git subtree` 嵌入到您的项目仓库的 `.pg/skills/` 目录下，通过 `pg init` 将能力以 symlink 形式暴露到 `.opencode/` 中供 opencode 加载。

---

## 2. 快速接入

### 前置条件

- 您的项目仓库已从 pg-skills 仓库同步（`git subtree` / 手动复制 / 其他方式）。`pg init` **不会**自动同步。
- 已安装 Python 3

### 4 步接入

```bash
# 1. 用 git subtree 把 pg-skills 同步进项目
git remote add pg-skills git@gitee.com:shao_hq/pg-skills.git
git fetch pg-skills
git subtree add --prefix=.pg/skills pg-skills master --squash

# 2. 跑 pg init 创建 .pg/ 骨架 + .opencode/ symlink
python3 .pg/skills/src/runtime/bin/pg init
#   - 生成 .pg/{hooks,context,scripts,changes,runs}/ 目录
#   - 首次生成 .pg/project.yaml（placeholder）；已存在则不动
#   - 在 .opencode/agents/ / .opencode/commands/ / .opencode/skills/ 下
#     为 pg-skills 的每项创建逐项 symlink
#   - 幂等，可重复跑

# 3. 重启 opencode
#    opencode 即可加载 pg-* slash commands + pg-* skills + pg-* sub-agents

# 4. 在 opencode 中输入提示词： 加载并执行 pg-init-project skill
#    （opencode 自动扫描仓库结构，生成 .pg/context/repo-scan.md + 实打实的 .pg/project.yaml）

# 5. (可选) 同步 hook 公共库: 上游 SSOT 改动后, 用 cp 覆盖
#    cp .pg/skills/examples/shell/hooks/lib/common.sh .pg/hooks/lib/common.sh
#    默认是 .pg/skills/examples/shell/hooks/lib/common.sh 的副本 (顶部含 SSOT 同步标记).
#    项目特有工具 (port 探测 / 自定义 health check) 可加在 SSOT 标记之后, 不会被同步覆盖.
#    pg doctor 会在 .pg/hooks/lib/common.sh 缺失或不含 pg_resolve_paths 时 WARN.
```

### 预期 .opencode/ symlink 布局

```
.opencode/
├── agents/   <── symlinks: explore.md, pg-manager.md, pg-build/, pg-fix-issue/, pg-quick-build/, pg-regression/
├── commands/ <── symlinks: pg-1-define.md, pg-2-propose.md, pg-2.1-propose-refine.md, pg-2b-quick-build.md,
│                          pg-3-build.md, pg-4-regression.md, pg-5-fix-issue.md, pg-6-archive.md
├── skills/   <── symlinks: git-workflow-and-versioning/, pg-archive/, pg-build/, pg-fix-issue/,
│                          pg-propose/, pg-propose-refine/, pg-quick-build/, pg-regression/,
│                          pg-systematic-diagnosing/, pg-verify-and-merge/, security-and-hardening/,
│                          using-agent-skills/, pg-browser-testing-with-devtools/
└── (无 scripts/ —— pg-skills 的 scripts/ 不通过 symlink 暴露)
```

### 验证

```bash
python3 .pg/skills/src/runtime/bin/pg doctor
```

### 一次性命令速览

```bash
git remote add pg-skills git@gitee.com:shao_hq/pg-skills.git
git fetch pg-skills
git subtree add --prefix=.pg/skills pg-skills master --squash
python3 .pg/skills/src/runtime/bin/pg init
git add .pg/
git commit -m "feat: 接入 pg-skills $(cat .pg/skills/VERSION)"
```

---

## 3. 目录结构

### pg-skills 仓库布局

```
pg-skills/
├── VERSION                       # semver （当前: 0.4.0）
├── CHANGELOG.md
├── README.md                     # 本文件
├── src/
│   ├── opencode/
│   │   ├── commands/             # 8 个 slash 命令（/1-pg-define, /2-pg-propose, ...）
│   │   ├── skills/               # 13 个 SKILL.md 文件（pg-propose, pg-build, ...）
│   │   └── agents/               # 子 agent（explore, pg-manager, ...）
│   └── runtime/                  # 运行时层
│       ├── bin/                  # CLI 入口（pg, pg-invoke-hook.py, ...）
│       ├── lib/                  # Python 辅助模块（hook_runner.py 等）
│       └── spec/                 # SSOT 规范（error-categories.yaml 等）
├── examples/
│   └── shell/hooks/              # 默认 hook 模板（role-{start,stop,logs}.sh + env-{prepare,clean}.sh）
└── tests/                        # 运行时层测试
```

> `examples/` 只放 **environments 维度**的 hook 模板（role / env lifecycle）。module 维度的 build / lint / test 命令**不进 hook 协议**，直接以 `executable_command` 形态写在 `.pg/project.yaml` 的 `modules.<m>.{build, lint, test.<key>}` 字段里。

### 项目接入后的 .pg/ 骨架

| 目录 | 用途 |
|------|------|
| `.pg/hooks/` | 环境 lifecycle hook 脚本（`<role>-<action>.sh`、`prepare_env.sh`、`clean_env.sh`） |
| `.pg/context/` | 项目上下文信息（`repo-scan.md` 等） |
| `.pg/scripts/` | 项目专用脚本 |
| `.pg/changes/` | 变更提案产物（proposal.md、design.md、tasks.md 等） |
| `.pg/runs/` | 运行记录与日志 |
| `.pg/project.yaml` | 项目配置文件，定义 modules / environments / actions |

---

## 4. 日常工作流

> 所有工作流通过 slash command 触发。opencode 加载 `.opencode/commands/` 下的 symlink 后即可使用。

### 标准流：propose → build → verify → merge

| 步骤 | 命令 / skill | 产出 |
|------|-------------|------|
| 定义需求 | `/1-pg-define` 或 `pg-propose` skill | `.pg/changes/<name>/proposal.md` |
| 生成设计 | `/2-pg-propose` | design.md + tasks.md + review-notes.md |
| 构建实现 | `/3-pg-build` | 代码 + 测试 + 验证报告 |
| 验证合并 | `pg-verify-and-merge` skill | 合并到 master |

### 快捷流：跳过 proposal 直接构建

| 步骤 | 命令 / skill | 说明 |
|------|-------------|------|
| 直接编码 | `/2b-pg-quick-build` | 不生成 proposal/design/tasks，直接构建代码与测试 |
| 验证 | `pg-verify-and-merge` skill | 同上 |

### 回归流

| 步骤 | 命令 / skill | 说明 |
|------|-------------|------|
| 跑回归 | `/4-pg-regression` | 执行测试 → 调度 fix-test agent → 输出问题清单 → 可选修复生产代码 |

### 修复流

| 步骤 | 命令 / skill | 说明 |
|------|-------------|------|
| 修复问题 | `/5-pg-fix-issue` | 切 branch → 修复 → git push → 创建 PR |

### 变更归档

| 步骤 | 命令 / skill | 说明 |
|------|-------------|------|
| 手动归档 | `/6-pg-archive` | `pg-build` 成功时自动归档；此项用于脚本失败后或主动放弃时手动归档 |

### 设计精炼

| 步骤 | 命令 / skill | 说明 |
|------|-------------|------|
| 按评审意见修改 | `/2.1-pg-propose-refine` | 读 review-notes.md 决策，按 scope 精准修改 proposal/design/tasks |

---

## 5. 版本管理与升级

### 版本路线图

| 版本 | 特性 |
|------|------|
| **0.1.x** | 骨架 + de-webvirtification |
| **0.2.x** | Hook 协议 + 错误传播 + pg CLI MVP |
| **0.3.x** | 完整项目初始化流程（`pg init` + `pg doctor`）—— **当前** |
| **1.0.x** | 生产就绪，在 2+ 外部项目 dogfood |

### 升级命令

```bash
# 升级到最新版（master）
pg upgrade

# 升级到指定版本
pg upgrade v0.3.0

# 查看远程可用版本
pg upgrade --list

# 工作区有修改时强制升级（自动 stash）
pg upgrade --force

# 交互式升级（冲突多时）
pg upgrade --interactive

# 校验
pg doctor
```

`pg upgrade` 等价于 `git subtree pull --prefix=.pg/skills pg-skills master --squash`。

> 注意：0.1.x 阶段 hook 脚本里 `source $PG_SKILLS_PATH/...` **手动写死绝对路径**。0.2.x 之后 subtree 嵌入，自动用相对路径 `.pg/skills/...`。

---

## 6. 自定义与扩展

### 自定义环境 hook（仅 environments 维度）

如果默认 hook 模板不满足需求，将 `.pg/hooks/` 中的脚本复制一份修改。`pg-run-hook.py` 优先使用项目中的 `.pg/hooks/` 脚本，找不到时回退到 `project.yaml` 中 `environments.<env>.roles.<r>.actions.<action>.script` 字段。

**module 维度的命令（build / lint / test）不允许走 hook 协议**——直接编辑 `.pg/project.yaml` 的 `modules.<m>.{build, lint, test.<key>}` 字段即可。

> ❗ 错误地把 module 命令塞进 hook 会出现双重 timeout + 双重日志路径，runner 不会按 hook 协议调度它。

### 项目专属 agent / command / skill

直接在 `.opencode/<dir>/` 下**真实**放文件，和 symlink 项并存。`pg init` 不会触碰真实文件。

### 跳过 symlink 创建

已有 `.opencode/` 的项目可跳过 symlink 创建：

```bash
python3 .pg/skills/src/runtime/bin/pg init --no-symlinks
```

---

## 7. 参考

### 7.1 Hook 协议

#### 7.1.1 Hook 边界

| 维度 | 走 hook 协议？ | 配置位置 | 调度方式 |
|------|---------------|---------|---------|
| **environments**（prepare_env / clean_env / role start/stop/logs） | ✅ 走 `.pg/hooks/<name>.sh` | project.yaml `environments.<env>.{prepare_env,clean_env}` 和 `.roles.<r>.actions` | `pg-run-hook.py`，注入 `PG_*` env vars |
| **modules**（build / lint / test） | ❌ 不走 hook | project.yaml `modules.<m>.{build, lint, test.<key>}` | 直接 `timeout N bash -c '<cmd>'` 执行 |

> `examples/<lang>/hooks/module-*.sh` 是历史遗留示例，当前 runner 已不再调用它们。如果项目里残留了 `<module>-{build,test,lint}.sh`，是历史遗留，删除即可（`rm .pg/hooks/<m>-*.sh`）。

#### 7.1.2 v4 协议 — caller × session 双维度路由

v4 协议把"日志目录路由"拆成两个**正交维度**，三类调用方（pg-build / pg-regression / pg-fix-issue / ad-hoc）共享同一套接口，但落到不同的目录树：

| 维度 | CLI 字段 | 取值规则 | 作用 |
|------|----------|----------|------|
| **caller**（调用方身份） | `--skill` / `--caller` | `pg-build` / `pg-regression` / `pg-fix-issue` / `ad-hoc`（**硬缺省**） | 一级目录 |
| **session**（工作单元） | `--session` | caller=ad-hoc 时留空自动生成 `auto-<date>-<pid>`；SKILL caller 必填 | 二级目录 |
| **env** | `--env` | 必填 | 三级目录 |

**日志目录路由表**（与 `.pg/hooks/lib/common.sh:pg_resolve_paths` 同步）：

| caller | 日志目录 | session 命名约定 |
|--------|----------|------------------|
| `pg-build` | `.pg/changes/<session>/2-build/<env>/logs/` | 提案名（如 `add-foo-bar`） |
| `pg-regression` | `.pg/regression/<session>/<env>/logs/` | `regression-<suite>-<date>-<seq>` |
| `pg-fix-issue` | `.pg/fix-issue/<session>/<env>/logs/` | `fix-<date>-<slug>` |
| `ad-hoc` | `.pg/ad-hoc/<session>/<env>/logs/` | 留空自动生成 `auto-<date>-<pid>`，或显式传入 |

**为什么是 caller 而不是 skill**：旧协议里 `--skill` 既是"调用方身份"又是"pg-skills 这个项目"的概念。v4 拆开后，调用方身份一律叫 **caller**（写为 `PG_RUN_CALLER`），不再和项目名混淆。

**为什么不双写到旧路径**：pg-build / pg-regression / pg-fix-issue 的日志目录**完全保持现状**（路径不变），不需要迁移；ad-hoc 单独走 `.pg/ad-hoc/` 不污染 SKILL 命名空间。

#### 7.1.3 三种使用场景的调用范式

**场景 A：SKILL 调用（编排流内）**

```bash
# pg-build 子 agent 收到 context 后执行的命令
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --session my-feat --env dev-local \
  --role backend --instance backend-1 --action start \
  --skill pg-build --stage dev
```

**场景 B：pg-run 菜单 / CLI 直达**

```bash
# 交互式菜单（主选项）
./pg-run
# ┌─ pg-run — pg-skills 运行菜单 ──────────────────┐
# │  1) 启动所有实例         一键启动所有角色实例    │
# │  2) 停止所有实例         一键停止所有角色实例    │
# │  3) 准备环境并启动所有   先准备环境再启动所有    │
# │  4) Module 操作          build/lint/test 编译检查│
# │  5) Environment 操作     prepare_env/clean_env   │
# │  6) Role 操作            start/stop/logs/tail    │
# └─────────────────────────────────────────────────┘

# 跳过菜单、直达执行 module 操作
./pg-run --module backend --action build

# 跳过菜单、直达执行 env 操作
./pg-run --env dev-local --action prepare_env

# 跳过菜单、一键启动/停止某 env 的全部角色
./pg-run --env dev-local --action start_all_instances
./pg-run --env dev-local --action stop_all_instances

# 跳过菜单、直达执行 role action
./pg-run --env dev-local --role backend --instance backend-1 --action start

# 跳过菜单、直接执行 shell 命令
./pg-run --cmd "curl -s localhost:9080/health"
# pg-run 内部自动调 pg-invoke-hook.py (无 --session, 留空 → ad-hoc 自动生成 auto-<date>-<pid>)
# caller 缺省 'ad-hoc', session 自动生成
```

**场景 C：agent 不经 SKILL 直接调用（ad-hoc 调试）**

```bash
# 标准 ad-hoc
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --env dev-local --role backend --instance backend-1 --action start

# 调试覆盖日志目录
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --env dev-local --role backend --instance backend-1 --action logs \
  --log-dir /tmp/debug-backend-1

# ad-hoc 临时改超时 (输出 WARN)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --env dev-local --role backend --instance backend-1 --action start \
  --timeout-override 60

# 显式指定 session 名 (便于聚合多次调用)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --session debug-backend-crash --env dev-local \
  --role backend --instance backend-1 --action start
```

**禁止**直接调 `bash .pg/hooks/role-backend-start.sh`——会绕过协议、缺 PG_* env vars、缺 result.json、缺统一超时。

#### 7.1.4 Hook 脚本模板

```bash
#!/usr/bin/env bash
set -euo pipefail
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

# 业务逻辑...

if mvn test -q > "$PG_LOG_FILE" 2>&1; then
    pg_exit --status=pass --duration=42
else
    pg_fail --category=test_failure \
            --code=PG-E-0601 \
            --message="mvn test failed" \
            --hint="Check $PG_LOG_FILE" \
            --agent-recoverable=true
fi
```

#### 7.1.5 注入的环境变量（v4 SSOT）

| 变量 | 说明 | 适用范围 |
|------|------|---------|
| `PG_SKILLS_PATH` | pg-skills 仓库根路径 | 全部 |
| `PG_PROJECT_ROOT` | 项目根路径 | 全部 |
| `PG_RUN_CALLER` | 调用方身份（pg-build / pg-regression / pg-fix-issue / ad-hoc） | 全部 |
| `PG_RUN_SESSION` | session 名（与 caller 正交） | 全部 |
| `PG_RESULT_FILE` | 写 result.json 的路径 | 全部 |
| `PG_LOG_FILE` | 写 stdout/stderr 的路径 | 全部 |
| `PG_STAGE` | 当前 stage 名 | 全部 |
| `PG_ENV` | 当前环境（dev-local / dev-3tier） | 全部 |
| `PG_ROLE` | role 名 | role action 时 |
| `PG_INSTANCE_NAME` | instance 名 | role action 时 |
| `PG_INSTANCE_HOST` | instance host | role action 时 |
| `PG_HOOK_TYPE` | hook 类型（start / stop / restart / logs / tail / prepare / clean） | 全部 |
| `PG_HOOK_LOG_DIR` | 服务内部 stdout/PID 的目标目录（预拼绝对路径，hook 脚本直接信任） | 全部 |

**1 版本 alias**（向下兼容老 hook，写新代码应改用上面的新名）：

| Alias | 新名 |
|-------|------|
| `PG_SKILL_NAME` | `PG_RUN_CALLER` |
| `PG_CHANGE_NAME` | `PG_RUN_SESSION` |

> ~~`PG_MODULE` / `PG_MODULE_ROOT`~~ 已不再注入——module 维度不经过 hook 协议。

#### 7.1.6 `PG_HOOK_LOG_DIR` 的来源与用法

`PG_HOOK_LOG_DIR` 由 `pg-invoke-hook.py` 在 spec 阶段通过 `pg_log_dir_for_caller(caller, session, env, project_root)` 计算，作为 spec 字段 `hook_log_dir` 注入到 `pg-run-hook.py`，再由 `pg-run-hook.py` 写入 ENV。

**预计算逻辑**（与 `pg_log_dir_for_skill()` Python 实现保持一致）：

| caller | session 形式 | `PG_HOOK_LOG_DIR` |
|--------|--------------|-------------------|
| `pg-build` | `<session>`（提案名） | `<root>/.pg/changes/<session>/2-build/<env>/logs` |
| `pg-regression` | `regression-<suite>-<date>-<seq>` | `<root>/.pg/regression/<session>/<env>/logs` |
| `pg-fix-issue` | `fix-<date>-<slug>` | `<root>/.pg/fix-issue/<session>/<env>/logs` |
| `ad-hoc` | `auto-<date>-<pid>` 或显式 | `<root>/.pg/ad-hoc/<session>/<env>/logs` |

**hook 脚本使用约定**：

```bash
PROJECT_ROOT="${PG_PROJECT_ROOT:-$PWD}"
if [ -n "${PG_HOOK_LOG_DIR:-}" ]; then
    LOG_DIR="$PG_HOOK_LOG_DIR"
else
    # 老式手工调用兜底 (建议改走 pg-invoke-hook.py, 不再依赖此分支)
    LOG_DIR="$PROJECT_ROOT/scripts/logs"
fi
PID_DIR="$LOG_DIR"   # logs 与 pids 同目录
mkdir -p "$LOG_DIR"
```

#### 7.1.7 `pg-invoke-hook.py` CLI

LLM **不**直接调 hook 脚本，也不解析 spec；通过 `pg-invoke-hook.py` 统一入口调度。

##### invoke-hook subcommand

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --session <S> --env <ENV> --role <ROLE> \
  --instance <INSTANCE> --action <ACTION> \
  [--stage <ST>] [--tail-lines <N>] \
  [--skill pg-build|pg-regression|pg-fix-issue|ad-hoc] \
  [--log-dir <DIR>] [--timeout-override <SECS>]
```

| 标志 | 必填 | 说明 |
|------|------|------|
| `--session` | ✅¹ | session 名（与 caller 正交）。caller=ad-hoc 时留空自动生成 `auto-<date>-<pid>` |
| `--change` | ❌ | DEPRECATED alias of `--session`（1 版本兼容） |
| `--env` | ✅ | 必须在 project.yaml `environments` 列表中 |
| `--role` | ✅² | backend / frontend / agent。`start/stop/logs/tail` 必填；`prepare_env/clean_env` 忽略 |
| `--instance` | ✅² | 必须在 `environments.<env>.roles.<role>.instances[]` 中 |
| `--action` | ✅ | per-role: `start / stop / logs / tail`；env-level: `prepare_env / clean_env` |
| `--stage` | ❌ | 默认 `manual`；用于 spec.stage 标记 |
| `--tail-lines` | ❌ | 仅 `--action logs\|tail` 生效 |
| `--skill` / `--caller` | ❌ | 调用方身份，**硬缺省 `ad-hoc`**。SKILL 调用必须显式标注 |
| `--log-dir` | ❌ | 显式覆盖日志目录（agent 调试用，优先级最高） |
| `--timeout-override` | ❌ | 覆盖 project.yaml 的 `timeout_seconds`（ad-hoc 调试用，输出 WARN） |

¹ SKILL caller (pg-build / pg-regression / pg-fix-issue) **必须**显式传 `--session`；ad-hoc 留空 → 自动生成 `auto-<date>-<pid>`。

² `--role` / `--instance` 对 env-level actions 是 no-op（CLI parser 不强制、runtime 也忽略）。

##### --tail-lines 语义

- 不传：runner 把 project.yaml `actions.<action>.args` 渲染后原样传给 hook（占位符 `{lines:100}` / `{role}` / `{instance.name}` / `{instance.host}` 由 runner 解析）。
- 传了：runner 把 `--tail-lines <N>` 作为 hook args 数组最后 2 个元素追加，**不**修改 project.yaml。hook 脚本从 `$@` 读取。

##### 超时与连接信息

- `--timeout` / `--host` / `--port` **不是 CLI flag**：
  - `timeout_seconds` 由 runner 从 project.yaml 的 `actions.<action>.timeout_seconds` 反查并写入 spec；`pg-run-hook.py` 通过 `subprocess.run(timeout=...)` 强制执行。
  - host / port 由 runner 从 `instances[]` 自动反查。
- `--timeout-override <N>`：ad-hoc 调试时显式覆盖 `timeout_seconds`，runner 输出 WARN 提示覆盖值。

##### status subcommand（prepare_env 状态查询）

与 `invoke-hook` 平级：

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py status \
  --change <C> [--stage <S>]
```

| 标志 | 必填 | 说明 |
|------|------|------|
| `--change` | ✅ | 当前 change 名（status subcommand 暂未改 `--session`，仅作 runner 透传参数） |
| `--stage` | ❌ | 可选 stage 名过滤 |

典型用法：在 verify agent 中查询 prepare_env 是否已成功执行，避免硬编码 log_path。

> 历史兼容：`pg-pipeline-runner.py prepare-env-status <C> [stage]` 仍可用，`pg-invoke-hook.py status` 是统一 runtime 入口。

#### 7.1.8 历史兼容与迁移

- `pg-pipeline-runner.py invoke-hook` 仍然可用（thin wrapper 转发到 `pg-invoke-hook.py`），但新代码统一走新路径。
- `--change` 字段保留 1 版本作为 deprecated alias；SKILL / pg-run / agent 调用方应改为 `--session`。
- `PG_SKILL_NAME` / `PG_CHANGE_NAME` env var 保留 1 版本作为 alias；hook 写新代码应改为 `PG_RUN_CALLER` / `PG_RUN_SESSION`。
- 旧 `.pg/changes/manual/` 目录里的历史日志保留为只读归档，新调用**不再**追加。

#### 错误 Category 枚举

| category | severity | agent-recoverable | retry | 用途 |
|----------|----------|-------------------|-------|------|
| `prereq_missing` | blocked | false | none | 缺前置命令/库 |
| `port_in_use` | recoverable | true | after_fix | 端口占用 |
| `timeout` | recoverable | true | exponential_backoff | 操作超时 |
| `health_check_fail` | recoverable | true | wait_and_retry | 健康检查失败 |
| `dependency_not_ready` | recoverable | true | wait_and_retry | 依赖未就绪 |
| `network` | recoverable | true | exponential_backoff | SSH/HTTP/DNS 失败 |
| `permission_denied` | blocked | false | none | 权限不足 |
| `config_invalid` | blocked | false | none | 配置错误 |
| `resource_exhausted` | blocked | false | none | 磁盘/内存 |
| `test_failure` | recoverable | true | none | 测试断言失败 |
| `build_failure` | recoverable | true | none | 编译失败 |
| `db_migration_fail` | blocked | true | none | 数据库迁移失败 |
| `invariant_violation` | blocked | true | none | 跨模块不变量违反 |
| `unknown` | recoverable | false | none | 未分类（兜底） |

SSOT 见 `src/runtime/spec/error-categories.yaml`。

### 7.2 CLI 命令参考速查

| 命令 | 用途 | 典型调用 |
|------|------|---------|
| `pg init` | 初始化项目 .pg/ 骨架 + .opencode/ symlink | `python3 .pg/skills/src/runtime/bin/pg init` |
| `pg init --no-symlinks` | 仅初始化 .pg/ 骨架，不创建 symlink | 已有 .opencode/ 的项目 |
| `pg upgrade [version]` | 升级 pg-skills 版本 | `pg upgrade v0.3.0` |
| `pg upgrade --list` | 查看远程可用版本 | — |
| `pg upgrade --force` | 工作区脏时自动 stash 后升级 | — |
| `pg upgrade --interactive` | 交互式解决冲突 | — |
| `pg doctor` | 校验安装状态 | `python3 .pg/skills/src/runtime/bin/pg doctor` |
| `pg-invoke-hook.py invoke-hook` | 触发环境 hook | 见 7.1 节 |
| `pg-invoke-hook.py status` | 查询 prepare_env 状态 | 见 7.1 节 |
| `./pg-run` | 交互式菜单运行 | `./pg-run` 或 `./pg-run --module backend --action build` |

---

## 8. 参与开发

```bash
# Clone
git clone git@gitee.com:shao_hq/pg-skills.git
cd pg-skills

# 跑运行时测试
pytest tests/

# 校验
python3 src/runtime/bin/pg doctor
```

Until 1.0, **all changes go through a single linear branch**. After 1.0, this repo will adopt the standard branch + PR workflow.

---

## 9. License

Internal project — see project root for license terms.
