# pg-skills

> L1 (Level 1) capability layer for AI-driven development workflows.
> Extracted from `oc3-web-virt` project. Cross-project, language-agnostic.

## What is this?

`pg-skills` is the **shared capability layer** that powers pg-* slash commands, agents, and pipeline runners. It is designed to be:

- **Language-agnostic** — works with Java/Go/TypeScript/Python/mixed stacks
- **Project-independent** — contains no project-specific knowledge (no `webvirt-*`, no `pangee`)
- **Embeddable** — projects consume it via `git subtree` into `.pg/skills/`

## Repository structure

```
pg-skills/
├── VERSION                       # semver
├── CHANGELOG.md
├── README.md                     # this file
├── src/
│   ├── opencode/
│   │   ├── commands/             # 8 slash commands (/1-pg-define, /2-pg-propose, ...)
│   │   ├── skills/               # 13 SKILL.md files (pg-propose, pg-build, ...)
│   │   └── agents/               # sub-agents (explore, pg-manager, ...)
│   └── runtime/                  # runtime layer (Phase 2: hooks, pg CLI, error protocol)
│       ├── bin/                  # CLI entry points
│       ├── lib/                  # Python helpers (hook_runner.py, etc.)
│       └── spec/                 # SSOT specifications
├── examples/
│   └── shell/                    # default hook templates for environments 维度
│       └── hooks/                # role-{start,stop,logs}.sh + env-{prepare,clean}.sh
└── tests/                        # tests for runtime layer
```

> 注意：`examples/` 只放 **environments 维度**的 hook 模板（role / env lifecycle）。module 维度的 build / lint / test 命令**不进 hook 协议**，直接以 `executable_command` 形态写在 `.pg/project.yaml` 的 `modules.<m>.{build, lint, test.<key>}` 字段里。历史遗留的 `examples/<lang>/hooks/module-*.sh` 已删除。

## Versioning

- **0.1.x** — skeleton + de-webvirtification (current)
- **0.2.x** — hook protocol + error propagation + pg CLI MVP
- **0.3.x** — full project init flow (`pg init` + `pg doctor`)
- **1.0.x** — production-ready, dogfooded on 2+ external projects

## Quick Start (新项目接入)

**0.1.x 当前状态**: `pg sync` 仅 `--check` 可用; 完整 sync (`pg sync` / `pg sync --interactive`) 在 0.2.x。

### 4 步接入流程

> **前提**: `.pg/skills/` 已从 pg-skills 仓库同步进项目（git subtree / 手动复制 / 其它方式），`pg init` **不会**自动同步它。
> 如果你还没同步，先跑 `git subtree add --prefix=.pg/skills pg-skills master --squash`。

```bash
# 1. 用 git subtree 把 pg-skills 同步进项目
git remote add pg-skills git@gitee.com:shao_hq/pg-skills.git
git fetch pg-skills
git subtree add --prefix=.pg/skills pg-skills master --squash
#   - .pg/skills/ 下含 VERSION / CHANGELOG.md / src/ / examples/ / tests/
#   - 这是 SSOT, 后续升级都走 subtree pull

# 2. 跑 pg init 创建 .pg/ 骨架 + .opencode/ symlink
python3 .pg/skills/src/runtime/bin/pg init
#   - 生成 .pg/{hooks,context,scripts,changes,runs}/ 目录
#   - 写 .pg-version
#   - 首次跑时生成 .pg/project.yaml (placeholder 状态); 已存在则不动
#   - 在 .opencode/agents/ / .opencode/commands/ / .opencode/skills/ 下
#     为 pg-skills 的每项创建逐项 symlink (scripts/ 不创建, 已存在 symlink 跳过)
#   - 设计意图: .opencode/ 下可同时放 pg-skills (symlink) + 项目专属 (real file) 资源
#   - 整个命令**幂等**, 可重复跑

# 3. 重启 opencode, 让 .opencode/ symlink 生效
#    启动后, opencode 就能看到 pg-* slash commands + pg-* skills + pg-* sub-agents

# 4. 在 opencode 中输入：加载 pg-init-project skill
#    (扫描仓库结构, 生成 .pg/context/repo-scan.md + 实打实的 .pg/project.yaml;
#     module 的 build/lint/test 命令直接写在 .pg/project.yaml 的 modules.<m>.{build,lint,test.<key>}
#     字段里, 走 `executable_command` 形态; 仅 environments 节点下声明的 lifecycle actions
#     (role start/stop/restart + prepare_env/clean_env) 才在 .pg/hooks/ 下生成 shell 脚本.
#     跑 pg doctor 验证.)
```

接入后预期 `.opencode/` 下的 symlink 布局:

