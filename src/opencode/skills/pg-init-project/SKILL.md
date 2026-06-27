---
name: pg-init-project
description: 在一个新项目里初始化 pg-skills 配置。扫描仓库结构（构建文件、源码组织、多模块布局），生成 `.pg/project.yaml`（modules/environments/tracks/stages/fix_issue，module 的 build/lint/test 命令直接写在 `modules.<m>.<field>` 字段里）和 `.pg/hooks/` 下仅服务 environments 维度的 lifecycle shell 脚本（role start/stop/restart + prepare_env/clean_env）。在 `pg init` 之后、第一次跑 `pg-propose` / `pg-build` 之前使用。
license: MIT
compatibility: 项目根目录需要 `.pg/` 目录（已由 `pg init` 创建）和 `.pg/skills/`（已由 `git subtree add` 同步）。
metadata:
  author: pg-spec
  version: "0.2"
---

# pg-init-project

把一个刚跑完 `pg init` 的项目仓库"填实"成可用的 pg-skills 项目：扫描技术栈和模块布局，生成 `.pg/project.yaml` 和 `.pg/hooks/` 下的 hook 脚本。

`pg init` 只搭骨架（目录结构 + 空白 `project.yaml` + 模板 hook）。本 SKILL 做的是基于实际仓库的"项目级一次成型"。

---

## 何时使用

- **使用**：刚在一个新项目跑完 `pg init`，`.pg/project.yaml` 还是 placeholder 状态。
- **使用**：新加入一个 module（例如新增 `kuboard-server` 子项目），需要把它纳入 `project.yaml`。
- **不使用**：只是想跑 `pg doctor` 校验配置——直接 `pg doctor` 即可。
- **不使用**：跑过本 SKILL 后想调整某个 module 的 build/test 命令——直接编辑 `.pg/project.yaml` 里 `modules.<m>.{build,lint,test.<key>}` 字段即可，不要重跑 SKILL。**这些命令不走 hook**，不需要改 `.pg/hooks/` 下的脚本。
- **不使用**：跑过本 SKILL 后想调整某个 role 的 lifecycle 命令——直接编辑 `.pg/hooks/<role>-<action>.sh` 或 `.pg/project.yaml` 里 `environments.<env>.roles.<r>.actions.<action>.script` 字段即可。
- **不使用**：从一次 change 的视角去修复或扩展——那是 `pg-fix-issue` / `pg-build` 的事。

---

## 核心原则

### 1. 扫描在先，生成在后

绝不猜测项目结构。**先**用 `glob` / `read` 把项目根扫一遍，识别出真实的 tech stack、build tool、multi-module layout，**再**开始写 `project.yaml`。生成的 modules 列表必须 1:1 对应仓库里实际存在的代码单元（Maven 子模块、Go package、pnpm workspace member 等）。

### 2. 占位 / 真实 / 推断 三态分明

生成 `project.yaml` 时每个字段按以下规则处理：

- **真实可推**：构建命令、模块路径、language 枚举——必须从仓库文件推断出来。
- **可推断但需确认**：environments / tracks / stages 的拓扑——本 SKILL 给出一组 **合理的初值**，但必须在最终输出里明确告诉用户"哪些字段是基于常见模式推断的、可能需要调整"。
- **不可推断**：端口、host、roles 之间的拓扑——本 SKILL **不编造**，而是留 `TBD: <说明>` 注释让用户填。

### 2.5 输出语言：优先中文

本 SKILL 生成的所有面向用户的产物（`repo-scan.md`、`project.yaml` 的 `description` 字段、stages / tracks / environments / roles 的说明、最终汇报）**优先使用中文**。规则：

