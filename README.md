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
git remote add pg-skills git@github.com:pin-gou/pg-skills.git
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

# 5. (可选) 同步 hook 公共库: 仅升级后需要；`pg init` 已自动复制 common.sh。
#    如 `pg doctor` 报 `pg_resolve_paths` 缺失则手动 cp：
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
├── skills/   <── symlinks: pg-archive/, pg-browser-testing-with-devtools/, pg-build/, pg-fix-issue/,
│                          pg-init-project/, pg-propose/, pg-propose-refine/, pg-quick-build/,
│                          pg-regression/, pg-systematic-diagnosing/, pg-verify-and-merge/
└── (无 scripts/ —— pg-skills 的 scripts/ 不通过 symlink 暴露)
```

### 验证

```bash
python3 .pg/skills/src/runtime/bin/pg doctor
```

### 一次性命令速览

```bash
git remote add pg-skills git@github.com:pin-gou/pg-skills.git
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
├── VERSION                       # semver （当前: 0.8.0）
├── CHANGELOG.md
├── README.md                     # 本文件
├── src/
│   ├── opencode/
│   │   ├── commands/             # 8 个 slash 命令（/1-pg-define, /2-pg-propose, ...）
│   │   ├── skills/               # 11 个活跃 SKILL.md（pg-archive, pg-browser-testing-with-devtools, pg-build, pg-fix-issue, pg-init-project, pg-propose, pg-propose-refine, pg-quick-build, pg-regression, pg-systematic-diagnosing, pg-verify-and-merge）
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
| 生成设计 | `/2-pg-propose` | design.md + tasks.md + execution-manifest.yaml |
| 构建实现 | `/3-pg-build` | 事件溯源引擎驱动，runner 自动编排 sub-agent |
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
| **0.3.x** | 完整项目初始化流程（`pg init` + `pg doctor`） |
| **0.4.x** | v4 Hook 协议 — caller × session 双维度路由 + pg-run 菜单 |
| **0.5.x** | 字段统一为 snake_case + 配置重构 |
| **0.6.x** | pg-build 事件溯源引擎 + pg-agent workflow + health check |
| **0.7.x** | pg-build v2 取代 v1 + 路径简化 + execution-manifest.yaml SSOT + pg-regression A/B/C 修复边界 |
| **0.8.0** | pg-build v2.6 review 阶段 + code-review profile 引擎（5 profile）+ pg-propose tasks.md 骨架脚本外化 + pg-fix-issue v3.2 重构 + code_view→code_review 重命名 + 品构品牌命名 —— **当前** |
| **1.0.x** | 生产就绪，在 2+ 外部项目 dogfood（未达） |

### 升级命令

```bash
# 升级到最新版（master）
pg upgrade

# 升级到指定版本
pg upgrade v0.8.0

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
set -uo pipefail  # 注意: 不加 -e, 由 hook-helpers.sh trap ERR 控制
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

##### Start hook 模板（后台服务启动）

```bash
#!/usr/bin/env bash
set -uo pipefail
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

PROJECT_ROOT="${PG_PROJECT_ROOT:-$PWD}"
source ".pg/hooks/lib/common.sh"
pg_resolve_paths

# 端口冲突先清理
if check_port $BACKEND_PORT; then
    kill_port $BACKEND_PORT "Backend"
    sleep 1
fi

# pg_start_bg: 一行替代 setsid+redirect+PID 写入
# env_kv 走 argv 注入, 无 shell 解析; setsid 自动 detach;
# hook 退出后服务仍存活 (避免 opencode 120s shell 超时杀掉)
backend_pid=$(pg_start_bg \
    "$LOG_DIR/backend.log" \
    "$PID_DIR/backend.pid" \
    "WEBVIRT_KEY=xxx" "SPRING_PROFILE=grpc" -- \
    mvn spring-boot:run -pl webvirt-bootstrap -DskipTests)

# 端口就绪检查 (可选)
wait_for_port_with_monitor $BACKEND_PORT "Backend" 60 \
    "$PID_DIR/backend.pid" "$LOG_DIR/backend.log"

pg_exit --status=pass
```

**Stop hook 模板**：

```bash
#!/usr/bin/env bash
set -uo pipefail
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

PROJECT_ROOT="${PG_PROJECT_ROOT:-$PWD}"
source ".pg/hooks/lib/common.sh"
pg_resolve_paths

