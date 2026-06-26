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
├── VERSION                       # semver （当前: 0.3.0）
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

#### Hook 边界

| 维度 | 走 hook 协议？ | 配置位置 | 调度方式 |
|------|---------------|---------|---------|
| **environments**（prepare_env / clean_env / role start/stop/logs） | ✅ 走 `.pg/hooks/<name>.sh` | project.yaml `environments.<env>.{prepare_env,clean_env}` 和 `.roles.<r>.actions` | `pg-run-hook.py`，注入 `PG_*` env vars |
| **modules**（build / lint / test） | ❌ 不走 hook | project.yaml `modules.<m>.{build, lint, test.<key>}` | 直接 `timeout N bash -c '<cmd>'` 执行 |

> `examples/<lang>/hooks/module-*.sh` 是历史遗留示例，当前 runner 已不再调用它们。如果项目里残留了 `<module>-{build,test,lint}.sh`，是历史遗留，删除即可（`rm .pg/hooks/<m>-*.sh`）。

#### Hook 脚本模板

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

#### 注入的环境变量

| 变量 | 说明 | 适用范围 |
|------|------|---------|
| `PG_SKILLS_PATH` | pg-skills 仓库根路径 | 全部 |
| `PG_PROJECT_ROOT` | 项目根路径 | 全部 |
| `PG_RESULT_FILE` | 写 result.json 的路径 | 全部 |
| `PG_LOG_FILE` | 写 stdout/stderr 的路径 | 全部 |
| `PG_CHANGE_NAME` | 当前 change 名 | 全部 |
| `PG_STAGE` | 当前 stage 名 | 全部 |
| `PG_ENV` | 当前环境（dev-local / dev-3tier） | 全部 |
| `PG_ROLE` | role 名 | role action 时 |
| `PG_INSTANCE_NAME` | instance 名 | role action 时 |
| `PG_INSTANCE_HOST` | instance host | role action 时 |
| `PG_SKILL_NAME` | 调用方 skill 名（pg-build/pg-regression/pg-fix-issue） | 全部 |

> ~~`PG_MODULE` / `PG_MODULE_ROOT`~~ 已不再注入——module 维度不经过 hook 协议。

#### pg-invoke-hook.py CLI

LLM **不**直接调 hook 脚本，也不解析 spec；通过 `pg-invoke-hook.py` 统一入口调度。

##### invoke-hook subcommand

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --change <C> --env <ENV> --role <ROLE> \
  --instance <INSTANCE> --action <ACTION> \
  [--stage <STAGE>] [--tail-lines <N>]
```

| 标志 | 必填 | 说明 |
|------|------|------|
| `--change` | ✅ | 当前 change 名（用于 spec.change + log_path 路由） |
| `--env` | ✅ | 必须在 project.yaml `environments` 列表中 |
| `--role` | ✅¹ | backend / frontend / agent。`start/stop/logs/tail` 必填；`prepare_env/clean_env` 忽略 |
| `--instance` | ✅¹ | 必须在 `environments.<env>.roles.<role>.instances[]` 中 |
| `--action` | ✅ | per-role: `start / stop / logs / tail`；env-level: `prepare_env / clean_env` |
| `--stage` | ❌ | 默认 `manual`；用于 spec.stage 标记 |
| `--tail-lines` | ❌ | 仅 `--action logs\|tail` 生效 |

¹ `--role` / `--instance` 对 env-level actions 是 no-op（CLI parser 不强制、runtime 也忽略）。

##### --tail-lines 语义

- 不传：runner 把 project.yaml `actions.<action>.args` 渲染后原样传给 hook（占位符 `{lines:100}` / `{role}` / `{instance.name}` / `{instance.host}` 由 runner 解析）。
- 传了：runner 把 `--tail-lines <N>` 作为 hook args 数组最后 2 个元素追加，**不**修改 project.yaml。hook 脚本从 `$@` 读取。

##### 超时与连接信息

- `--timeout` / `--host` / `--port` **不是 CLI flag**：
  - `timeout_seconds` 由 runner 从 project.yaml 的 `actions.<action>.timeout_seconds` 反查并写入 spec；`pg-run-hook.py` 通过 `subprocess.run(timeout=...)` 强制执行。
  - host / port 由 runner 从 `instances[]` 自动反查。

##### status subcommand（prepare_env 状态查询）

与 `invoke-hook` 平级：

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py status \
  --change <C> [--stage <S>]
```

| 标志 | 必填 | 说明 |
|------|------|------|
| `--change` | ✅ | 当前 change 名 |
| `--stage` | ❌ | 可选 stage 名过滤 |

典型用法：在 verify agent 中查询 prepare_env 是否已成功执行，避免硬编码 log_path。

> 历史兼容：`pg-pipeline-runner.py prepare-env-status <C> [stage]` 仍可用，`pg-invoke-hook.py status` 是统一 runtime 入口。

#### 历史旧路径

`pg-pipeline-runner.py invoke-hook` 仍然可用（thin wrapper 转发到 `pg-invoke-hook.py`），但新代码统一走新路径。

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