- **必须中文**：`description` 字段、`repo-scan.md` 的章节标题与正文、TBD 标注的解释文字、终态汇报。
- **必须保留英文/原文**：模块 id、role 名、instance 名（schema 用 `^[a-z][a-z0-9-]*$` 约束）、`language` 枚举值（`java` / `typescript` 等）、shell 命令、构建/测试命令、YAML 字段 key。这些是机器契约或 schema 约束，**不能翻译**。
- **保留原文作为引用**：当 description 中引用代码里的标识符、文件名、命令时，原文照抄（如 `mvn -pl kuboard-server -am test`），不要翻译。
- **不引入**额外的语言切换机制（如 `lang: zh` 字段）。中文是默认；用户如需英文，直接编辑生成的文件即可。

### 3. hooks 走模板，不发明

**Hook 协议边界（schema/runtime SSOT）**：hook 只服务于 **environments 维度**，不服务于 modules 维度。具体：

- **走 hook 协议**（生成 `.pg/hooks/<name>.sh`）：`environments.<env>.{prepare_env, clean_env}`、`environments.<env>.roles.<r>.{start, stop, restart, logs, tail, ...}`。runner 通过 `pg-run-hook.py` 调用，注入 `PG_*` env vars（v4: `PG_RUN_CALLER` / `PG_RUN_SESSION` / `PG_ROLE` / `PG_INSTANCE_NAME` / `PG_INSTANCE_HOST` / `PG_HOOK_TYPE` / `PG_LOG_FILE` / `PG_HOOK_LOG_DIR` / `PG_RESULT_FILE` ...；1 版本 alias: `PG_SKILL_NAME` / `PG_CHANGE_NAME`）。
- **不走 hook 协议**（直接写在 `project.yaml` 里）：`modules.<m>.{build, lint, test.<key>}` 字段。这些字段是 `executable_command` 形态（`string` 或 `{cmd, timeout_seconds}`），runner 渲染为 `timeout N bash -c '<cmd>'` 直接执行，**不**经过 `.pg/hooks/<m>-<action>.sh`。原因：单测/单条命令经常需要 ad-hoc 跑（`mvn -Dtest=FooTest`、`pnpm test:e2e --grep "..."`），把每条命令固化成 hook 反而牺牲 agent 灵活性。

因此 `pg-init-project` 的 Phase 3 只为 **environments 节点**生成 hook 脚本。`examples/<language>/hooks/module-<action>.sh` 是 **历史示例模板**，**不再复制到项目里**（项目模块命令直接写在 `project.yaml` 里）。

如果仓库里残留了 `<module>-{build,test,lint}.sh` 之类的旧 hook，提示用户删除（`rm .pg/hooks/<m>-*.sh`）；`pg doctor` 不会把它们当 schema 错误，但它们是死代码。

**SSOT 公共库**：除模板外，pg-init-project 还要把 `.pg/skills/examples/shell/hooks/lib/common.sh` 复制到项目的 `.pg/hooks/lib/common.sh`。该文件是 hook 协议 SSOT，包含 `pg_resolve_paths`：

- **优先**：直接信任 `PG_HOOK_LOG_DIR`（由 `pg-invoke-hook.py` 在 spec 阶段预拼的绝对路径）
- **Fallback**：按 `PG_RUN_CALLER` + `PG_RUN_SESSION` + `PG_ENV` 自拼（v4 caller × session 双维度路由；兼容老式手工调用 / 未走 `pg-invoke-hook.py`，老 hook 仍可读 `PG_SKILL_NAME` / `PG_CHANGE_NAME` 作 1 版本 alias）
  - `pg-build` → `.pg/changes/<C>/2-build/<env>/logs|pids`
  - `pg-regression` → `.pg/regression/<suite>/<env>/logs|pids`（从 `regression-<suite>` 截 suite）
  - `pg-fix-issue` → `.pg/fix-issue/<change>/<env>/logs|pids`
  - 兜底 → `scripts/logs|pids`

无此文件时，模板 fallback 到 caller 控制的 `$PG_LOG_FILE`（所有 skill 共用一条日志，pg-regression / pg-fix-issue 不再走隔离目录）。

`pg_resolve_paths` 的 fallback 路由表必须与 `.pg/skills/src/runtime/bin/pg-invoke-hook.py:pg_log_dir_for_skill` 三处保持同步（SSOT）。改动前先核对两侧。