```
.opencode/
├── agents/   <-- symlinks: explore.md, pg-manager.md, pg-build/, pg-fix-issue/, pg-quick-build/, pg-regression/
├── commands/ <-- symlinks: pg-1-define.md, pg-2-propose.md, pg-2.1-propose-refine.md, pg-2b-quick-build.md,
│                        pg-3-build.md, pg-4-regression.md, pg-5-fix-issue.md, pg-6-archive.md
├── skills/   <-- symlinks: git-workflow-and-versioning/, pg-archive/, pg-build/, pg-fix-issue/,
│                        pg-propose/, pg-propose-refine/, pg-quick-build/, pg-regression/,
│                        pg-systematic-diagnosing/, pg-verify-and-merge/, security-and-hardening/,
│                        using-agent-skills/, pg-browser-testing-with-devtools/
└── (无 scripts/, pg-skills 的 scripts/ 不通过 symlink 暴露, 项目直接调用即可)
```

### 进阶

- **自定义 hook（仅 environments 维度）**：如果默认模板不满足，把 `.pg/hooks/<role>-<action>.sh` 或 `.pg/hooks/{prepare_env,clean_env}.sh` 复制一份修改。`pg-run-hook.py` 优先看项目里的 `.pg/hooks/`，找不到对应脚本时回退到 `project.yaml` 里 `environments.<env>.roles.<r>.actions.<action>.script` 字段直接执行。**module 维度的命令不进 hook**——直接编辑 `.pg/project.yaml` 的 `modules.<m>.{build,lint,test.<key>}` 字段。
- **添加项目专属 agent / command / skill**：直接在 `.opencode/<dir>/` 下**真实**放文件，和 symlink 项并存。`pg init` 不会触碰真实文件。
- **再次同步 pg-skills**：`pg sync`（0.2.x 之后，等价于 `git subtree pull --prefix=.pg/skills pg-skills master --squash`）。
- **跳过 symlink 创建**：`pg init --no-symlinks`（已有 `.opencode/` 的项目）。

### 一次性命令一览

```bash
git remote add pg-skills git@gitee.com:shao_hq/pg-skills.git
git fetch pg-skills
git subtree add --prefix=.pg/skills pg-skills master --squash
python3 .pg/skills/src/runtime/bin/pg init
git add .pg/ .pg-version
git commit -m "feat: 接入 pg-skills $(cat .pg/skills/VERSION)"
```

### Hook 协议

**Hook 协议边界**：

- **走 hook 协议**（`.pg/hooks/<name>.sh`）：environments 维度的 lifecycle actions——`environments.<env>.{prepare_env,clean_env}` 与 `environments.<env>.roles.<r>.{start,stop,restart,logs,tail,...}`。runner 通过 `pg-run-hook.py` 调度，注入 `PG_*` env vars（见下），并写 `result.json`。
- **不走 hook 协议**（直接写在 `.pg/project.yaml`）：module 维度的命令——`modules.<m>.{build, lint, test.<key>}`。runner 把这些字段（`executable_command` 形态：`string` 或 `{cmd, timeout_seconds}`）渲染为 `timeout N bash -c '<cmd>'` 直接执行。**不**经过 `.pg/hooks/<m>-<action>.sh`。

注意：`examples/<lang>/hooks/module-*.sh` 是历史示例模板（早期把 module 命令也固化成 hook），当前 runner 已不再调用它们——它们保留在仓库里仅供阅读参考，**不要**复制到 `.pg/hooks/` 下。如果项目里残留了 `<module>-{build,test,lint}.sh`，是历史遗留，删除即可（`rm .pg/hooks/<m>-*.sh`），不影响 `pg doctor`。

错误地把 module 命令塞进 hook 会出现双重 timeout + 双重日志路径，runner 不会按 hook 协议调度它。

每个 hook 是 shell 脚本, 加载 `hook-helpers.sh` 后用 `pg-fail` / `pg-exit` 报告结果:

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

**env 变量** (`pg-run-hook.py` 注入):
- `PG_SKILLS_PATH` — pg-skills 仓库根
- `PG_PROJECT_ROOT` — 项目根
- `PG_RESULT_FILE` — 写 result.json 的路径
- `PG_LOG_FILE` — 写 stdout/stderr 的路径
- `PG_CHANGE_NAME` — 当前 change 名 (来自 input.change)
- `PG_STAGE` — 当前 stage 名 (来自 input.stage)
- `PG_ENV` — 当前环境 (dev-local / dev-3tier)
- `PG_ROLE` — role 名（role action 时）
- `PG_INSTANCE_NAME` — instance 名（role action 时）
- `PG_INSTANCE_HOST` — instance host（role action 时）

`PG_MODULE` / `PG_MODULE_ROOT` 已不再注入——module 维度走 `modules.<m>.<field>` 直接执行 shell 命令字符串，不经过 hook 协议。