# pg_stop_bg: SIGTERM → 5s 宽限 → SIGKILL, 幂等
pg_stop_bg "$PID_DIR/backend.pid" "Backend"

# 端口残留清理
if check_port $BACKEND_PORT; then
    kill_port $BACKEND_PORT "Backend (residual)"
fi

pg_exit --status=pass
```

#### 7.1.5 注入的环境变量（v5 SSOT）

**机器可读 SSOT**：`.pg/skills/src/runtime/spec/hook-env-vars.yaml`。
本节表格与 YAML 文件双向同步，一致性由 `tests/test_hook_env_vars_ssot.py` 校验。
任何新增/删除 PG_* var 必须先改 YAML，再同步本表 + `_PG_ENV_MAP`。

##### 硬注入（与 spec 无关，pg-run-hook.py 必填）

| 变量 | 类型 | 说明 |
|---|---|---|
| `PG_PROJECT_ROOT` | path | 项目根路径 |
| `PG_SKILLS_PATH` | path | pg-skills 仓库根 |
| `PG_RUN_CALLER` | enum | 调用方身份（pg-build / pg-regression / pg-fix-issue / ad-hoc），硬缺省 `ad-hoc` |

##### Spec 注入（pg-run-hook.py:_PG_ENV_MAP 由 spec 字段驱动）

| 变量 | Spec key | 适用范围 | 说明 |
|---|---|---|---|
| `PG_RUN_SESSION` | `session` | 全部 | session 名（与 caller 正交） |
| `PG_STAGE` | `stage` | 全部 | 当前 stage 名 |
| `PG_ENV` | `env` | 全部 | 当前 environment（dev-local / dev-3tier） |
| `PG_ROLE` | `role` | per-role | role 名 |
| `PG_INSTANCE_NAME` | `instance_name` | per-role | instance 名 |
| `PG_INSTANCE_HOST` | `instance_host` | per-role | instance host |
| `PG_HOOK_TYPE` | `hook_type` | 全部 | hook 类型（start / stop / logs / tail / prepare_env / clean_env） |
| `PG_HOOK_LOG_DIR` | `hook_log_dir` | 全部 | 预拼日志绝对目录（lib/common.sh:pg_resolve_paths 优先信任） |
| `PG_LOG_FILE` | `log_path` | 全部 | hook stdout/stderr 目标路径 |
| `PG_RESULT_FILE` | `hook_result_path` | 全部 | hook 写 result.json 的路径 |

##### 已废弃（v4 → v5 移除，不再注入）

| 变量 | 原语义 | 替代 |
|---|---|---|
| `PG_SKILL_NAME` | 1 版本 alias of PG_RUN_CALLER | `PG_RUN_CALLER` |
| `PG_CHANGE_NAME` | 1 版本 alias of PG_RUN_SESSION | `PG_RUN_SESSION` |
| `PG_RUNNER_ORIGIN` | legacy alias of PG_RUN_CALLER | `PG_RUN_CALLER` |
| `PG_MODULE` | module 维度不进 hook 协议 | n/a（module 命令改 `pg-run --module X --action build`，`cwd=<project_root>/<module.root>`） |
| `PG_MODULE_ROOT` | 同上 | 同上 |

#### 7.1.6 `PG_HOOK_LOG_DIR` vs `PG_LOG_FILE` — 用途与区别

这两个变量名字相近但承担完全不同的角色，hook 写代码前必须分清。

| 维度 | `PG_HOOK_LOG_DIR` | `PG_LOG_FILE` |
|---|---|---|
| **类型** | 目录路径 | 文件路径 |
| **谁写入** | hook 脚本自己 | `pg-run-hook.py` 用 tee 模式自动写入 |
| **承载内容** | hook 派生的多份文件：`backend.log` / `frontend.log` / `agent.log` + PID 文件等 | hook 自身的 stdout/stderr |
| **hook 端的职责** | 派生 `LOG_DIR` / `PID_DIR`，在目录下写**业务日志**（如启动的后台进程日志） | 业务命令输出重定向到这里（`mvn test > "$PG_LOG_FILE" 2>&1`） |
| **依赖方** | `lib/common.sh:pg_resolve_paths` 优先信任 | `hook-helpers.sh:pg_fail_on_error` trap 用它 tail 诊断 |
| **典型用法** | `LOG_DIR="$PG_HOOK_LOG_DIR"; mkdir -p "$LOG_DIR"; setsid mvn ... > "$LOG_DIR/backend.log" &` | `mvn test -q > "$PG_LOG_FILE" 2>&1` |

**记忆口诀**：
- `PG_HOOK_LOG_DIR` = **D**irectory，hook 自管的业务日志**目录**
- `PG_LOG_FILE` = **F**ile，caller 给 hook 开的 stdout/stderr **单文件**

---

##### `PG_HOOK_LOG_DIR` 的来源

由 `pg-invoke-hook.py` 在 spec 阶段通过 `pg_log_dir_for_caller(caller, session, env, project_root)` 计算，作为 spec 字段 `hook_log_dir` 注入到 `pg-run-hook.py`，再由 `pg-run-hook.py` 写入 ENV。

**预计算逻辑**（与 `pg_log_dir_for_skill()` Python 实现保持一致）：

| caller | session 形式 | `PG_HOOK_LOG_DIR` |
|--------|--------------|-------------------|
| `pg-build` | `<session>`（提案名） | `<root>/.pg/changes/<session>/2-build/<env>/logs` |
| `pg-regression` | `regression-<suite>-<date>-<seq>` | `<root>/.pg/regression/<session>/<env>/logs` |
| `pg-fix-issue` | `fix-<date>-<slug>` | `<root>/.pg/fix-issue/<session>/<env>/logs` |
| `ad-hoc` | `auto-<date>-<pid>` 或显式 | `<root>/.pg/ad-hoc/<session>/<env>/logs` |

##### `PG_LOG_FILE` 的来源

由 `pg-invoke-hook.py` 在 spec 阶段根据 caller 路由到 `$PG_HOOK_LOG_DIR` 下，命名格式：

- per-role action: `$PG_HOOK_LOG_DIR/role.<role>.<action>@<instance>.log`
- env-level action: `$PG_HOOK_LOG_DIR/env.<action>.log`

作为 spec 字段 `log_path` 注入到 `pg-run-hook.py`，`run_command()` 用 tee 模式边写边读 stdout/stderr。

---

##### hook 脚本使用约定

```bash
PROJECT_ROOT="${PG_PROJECT_ROOT:-$PWD}"