**environments 维度的 hook 生成规则**：

- 命名约定：`<role-name>-<action>.sh`（例：`backend-start.sh`、`backend-stop.sh`、`frontend-start.sh`）。environment 级 hook 用 `prepare_env.sh` / `clean_env.sh`。
- 模板来源：`.pg/skills/examples/shell/hooks/role-{start,stop,logs,restart}.sh` 与 `env-{prepare,clean}.sh`。pg-init-project 把模板复制到 `.pg/hooks/<role>-<action>.sh` 后，替换其中的 `CMD_PLACEHOLDER` 为本 role 真实的 start/stop 命令。
- 模板里**只**改 `CMD_PLACEHOLDER` 命令块，**不**改 trap / `pg_fail` / `pg_exit` 调用——hook 协议是 SSOT。

**注意**：`modules.<m>.build` / `modules.<m>.test.<key>` 等字段出现在 `project.yaml` 里时，必须是 `executable_command` 形态（string 或 `{cmd, timeout_seconds}`），runner 用 `pg-parse-config.py --resolve-module-build <m>` 等子命令解析。不要在 `project.yaml` 里写 `"build": "bash .pg/hooks/kuboard-server-build.sh"` 这种"调用 hook 来跑 build"的形式——那是错误的，会双重 timeout。

### 4. 跑 `pg doctor` 收尾

写完文件后**必须**跑 `pg doctor` 验证。失败则告知用户缺什么、需不需要回头调整。**不**自行"修" doctor 报错的字段（修法属于项目决策，不属于 SKILL）。

---

## 工作流

按顺序执行 Phase 1 → 4。**不要跳过 Phase 1**——它是后面所有推断的事实基础。

### Phase 1: 扫描仓库

**目标**：输出一份 `<project_root>/.pg/context/repo-scan.md`，列出 tech stack、模块清单、构建/测试入口。

步骤：

1. 用 `glob` 列出项目根一级目录（排除 `.git` / `node_modules` / `target` / `.pg` / `.idea` / `dist` / `build`）。这一项只扫根级，不递归。

2. 识别主构建文件：
   - `pom.xml` 存在 → Maven（看顶层 `<modules>` 段决定是否多模块）
   - `build.gradle` / `build.gradle.kts` → Gradle
   - `go.mod` → Go（看是否多 module workspace）
   - `package.json` 存在且有 `workspaces` 字段 → pnpm/yarn workspace
   - `pyproject.toml` / `setup.py` / `pyproject` → Python
   - 都没有 → 标记为 "mixed / unknown"，让用户确认

3. 对每个识别到的 multi-module 入口（Maven `<modules>` / Go workspace / pnpm `workspaces` 数组），递归 1 层列出子模块路径。

4. 扫测试入口约定：
   - Java/Maven：默认 `src/test/java/**/*Test.java`
   - Go：默认 `*_test.go`
   - TS/Vue：默认 `*.spec.ts` / `*.test.ts` / `tests/`
   - Python：默认 `test_*.py` / `*_test.py` / `tests/`

5. 把扫到的内容写进 `.pg/context/repo-scan.md`，格式见"输出格式 §1"。**全文使用中文**（除命令、文件名、模块 id 这些机器契约）。

**产出**：`repo-scan.md` 已写盘。

### Phase 2: 生成 `.pg/project.yaml`

**目标**：替换 placeholder 的 `project.yaml`，填实 modules/environments/tracks/stages/fix_issue。

读取 `.pg/skills/src/runtime/spec/project.schema.json`，按 schema 字段填：

- `schema: spec-driven`（固定）
- `modules`：从 `repo-scan.md` 的模块清单生成。每个 module 必须有 `root` 和 `language`（language 用 schema 允许的枚举：`java` / `go` / `typescript` / `python` / `proto` / `shell`）。
  - **多模块 Maven**：`root: <子模块相对路径>`, `language: java`。
  - **单模块**：一个 module，`root: .`, `language: <推断>`。
  - **pnpm workspace**：每个 `packages/<name>/` 算一个 module，`language: typescript`。
  - **Go workspace**：每个 module 目录一个 module。