**LLM ↔ Runner 通信约定**（environments 维度 LLM 触发 hook 的唯一入口）：

LLM **不**直接调 hook 脚本，也不解析 spec；它调用 `pg-pipeline-runner.py invoke-hook` CLI，runner 内部反查 project.yaml、拼 spec、调 pg-run-hook.py。

```bash
python3 .pg/skills/src/opencode/skills/pg-build/scripts/pg-pipeline-runner.py invoke-hook \
  --change <C> --env <ENV> --role <ROLE> \
  --instance <INSTANCE> --action <ACTION> \
  [--stage <STAGE>] [--tail-lines <N>]
```

| 标志 | 必填 | 说明 |
|------|------|------|
| `--change` | ✅ | 当前 change 名（用于 spec.change + log_path 路由） |
| `--env` | ✅ | 必须在 project.yaml `environments` 列表中 |
| `--role` | ✅ | backend / frontend / agent |
| `--instance` | ✅ | 必须在 `environments.<env>.roles.<role>.instances[]` 中 |
| `--action` | ✅ | 仅 start / stop / logs / tail |
| `--stage` | ❌ | 默认 `manual`；用于 spec.stage 标记 |
| `--tail-lines` | ❌ | 仅 `--action logs\|tail` 生效；runner 把它作为 hook args 末尾追加 |

**`--timeout` / `--host` / `--port` 不是 CLI flag**：

- `timeout_seconds` 是 **INFORMATION**，由 runner 从 project.yaml 的 `actions.<action>.timeout_seconds` 反查并写入 spec；pg-run-hook.py 通过 `subprocess.run(timeout=...)` 强制执行。LLM 调用时不传，调用后 LLM 也不应自己计算超时（用 prompt 里 `environment.hooks.action_metadata[role][action].timeout_seconds` 字段规划任务时长即可）。
- host / port 由 runner 从 `instances[]` 自动反查。

**`--tail-lines` 选项 Y 语义**：

- 不传：runner 把 project.yaml `actions.<action>.args` 渲染后原样塞给 hook（占位符 `{lines:100}` / `{role}` / `{instance.name}` / `{instance.host}` 都由 runner 解析）。
- 传了：runner 把 `--tail-lines <N>` 作为 hook args 数组的最后 2 个元素追加，**不**修改 project.yaml 配置。

hook 脚本作者须知：如果 hook 脚本需要读取日志行数，从 `$@` 取 `--tail-lines N`（runner 已经追加到 args 末尾）。

**错误 category 枚举** (`src/runtime/spec/error-categories.yaml`):

| category | severity | retry | 用途 |
|---|---|---|---|
| `prereq_missing` | blocked | none | 缺前置命令/库 |
| `port_in_use` | recoverable | after_fix | 端口占用 |
| `timeout` | recoverable | exponential | 操作超时 |
| `health_check_fail` | recoverable | wait | 健康检查失败 |
| `dependency_not_ready` | recoverable | wait | 依赖未就绪 (DB/cache) |
| `network` | recoverable | exponential | SSH/HTTP/DNS 失败 |
| `permission_denied` | blocked | none | 权限不足 |
| `config_invalid` | blocked | none | 配置错误 |
| `resource_exhausted` | blocked | none | 磁盘/内存 |
| `test_failure` | recoverable | none | 测试断言失败 |
| `build_failure` | recoverable | none | 编译失败 |
| `db_migration_fail` | blocked | none | 数据库迁移失败 |
| `invariant_violation` | blocked | none | 跨模块不变量违反 |
| `unknown` | recoverable | none | 未分类 (兜底) |

## Consuming pg-skills via git subtree

(0.2.x 之后推荐路径, 0.1.x 暂手动 cp)

```bash
# 一次性
git remote add pg-skills git@gitee.com:shao_hq/pg-skills.git
git fetch pg-skills
git subtree add --prefix=.pg/skills pg-skills master --squash

# 升级
pg sync           # = git subtree pull --prefix=.pg/skills pg-skills master --squash
pg doctor         # 校验

# 交互式升级 (冲突多时)
pg sync --interactive
```

注意: 0.1.x 阶段, hook 脚本里的 `source $PG_SKILLS_PATH/...` **手动写死绝对路径**。0.2.x 之后 subtree 嵌入, 自动用相对路径 `.pg/skills/...`。

## Development

```bash
# Clone
git clone git@gitee.com:shao_hq/pg-skills.git
cd pg-skills

# Run runtime tests (Phase 2+)
pytest tests/

# Validate all commands/skills parse as expected
python3 src/runtime/bin/pg validate
```

## Contributing

Until 1.0, **all changes go through a single linear branch**. After 1.0, this repo will adopt the standard branch + PR workflow.

## License

Internal project — see project root for license terms.