# 1. LOG_DIR: 优先用 caller 预拼的目录, 没有时兜底
if [ -n "${PG_HOOK_LOG_DIR:-}" ]; then
    LOG_DIR="$PG_HOOK_LOG_DIR"
else
    LOG_DIR="$PROJECT_ROOT/scripts/logs"   # 手工调用兜底
fi
PID_DIR="$LOG_DIR"
mkdir -p "$LOG_DIR"

# 2. 启动后台进程: 业务日志写到 LOG_DIR 下 (PG_HOOK_LOG_DIR 派生)
setsid mvn spring-boot:run > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$PID_DIR/backend.pid"

# 3. hook 自身 stdout/stderr: 重定向到 PG_LOG_FILE (caller 给的固定路径)
#    若 hook 顶层没有自己的输出, 这步可省略 (pg-run-hook.py 会自动 tee)
if [ -n "${PG_LOG_FILE:-}" ]; then
    exec > "$PG_LOG_FILE" 2>&1
fi
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
- 旧 `.pg/changes/manual/` 目录里的历史日志保留为只读归档，新调用**不再**追加。
- v5 起 `PG_SKILL_NAME` / `PG_CHANGE_NAME` / `PG_RUNNER_ORIGIN` 已从注入实现移除，老 hook 须改用 `PG_RUN_CALLER` / `PG_RUN_SESSION`。
- `lib/common.sh:kill_pid_file` 已弃用，迁移到 `hook-helpers.sh:pg_stop_bg`（保留 kill_pid_file 作为兼容垫片，打 WARN）。

#### 7.1.9 后台服务生命周期（start / stop）

##### 问题背景

`opencode` 默认 shell 命令 120s 过期，**hook 进程超时被杀**会导致两种后果：

1. 如果 hook 在前台跑业务命令（`mvn spring-boot:run`），超时 → hook 退出 → 业务进程**也挂掉**（无 detach）
2. 如果 hook 用 `setsid ... &` 启动后台服务，hook 退出后服务**继续运行**，但 `pg-run-hook.py` 的 timeout 仍在等 hook 进程退出 → 误报"超时失败"