- `environments`：用 schema 的 environment 形态，但**只填合理的初值**：
  - 默认给一个 `local` environment，含 `dev` role 一个 instance，host `localhost`、port `TBD: <常见端口，e.g. 8080>`。
  - 在 `description` 字段用 `TBD:` 标注所有未确认值。
- `tracks`：用 schema 的 track 形态。每个 module 一个 track，`type: standard`，`max_fix_retries: 5`，`modules: [<module.id>]`。
- `stages`：两个 stage，`dev-isolated`（`environment.required: false`，`test_key: unit`）和 `dev-mock-integration`（`environment.required: true`，`test_key: integration`）。
- `fix_issue`：照 schema 默认值填。

**注意**：
- **绝不**编造端口 / host / role 拓扑——这些只能从 `repo-scan.md` 之外的信息推断（如 README、部署脚本），没有就 `TBD:`。
- 不引入 schema 之外的字段（`additionalProperties: false`）。
- `description` 字段用 `TBD:` 标注需用户复核的项，例：`description: "TBD: 确认端口 8080 还是 80"`。**所有 description 一律使用中文**（除非引用代码标识符或 shell 命令保持原文）。

写盘前用 schema 校验一次（`python3 .pg/skills/src/runtime/bin/pg doctor` 会跑校验；本阶段至少过 yaml 解析）。

**产出**：`project.yaml` 写盘。

### Phase 3: 生成 `.pg/hooks/`

**目标**：仅在 environments 节点实际声明 `actions` / `prepare_env` / `clean_env` 时，为对应的 role / environment 生成 hook 脚本。

**module 维度的命令不进 hook**：见上方 `核心原则 §3` —— `modules.<m>.{build, lint, test.<key>}` 直接以 `executable_command` 形态写在 `project.yaml`，**不**在 `.pg/hooks/` 生成对应文件。

**生成步骤**：

1. 遍历 `environments.<env>.roles`，对每个 role 的 `actions.start` / `actions.stop` / `actions.restart`（含其它 lifecycle action），生成 `.pg/hooks/<role>-<action>.sh`。
2. 遍历 `environments.<env>.prepare_env` / `clean_env`，生成 `.pg/hooks/prepare_env.sh` / `.pg/hooks/clean_env.sh`（如声明）。
2.5. 复制 SSOT 公共库（与模板同源）：
   - 源：`.pg/skills/examples/shell/hooks/lib/common.sh`
   - 目标：`.pg/hooks/lib/common.sh`
   - 作用：模板头部条件 `source lib/common.sh` + `pg_resolve_paths` 才能找到目标；`pg_resolve_paths` 优先信任 `PG_HOOK_LOG_DIR`（由 `pg-invoke-hook.py` 预拼），fallback 时按 `PG_RUN_CALLER + PG_RUN_SESSION + PG_ENV` 自拼（v4 caller × session 双维度路由；兼容老式手工调用）
   - 跳过此步：生成的 hook 仍能工作（走 `$PG_LOG_FILE`），但 pg-regression / pg-fix-issue 日志会回落到 `scripts/logs`，不写到预期的 `.pg/regression/` / `.pg/fix-issue/` 目录
3. 模板来源：从 `.pg/skills/examples/shell/hooks/role-<action>.sh` 复制并替换 `CMD_PLACEHOLDER`；env 级模板从 `env-prepare.sh` / `env-clean.sh` 复制。模板依赖 `pg-run-hook.py` 注入的 `PG_RUN_CALLER` / `PG_RUN_SESSION` / `PG_ROLE` / `PG_INSTANCE_NAME` / `PG_INSTANCE_HOST` / `PG_HOOK_TYPE` / `PG_LOG_FILE` / `PG_HOOK_LOG_DIR` / `PG_RESULT_FILE` 等 env vars（v4 协议），**不**依赖 `PG_MODULE_ROOT`（module 维度不进 hook 协议）。
4. chmod 755。
5. **不**改 trap / `pg_fail` / `pg_exit` 调用——hook 协议是 SSOT。

**产出**：`.pg/hooks/<role>-<action>.sh` 与 `.pg/hooks/{prepare_env,clean_env}.sh`（如适用）全部写盘且可执行。如果 environments 没有任何 actions（只声明静态 roles），**不**生成任何 hook，目录保持空。

### Phase 4: 跑 `pg doctor` 校验

**目标**：让用户看到一份 "OK (4 checks passed), 0 warning" 的输出。

```bash
python3 .pg/skills/src/runtime/bin/pg doctor
```

如果 doctor 报 schema 错：检查 `project.yaml` 的 `TBD:` 字段是否破坏了 schema 约束（不应该，`TBD:` 只在 description 字段里，但 lint 一遍）。

如果 doctor 报 `.pg/hooks/<x>.sh not executable`：`chmod +x`。

如果 doctor 报 `.pg-version not found`：用户没跑 `pg init`，退出并提示先跑 `pg init`。

**产出**：doctor 输出 0 / 0 / 4。

---

## 输出格式

### §1: `repo-scan.md`

模板（**优先中文**；标题、字段名、命令一律保持英文以便核对；说明文字全部中文）：

```markdown
# <项目名> 仓库扫描报告

Generated: <ISO 时间戳>
Scanner: pg-init-project v0.1

## 技术栈

- 主构建工具: <pom.xml / go.mod / package.json / pyproject.toml>
- 语言: <java / go / typescript / python / mixed>
- 多模块: <是 / 否>

## 模块清单

| Module id | 根目录（相对） | 语言 | 构建命令 | 测试命令 | 备注 |
|---|---|---|---|---|---|
| backend | kuboard-server/ | java | mvn -pl kuboard-server -am package -DskipTests | mvn -pl kuboard-server test | |
| frontend | kb-portal/ | typescript | pnpm --filter kb-portal build | pnpm --filter kb-portal test:unit | |

## 构建/测试入口命令

### 后端（Maven 父 POM 在项目根）

```bash
# 构建所有模块
mvn -DskipTests package -q

# 构建单个模块 + 传递依赖
mvn -pl <module> -am package -DskipTests -q

# 单个模块的单元测试
mvn -pl <module> test -q
```

### 前端（kb-portal）

```bash
cd kb-portal
pnpm install
pnpm dev
pnpm type:check
pnpm test:e2e
```

## 服务端口（项目当前约定）

| 服务 | 端口 | 来源 |
|------|------|------|
| kuboard-server (HTTP) | 9080 / 9090 | application.yaml / run-kuboard-server.sh |
| kb-portal (vite dev) | 8848 | playwright.config.ts |

## TBD 字段（需人工复核）

- `environments.local.roles.dev.instances[0].port`: 8080 — 按 Spring Boot 默认推断，请到 application.yml 确认
- `environments.local.roles.dev.instances[0].host`: localhost — 本地开发默认；staging / prod 需用户补充
```

### §2: 终态汇报（写完所有文件后给 LLM 主循环的回报）

```
✓ pg-init-project 完成

已生成:
  - .pg/context/repo-scan.md
  - .pg/project.yaml（X 个模块，Y 个环境，Z 个 track，W 个 stage；
                     module 命令直接写在 modules.<m>.{build,lint,test.<key>} 字段里）
  - .pg/hooks/<role>-<action>.sh × M（仅 environments 维度的 lifecycle actions）
  - .pg/hooks/{prepare_env,clean_env}.sh（如声明）

Doctor: OK (4 checks passed)，0 warning

需人工复核的项 (TBD):
  - environments.local.roles.dev.instances[0].port: 8080（请到 application.yml 确认）
  - <其他 TBD 项，详见 repo-scan.md>

Next steps:
  1. 在 .pg/project.yaml 与 .pg/context/repo-scan.md 中复核所有 TBD 项
  2. 补全 environments 缺失的 port / host
  3. 运行 pg-propose 启动第一个 change
```