第二种情况若 hook 内部 wait_for_port_with_monitor 通过但 hook 进程不退，300s timeout 一到，`pg-run-hook.py:run_command` 会 `proc.kill()`。**幸运的是**：`kill` 只杀 hook 进程本身，已 setsid detach 到新 session 的孙子进程不受影响。

但更干净的做法是让 hook **spawn 后立刻返回**——这就是 fire-and-forget 模式。

##### 协议层：`wait_for_completion`

`pg-run-hook.py` spec 加 `wait_for_completion` 字段（bool，默认 True）：

- `True`（默认）：标准模式。`pg-run-hook.py` `proc.wait(timeout=...)`，超时 `proc.kill()`。
- `False`：fire-and-forget。`pg-run-hook.py` 等短时间（`min(timeout, 30)` 秒）让 hook 完成 spawn，然后立即 `proc.kill()` 释放 stdio，**不杀孙子进程**（setsid 后已 detach）。

`pg-invoke-hook.py invoke-hook` 默认行为：

| action | `wait_for_completion` | 备注 |
|---|---|---|
| `start` | `False`（默认 fire-and-forget） | 服务用 `pg_start_bg` detach 后立即返回 |
| `stop` / `logs` / `tail` | `True`（强制等完） | 这些 action 必须看 hook 退出码才有意义 |
| `prepare_env` / `clean_env` | `True`（环境级始终等） | env-level hook 不需要 detach |

CLI override：`--no-wait-for-bg` / `--wait-for-completion`（调试时偶尔需要强制等 start hook）。

##### 框架层：`pg_start_bg` / `pg_stop_bg`

`hook-helpers.sh` 提供两个统一 API，**所有 role-start.sh / role-stop.sh 都应使用**：

```bash
# pg_start_bg: 后台启动命令, setsid detach, 安全 env 注入, 写 PID
# 用法: pg_start_bg <log_file> <pid_file> [env_kv ...] -- <cmd ...>
pg_start_bg() {
    # 1. 解析 env_kv 与 cmd (-- 分隔符)
    # 2. mkdir -p log/pid 父目录
    # 3. setsid env -i "${env_args[@]}" PATH="$PATH" "${cmd[@]}" > log 2>&1 &
    #    (env -i 清空继承, 只保留 env_kv + PATH; 无 shell 解析, 无注入)
    #    setsid 不可用时降级 nohup + disown
    # 4. echo $! > pid_file
    # 5. sleep 0.1; kill -0 $! 检测立即 crash → return 1
    # 6. echo PID 给调用方
}
```

```bash
# pg_stop_bg: 优雅关停 PID 文件指向的进程, 取代 lib/common.sh:kill_pid_file
# 用法: pg_stop_bg <pid_file> <name> [<grace_seconds=5>]
pg_stop_bg() {
    # 1. PID 文件不存在 → 静默 skip (幂等)
    # 2. PID 已死 → 清 stale PID, skip
    # 3. SIGTERM → 等 grace → SIGKILL
    # 4. rm -f pid_file
}
```

##### hook 层：start / stop 最小骨架

```bash
# role-backend-start.sh
backend_pid=$(pg_start_bg \
    "$LOG_DIR/backend.log" \
    "$PID_DIR/backend.pid" \
    "WEBVIRT_KEY=xxx" "SPRING_PROFILE=grpc" -- \
    mvn spring-boot:run -pl webvirt-bootstrap)
wait_for_port_with_monitor $BACKEND_PORT "Backend" 60 \
    "$PID_DIR/backend.pid" "$LOG_DIR/backend.log"
pg_exit --status=pass
```

```bash
# role-backend-stop.sh
pg_stop_bg "$PID_DIR/backend.pid" "Backend"
if check_port $BACKEND_PORT; then
    kill_port $BACKEND_PORT "Backend (residual)"
fi
pg_exit --status=pass
```

##### 例外：systemd-managed 服务

`role-agent-start.sh` / `role-agent-stop.sh` 用 `systemctl start/stop`，**不需要** `pg_start_bg`：

- systemd 本身就把服务 detach 到独立 cgroup，**不可能被父 shell 退出杀掉**
- `systemctl start` 是 fire-and-forget 命令，立刻返回；后续 `systemctl is-active` 检查状态
- 这类 hook 应保持现有的 systemctl 调用，**不要**套 `pg_start_bg`

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
git clone git@github.com:pin-gou/pg-skills.git
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