---

## 失败模式

这些是容易犯但代价高的错：

1. **不扫仓库直接编 modules** —— 凭空生成 modules 列表，跳过实际代码。**反例**：看到 `pom.xml` 假设"单模块 Java"，但实际是 4 个 Maven 子模块。
2. **编造端口/host** —— 把 8080 写死成 backend port，不验证。**反例**：8080 在项目里是 kuboard-server，但用户的 Spring Boot 实际跑 80。
3. **改 hook 协议** —— 在生成的 hook 里改 `pg_fail` / `pg_exit` 的参数或 trap 行为。**反例**：把 `set -euo pipefail` 改成 `set -e` 怕报错。这破坏 SSOT。
4. **跳过 `pg doctor`** —— 写完文件直接返回成功。**反例**：用户跑 `pg-propose` 时报 schema 错，回头找问题浪费半小时。
5. **把 placeholder 留着** —— 在 `project.yaml` 顶部保留 `placeholder` module 不删。**反例**：schema 允许 `minProperties: 1` 但实际项目有 4 个 module，placeholder 残留污染 tracks/stages。
6. **混淆 module hook 与 environment hook 的边界** —— 把 `modules.<m>.build` 写成 `bash .pg/hooks/kuboard-server-build.sh`，期望它走 hook 协议。**错**：`modules.<m>.build` 是 `executable_command` 字段，runner 直接渲染为 `timeout N bash -c '<cmd>'` 执行，**不**调用 `.pg/hooks/<m>-<action>.sh`。`pg-run-hook.py` 只服务于 `environments.<env>.{prepare_env,clean_env}` 与 `environments.<env>.roles.<r>.{start,stop,...}`。项目里如果残留 `<module>-{build,test,lint}.sh`，是历史模板的产物，删除即可。
7. **忘记复制 `lib/common.sh`** —— 只复制 5 个 role/env 模板但漏掉 `lib/common.sh`。**反例**：新项目跑 `pg-regression` 时日志写到 `scripts/logs` 而非 `.pg/regression/<suite>/<env>/logs`，排错时找不到日志。`pg doctor` 会有 `hooks_lib_common_present` warning 提示。

---

## 行为规约（必须遵守）

- **MUST**：扫完仓库**才**开始写 `project.yaml`。不允许"看名字猜结构"。
- **MUST**：每个 module 的 `root` 路径相对项目根，且与仓库里实际存在的路径一一对应。
- **MUST**：所有 `TBD:` 项集中在 `description` 字段，**不**污染 `root` / `language` / `cmd` 等结构化字段。
- **MUST**：跑 `pg doctor` 且输出 0 错误才视为完成。
- **MUST**：复制 `.pg/skills/examples/shell/hooks/lib/common.sh` 到 `.pg/hooks/lib/common.sh`，让生成的 role-* / env-* hook 能调 `pg_resolve_paths` 做 per-skill 路径路由。
- **MUST NOT**：引入 `additionalProperties: false` 之外的 schema 字段。
- **MUST NOT**：从 `examples/<lang>/hooks/module-*.sh` 之外的地方抄 module hook——module 命令应在 `project.yaml` 里以 `executable_command` 形态声明，**不进 hook 协议**。
- **MUST NOT**：把 `modules.<m>.build` 写成 `bash .pg/hooks/<m>-build.sh`——那是双重封装 + 双重 timeout，runner 不识别。
- **MUST NOT**：动 `pg-version` / `pg` CLI / `hook-helpers.sh` / `error-categories.yaml`。
- **SHOULD**：每个 module 至少生成 `build` 和 `test` 两个 hook；`lint` 仅在 language 习惯上有独立命令时（go: `go vet`）才生成。
- **SHOULD**：在最终汇报里把所有 TBD 项用清单列出来，让用户一次性 review 完。
