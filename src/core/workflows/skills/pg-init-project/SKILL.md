---
name: pg-init-project
description: 在一个新项目里初始化 pg-skills 配置。扫描仓库结构（构建文件、源码组织、多模块布局），生成 `.pg/project.yaml`（modules/environments，module 的 build/lint/test 命令直接写在 `modules.<m>.<field>` 字段里）、`.pg/hooks/` 下仅服务 environments 维度的 lifecycle shell 脚本（role start/stop/health_check + prepare_env/clean_env），并把 `.pg/context/agent-protocol.md` 注入项目。在 `pg init` 之后、第一次跑 `pg-auto-pilot` 之前使用。仅当用户显式要求初始化/补全 pg-skills 项目配置时加载；与构建无关的日常任务禁止自行加载、禁止主动提示用户使用本 SKILL。
license: MIT
compatibility: 项目根目录需要 `.pg/` 目录（已由 `pg init` 创建）和 `.pg/skills/`（已由 `git subtree add` 同步）。
metadata:
  author: pg-spec
  version: "1.0"
---

# pg-init-project

把一个刚跑完 `pg init` 的项目仓库"填实"成可用的 pg-skills 项目：扫描技术栈和模块布局，生成 `.pg/project.yaml` 和 `.pg/hooks/` 下的 hook 脚本。

`pg init` 只搭骨架（目录结构 + placeholder `project.yaml` + 工具适配器安装）。本 SKILL 做的是基于实际仓库的"项目级一次成型"，并把 agent 协议（`.pg/context/agent-protocol.md`）注入项目——`pg doctor` 的 `context_protocol_present` 检查依赖这一步。

---

## 何时使用

- **使用**：刚在一个新项目跑完 `pg init`，`.pg/project.yaml` 还是 placeholder 状态。
- **使用**：新加入一个 module（例如新增 `kuboard-server` 子项目），需要把它纳入 `project.yaml`。
- **不使用**：只是想跑 `pg doctor` 校验配置——直接 `pg doctor` 即可。
- **不使用**：跑过本 SKILL 后想调整某个 module 的 build/test 命令——直接编辑 `.pg/project.yaml` 里 `modules.<m>.{build,lint,test.<key>}` 字段即可，不要重跑 SKILL。**这些命令不走 hook**，不需要改 `.pg/hooks/` 下的脚本。
- **不使用**：跑过本 SKILL 后想调整某个 role 的 lifecycle 命令——直接编辑 `.pg/hooks/<role>-<action>.sh` 或 `.pg/project.yaml` 里 `environments.<env>.roles.<r>.actions.<action>.script` 字段即可。
- **不使用**：从一次 change 的视角去修复或扩展——那是 `pg-auto-pilot` 的事。

---

## 核心原则

### 1. 扫描在先，生成在后

绝不猜测项目结构。**先**用 `glob` / `read` 把项目根扫一遍，识别出真实的 tech stack、build tool、multi-module layout，**再**开始写 `project.yaml`。生成的 modules 列表必须 1:1 对应仓库里实际存在的代码单元（Maven 子模块、Go package、pnpm workspace member 等）。

### 2. 占位 / 真实 / 推断 三态分明

生成 `project.yaml` 时每个字段按以下规则处理：

- **真实可推**：构建命令、模块路径、language 枚举——必须从仓库文件推断出来。
- **可推断但需确认**：environments 的拓扑——本 SKILL 给出一组 **合理的初值**，但必须在最终输出里明确告诉用户"哪些字段是基于常见模式推断的、可能需要调整"。
- **不可推断**：端口、host、role 之间的拓扑——本 SKILL **不编造**，而是留 `TBD: <说明>` 注释让用户填。

### 2.5 输出语言：优先中文

本 SKILL 生成的所有面向用户的产物（`repo-scan.md`、`project.yaml` 的 `description` 字段、environments / roles 的说明、最终汇报）**优先使用中文**。规则：

- **必须中文**：`description` 字段、`repo-scan.md` 的章节标题与正文、TBD 标注的解释文字、终态汇报。
- **必须保留英文/原文**：module id、role 名、instance 名（schema 用 `^[a-z][a-z0-9-]*$` 约束）、`language` 枚举值（`java` / `typescript` 等）、shell 命令、构建/测试命令、YAML 字段 key。这些是机器契约或 schema 约束，**不能翻译**。
- **保留原文作为引用**：当 description 中引用代码里的标识符、文件名、命令时，原文照抄（如 `mvn -pl kuboard-server -am test`），不要翻译。
- **不引入**额外的语言切换机制（如 `lang: zh` 字段）。中文是默认；用户如需英文，直接编辑生成的文件即可。

### 3. hooks 走模板，不发明

**Hook 协议边界（schema/runtime SSOT）**：hook 只服务于 **environments 维度**，不服务于 modules 维度。具体：

- **走 hook 协议**（生成 `.pg/hooks/<name>.sh`）：`environments.<env>.{prepare_env, clean_env}`、`environments.<env>.roles.<r>.{start, stop, restart, logs, tail, health_check, ...}`。runner 通过 `pg-invoke-hook.py` 调用，注入 `PG_*` env vars（v7 SSOT 见 `.pg/skills/src/runtime/spec/hook-env-vars.yaml`：硬注入 `PG_PROJECT_ROOT` / `PG_SKILLS_PATH` / `PG_RUN_CALLER` + spec 注入 `PG_RUN_SESSION` / `PG_STAGE` / `PG_ENV` / `PG_ROLE` / `PG_INSTANCE_NAME` / `PG_INSTANCE_HOST` / `PG_INSTANCE_PORT` / `PG_HOOK_TYPE` / `PG_HOOK_LOG_DIR` / `PG_LOG_FILE` / `PG_RESULT_FILE`）。
- **不走 hook 协议**（直接写在 `project.yaml` 里）：`modules.<m>.{build, lint, test.<key>}` 字段。这些字段是 `executable_command` 形态（`string` 或 `{cmd, timeout_seconds}`），由 `pg-parse-config.py --resolve-module-build <m>` 等子命令解析，runner 渲染为 `timeout N bash -c '<cmd>'` 直接执行，**不**经过 `.pg/hooks/<m>-<action>.sh`。原因：单测/单条命令经常需要 ad-hoc 跑（`mvn -Dtest=FooTest`、`pnpm test:e2e --grep "..."`），把每条命令固化成 hook 反而牺牲 agent 灵活性。

因此本 SKILL 的 Phase 3 只为 **environments 节点**生成 hook 脚本。如果仓库里残留了 `<module>-{build,test,lint}.sh` 之类的旧 hook，提示用户删除（`rm .pg/hooks/<m>-*.sh`）；`pg doctor` 不会把它们当 schema 错误，但它们是死代码。

**SSOT 公共库**：除模板外，本 SKILL 还要把 `.pg/skills/examples/shell/hooks/lib/common.sh` 复制到项目的 `.pg/hooks/lib/common.sh`。该文件是 hook 协议 SSOT，包含 `pg_resolve_paths`：

- **优先**：直接信任 `PG_HOOK_LOG_DIR`（由 `pg-invoke-hook.py` 在 spec 阶段预拼的绝对路径）
- **Fallback**：按 `PG_RUN_CALLER` + `PG_RUN_SESSION` + `PG_ENV` 自拼（caller × session 双维度路由；老式手工调用 / 未走 `pg-invoke-hook.py` 仍可走此路径）
  - `pg-agent` → `.pg/agent/<session>/<env>-logs`
  - `ad-hoc` → `.pg/ad-hoc/<session>/<env>-logs`
  - 兜底 → `scripts/logs|pids`

无此文件时，模板 fallback 到 caller 控制的 `$PG_LOG_FILE`（所有调用共用一条日志，不再走隔离目录）。

`pg_resolve_paths` 的 fallback 路由表必须与 `.pg/skills/src/runtime/bin/pg-invoke-hook.py:pg_log_dir_for_skill` 保持同步（SSOT）。改动前先核对两侧。

**environments 维度的 hook 生成规则**：

- 命名约定：`<role-name>-<action>.sh`（例：`backend-start.sh`、`backend-stop.sh`、`frontend-start.sh`）。environment 级 hook 用 `prepare_env.sh` / `clean_env.sh`。
- 模板来源：`.pg/skills/examples/shell/hooks/role-{start,stop,logs,health-check}.sh` 与 `env-{prepare,clean}.sh`。本 SKILL 把模板复制到 `.pg/hooks/<role>-<action>.sh` 后，替换其中的 TODO 块为本 role 真实的 start/stop 命令。
- **不生成 restart 脚本**：`restart` action 由 `pg-invoke-hook.py` 的 fallback（stop → start → [health_check]）自动处理，无需独立脚本。
- 模板里**只**改 TODO 命令块，**不**改 trap / `pg_fail` / `pg_exit` 调用——hook 协议是 SSOT。

**注意**：`modules.<m>.build` / `modules.<m>.test.<key>` 等字段出现在 `project.yaml` 里时，必须是 `executable_command` 形态（string 或 `{cmd, timeout_seconds}`）。不要在 `project.yaml` 里写 `"build": "bash .pg/hooks/kuboard-server-build.sh"` 这种"调用 hook 来跑 build"的形式——那是错误的，会双重 timeout。

### 4. 跑 `pg doctor` 收尾

写完文件后**必须**跑 `pg doctor` 验证。失败则告知用户缺什么、需不需要回头调整。**不**自行"修" doctor 报错的字段（修法属于项目决策，不属于 SKILL）。

---

## 工作流

按顺序执行 Phase 1 → 5。**不要跳过 Phase 1**——它是后面所有推断的事实基础。

### Phase 1: 扫描仓库

**目标**：输出一份 `<project_root>/.pg/context/repo-scan.md`，列出 tech stack、模块清单、构建/测试入口。

步骤：

1. 用 `glob` 列出项目根一级目录（排除 `.git` / `node_modules` / `target` / `.pg` / `.idea` / `dist` / `build`）。这一项只扫根级，不递归。

2. 识别主构建文件：
   - `pom.xml` 存在 → Maven（看顶层 `<modules>` 段决定是否多模块）
   - `build.gradle` / `build.gradle.kts` → Gradle
   - `go.mod` → Go（看是否多 module workspace）
   - `package.json` 存在且有 `workspaces` 字段 → pnpm/yarn workspace
   - `pyproject.toml` / `setup.py` → Python
   - 都没有 → 标记为 "mixed / unknown"，让用户确认

3. 对每个识别到的 multi-module 入口（Maven `<modules>` / Go workspace / pnpm `workspaces` 数组），递归 1 层列出子模块路径。

4. 扫测试入口约定：
   - Java/Maven：默认 `src/test/java/**/*Test.java`
   - Go：默认 `*_test.go`
   - TS/Vue：默认 `*.spec.ts` / `*.test.ts` / `tests/`
   - Python：默认 `test_*.py` / `*_test.py` / `tests/`

5. 把扫到的内容写进 `.pg/context/repo-scan.md`，格式见"输出格式 §1"。**全文使用中文**（除命令、文件名、module id 这些机器契约）。

**产出**：`repo-scan.md` 已写盘。

### Phase 2: 生成 `.pg/project.yaml`

**目标**：替换 placeholder 的 `project.yaml`，填实 modules + environments。**不再生成 tracks / stages**（已从 schema 移除，`pg doctor` 不再接受这两个顶层键）。

读取 `.pg/skills/src/runtime/spec/project.schema.json`，按 schema 字段填：

- `schema: spec-driven`（固定）
- `modules`：从 `repo-scan.md` 的模块清单生成。每个 module 必须有 `root` 和 `language`（language 用 schema 允许的枚举：`java` / `go` / `typescript` / `python` / `proto` / `shell`）。
  - **多模块 Maven**：`root: <子模块相对路径>`, `language: java`。
  - **单模块**：一个 module，`root: .`, `language: <推断>`。
  - **pnpm workspace**：每个 `packages/<name>/` 算一个 module，`language: typescript`。
  - **Go workspace**：每个 module 目录一个 module。
  - 每个 module 的 `build` / `lint` / `test` 字段按 repo-scan 推断的命令写成 `executable_command` 形态（`string` 或 `{cmd, timeout_seconds}`）；`test` 是 `key → 命令` 的 map（如 `unit` / `integration` / `e2e`），按扫描到的测试入口填。
- `environments`：用 schema 的 environment 形态，但**只填合理的初值**：
  - 默认给一个 `local` environment，含一个 `dev` role 与一个 instance，host `localhost`、port `TBD: <常见端口，e.g. 8080>`。
  - role 的 `actions` 只声明本 SKILL 生成了脚本的 action（`start` / `stop`；`logs` / `health_check` 按需声明），`script` 指向 `.pg/hooks/<role>-<action>.sh`（Phase 3 生成后再写）。
  - 在 `description` 字段用 `TBD:` 标注所有未确认值。

**注意**：
- **绝不**编造端口 / host / role 拓扑——这些只能从 `repo-scan.md` 之外的信息推断（如 README、部署脚本），没有就 `TBD:`。
- 不引入 schema 之外的字段（`additionalProperties: false`）——特别是**不要**写 `tracks` / `stages` / `describe_env` 等已移除的键，写了 `pg doctor` 会校验失败。
- `description` 字段用 `TBD:` 标注需用户复核的项，例：`description: "TBD: 确认端口 8080 还是 80"`。**所有 description 一律使用中文**（除非引用代码标识符或 shell 命令保持原文）。

写盘前至少过 yaml 解析，Phase 4 会跑完整 schema 校验。

**产出**：`project.yaml` 写盘。

### Phase 3: 生成 `.pg/hooks/` 并修复 `project.yaml` 引用

**目标**：仅为 environments 节点实际声明的 `actions` / `prepare_env` / `clean_env` 生成 hook 脚本，并修改 `project.yaml` 中 action 的 `script` 字段指向生成的 hook 文件（而非内联命令）。

**module 维度的命令不进 hook**：见上方 `核心原则 §3` —— `modules.<m>.{build, lint, test.<key>}` 直接以 `executable_command` 形态写在 `project.yaml`，**不**在 `.pg/hooks/` 生成对应文件。

**生成步骤**：

1. 遍历 `environments.<env>.roles`，对每个 role：
   - 生成 `.pg/hooks/<role>-start.sh` 与 `.pg/hooks/<role>-stop.sh`（**必生成**：start 需要 stop 配对，pg-invoke-hook 的 restart fallback 依赖 stop+start 都存在）。
   - `actions.health_check` 声明了才生成 `.pg/hooks/<role>-health-check.sh`（**opt-in**，模板 `role-health-check.sh` 已实例化 `PG_INSTANCE_PORT`，无需 TODO 替换）。
   - `actions.logs` 声明了才生成 `.pg/hooks/<role>-logs.sh`。
   - **不生成 restart 脚本**：restart 由 pg-invoke-hook fallback（stop → start → [health_check]）处理。
   - start/stop 模板来源 `.pg/skills/examples/shell/hooks/role-start.sh` / `role-stop.sh`，复制后把 TODO 块替换为本 role 真实的启动/停止命令。

2. **生成后立即修改 `project.yaml`**：将 `environments.<env>.roles.<r>.actions.<action>.script` 从内联命令改为 `.pg/hooks/<role>-<action>.sh` 路径。确保 `pg-invoke-hook.py` 执行的是 hook 文件而非内联命令。

3. 遍历 `environments.<env>.prepare_env` / `clean_env`，生成 `.pg/hooks/prepare_env.sh` / `.pg/hooks/clean_env.sh`（如声明）。
   - **prepare_env 模板应调用 `pg-invoke-hook.py` 来启动/停止服务**，而非直接调用 `pg_start_bg`。格式：
     ```bash
     python3 "$PG_SKILLS_PATH/src/runtime/bin/pg-invoke-hook.py" \
         --caller "${PG_RUN_CALLER:-pg-agent}" --session "$PG_RUN_SESSION" \
         --env local --role <role> --action start --instance <instance>
     # ... 种子化逻辑 ...
     python3 "$PG_SKILLS_PATH/src/runtime/bin/pg-invoke-hook.py" \
         --caller "${PG_RUN_CALLER:-pg-agent}" --session "$PG_RUN_SESSION" \
         --env local --role <role> --action stop --instance <instance>
     ```
     这样 start/stop 逻辑只需维护一份（在 start/stop hook 中），prepare_env 不复现。caller 只允许 `pg-agent` / `ad-hoc`（`pg-invoke-hook.py` 的 `--caller` choices 白名单），继承 `PG_RUN_CALLER` 即可两者兼得。

4. 复制 SSOT 公共库（与模板同源）：
   - 源：`.pg/skills/examples/shell/hooks/lib/common.sh`
   - 目标：`.pg/hooks/lib/common.sh`
   - 作用：模板头部条件 `source lib/common.sh` + `pg_resolve_paths` 才能找到目标；`pg_resolve_paths` 优先信任 `PG_HOOK_LOG_DIR`（由 `pg-invoke-hook.py` 预拼），fallback 时按 `PG_RUN_CALLER + PG_RUN_SESSION + PG_ENV` 自拼。
   - 跳过此步：生成的 hook 仍能工作（走 `$PG_LOG_FILE`），但日志回落到 `scripts/logs`，不写到预期的 `.pg/agent/` / `.pg/ad-hoc/` 目录，`pg doctor` 也会报 `hooks_lib_common_present` warning。

5. 模板来源：从 `.pg/skills/examples/shell/hooks/role-<action>.sh` 复制并替换 TODO 块；env 级模板从 `env-prepare.sh` / `env-clean.sh` 复制。

6. chmod 755。

7. **不**改 trap / `pg_fail` / `pg_exit` 调用——hook 协议是 SSOT。

**产出**：`.pg/hooks/<role>-<action>.sh` 与 `.pg/hooks/{prepare_env,clean_env}.sh`（如适用）全部写盘且可执行，同时 `project.yaml` 中 action 引用已更新为 hook 文件路径。如果 environments 没有任何 actions（只声明静态 roles），**不**生成任何 hook，目录保持空。

### Phase 4: 跑 `pg doctor` 校验

**目标**：让用户看到一份 "OK (N checks passed)"、0 ERROR 的输出。

```bash
python3 .pg/skills/src/runtime/bin/pg doctor
```

- 如果 doctor 报 schema 错：检查 `project.yaml` 的 `TBD:` 字段是否破坏了 schema 约束（不应该，`TBD:` 只在 description 字段里，但 lint 一遍）。**最常见原因是手滑写了已移除的键**（`tracks` / `stages` / `describe_env` / `verify_merge` / `git` / action 级 `host` / `parallel` / `libvirt_uri`）——对照 `src/runtime/spec/project.schema.json` 删掉。
- 如果 doctor 报 `.pg/hooks/<x>.sh not executable`：`chmod +x`。
- 如果 doctor 报 `.pg/ not found` / `project.yaml not found`：用户没跑 `pg init`，退出并提示先跑 `pg init`。
- 如果 doctor 报 `.pg/context/agent-protocol.md is missing`：Phase 5 尚未执行——继续往下走，Phase 5.3 会复制并消除该 warning。

**产出**：doctor 输出 0 ERROR（warning 仅允许 `AGENTS.md does not reference agent-protocol` 这类待 Phase 5 处理项）。

### Phase 5: AGENTS.md drift 检测 + agent 协议注入

**目标**：解决 "项目已有 AGENTS.md 描述启动/构建命令 → 但 .pg/hooks/ 接管了 SSOT → AGENTS.md 与 hooks 协议 drift" 的历史遗留问题。Phase 5 不直接修改用户的 AGENTS.md，而是产出 drift 报告 + 生成 `.pg/context/agent-protocol.md`（agent 通用的 SSOT 发现机制文档，同时满足 `pg doctor` 的 `context_protocol_present` 检查）。

**MUST**：如果 `PG_SKIP_AGENTS_MD_MIGRATION=1`（env），跳过整个 Phase 5，便于用户拒绝该自动化。

#### Step 5.1: 扫描仓库里所有 AGENTS.md

```bash
# 用 glob 列出所有 AGENTS.md, 包括 root 和 sub-module
files=$(git ls-files '**/AGENTS.md' 'AGENTS.md' 2>/dev/null || \
        find . -name AGENTS.md -not -path './.git/*' -not -path './node_modules/*')
```

对每个文件：
- 读取全文
- 提取命令关键字命中行（行号 + 命中关键字）：
  - 模块命令关键字：`mvn | pnpm | make | go test | go build | go vet | npx`
  - Hook / 脚本路径关键字：`bash .pg/hooks/` / `pg-spec/scripts/` / `scripts/`
- 分类文件：
  - **root**: 项目根目录的 AGENTS.md
  - **sub-module**: 路径匹配 `modules.<m>.root`（如 `webvirt-backend/AGENTS.md`）
  - **tests**: 路径含 `tests/` 或 `test/`

#### Step 5.2: drift 分类（3 类）

| 类别 | 判定 | 严重度 |
|---|---|---|
| **a. 重复** | sub-module/tests AGENTS.md 出现模块命令关键字，且文件内容没说"见 .pg/context/agent-protocol.md" | low（提醒统一） |
| **b. 硬编码** | 命令关键字后紧跟具体子命令（如 `pnpm openapi` / `mvn clean install` / `make build-all`），而非通用占位符（如 `<module-cmd>`） | medium（drift 风险） |
| **c. 旧路径** | 引用 `pg-spec/scripts/` / `scripts/` / `scripts/logs/` 等非 `.pg/hooks/` 路径 | **high**（agent 跑就会失败） |

#### Step 5.3: 生成 `.pg/context/agent-protocol.md`

**模板来源**：`.pg/skills/examples/shell/agent-protocol.md`（已写好的最终形态）。

复制到 `.pg/context/agent-protocol.md`，**不做内容改写**——模板是 SSOT。

模板结构：
```
§1   SSOT 查询（pg-parse-config.py pg-agent）
§2   Hook 调用（仅 pg-invoke-hook.py 入口）
§2.5 session-id 约定
§3   日志路径（按 caller × session × env）
§5   常见错误
```

#### Step 5.4: 生成 drift patch 清单 `.pg/context/agents-md-patches.md`

**不直接修改 AGENTS.md**，产出 markdown 表格：

```markdown
# AGENTS.md Drift Patches

Generated: <ISO timestamp>
Scanner: pg-init-project Phase 5 v1

## Drift 总览

| 文件 | 类别 a | 类别 b | 类别 c | 总计 |
|---|---|---|---|---|
| webvirt-agent/AGENTS.md | 0 | 5 | 0 | 5 |
| webvirt-frontend/AGENTS.md | 2 | 3 | 0 | 5 |
| AGENTS.md (root) | 0 | 0 | 2 | 2 |

## Patch 清单

| # | 文件 | 行号 | 当前内容（节选） | 类别 | 建议改法 |
|---|------|------|------------------|------|----------|
| 1 | webvirt-agent/AGENTS.md | 60-95 | `make build` / `make proto` 等硬编码 | b | 替换为"模块构建命令: 见 .pg/context/agent-protocol.md §1" |
| 2 | AGENTS.md (root) | 166-168 | `scripts/logs/backend.log` | c | 替换为"日志路径: 按 §3 路由 (e.g. .pg/agent/<session>/dev-local-logs/)" |
```

**关键约束**：
- 必须按"先 c 后 b 后 a"排序（最严重的先列）
- 每条 patch 必须给出**具体的修改模板字符串**（不是模糊描述）
- 表格列数固定 6 列，方便用户复制到 issue tracker

#### Step 5.5: 提示用户 review + 应用

在终态汇报里**显式提示**：

```
✓ Phase 5 完成
  生成:
    - .pg/context/agent-protocol.md (agent 协议速查, SSOT)
    - .pg/context/agents-md-patches.md (drift 清单, 待 review)
  不修改: 任何 AGENTS.md

下一步:
  1. cat .pg/context/agents-md-patches.md  review patch 清单
  2. 按 patch 手动修改 AGENTS.md (或写脚本批量应用)
  3. 跑 pg doctor 验证 context_protocol_present + agents_md_protocol_link_present
```

**MUST**：不静默修改用户的 AGENTS.md——这违反 LLM agent 与用户文件的边界。

#### 跳过 Phase 5 的方式

```bash
PG_SKIP_AGENTS_MD_MIGRATION=1 <触发本 SKILL 的命令>
```

适用场景：
- 用户明确表示不需要 agent-protocol 注入
- 已有自己的 AGENTS.md 规范，不希望被报告打扰
- 在 CI / 自动化里跑 pg-init-project（避免生成 patch 清单污染 artifacts）

---

## 输出格式

### §1: `repo-scan.md`

模板（**优先中文**；标题、字段名、命令一律保持英文以便核对；说明文字全部中文）：

```markdown
# <项目名> 仓库扫描报告

Generated: <ISO 时间戳>
Scanner: pg-init-project v1.0

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
- .pg/project.yaml（X 个模块，Y 个环境；module 命令直接写在 modules.<m>.{build,lint,test.<key>} 字段里）
- .pg/hooks/<role>-<action>.sh × M（仅 environments 维度的 lifecycle actions: start/stop[+health_check/logs 按声明]）
- .pg/hooks/{prepare_env,clean_env}.sh（如声明）
- .pg/hooks/lib/common.sh（SSOT 公共库）
- .pg/context/agent-protocol.md（agent 协议速查, SSOT）
- .pg/context/agents-md-patches.md（AGENTS.md drift 清单, 待 review）

Doctor: OK (N checks passed)，0 ERROR

需人工复核的项 (TBD):
  - environments.local.roles.dev.instances[0].port: 8080（请到 application.yaml 确认）
  - <其他 TBD 项，详见 repo-scan.md>

Next steps:
  1. 在 .pg/project.yaml 与 .pg/context/repo-scan.md 中复核所有 TBD 项
  2. 补全 environments 缺失的 port / host
  3. review .pg/context/agents-md-patches.md 并按清单更新 AGENTS.md（可选）
  4. 运行 pg-auto-pilot 启动第一次自动驾驶
```

---

## 失败模式

这些是容易犯但代价高的错：

1. **不扫仓库直接编 modules** —— 凭空生成 modules 列表，跳过实际代码。**反例**：看到 `pom.xml` 假设"单模块 Java"，但实际是 4 个 Maven 子模块。
2. **编造端口/host** —— 把 8080 写死成 backend port，不验证。**反例**：8080 在项目里是 kuboard-server，但用户的 Spring Boot 实际跑 80。
3. **改 hook 协议** —— 在生成的 hook 里改 `pg_fail` / `pg_exit` 的参数或 trap 行为。**反例**：把 `set -uo pipefail` 改成 `set -e` 怕报错。这破坏 SSOT。
4. **跳过 `pg doctor`** —— 写完文件直接返回成功。**反例**：用户跑 `pg-auto-pilot` 时报 schema 错，回头找问题浪费半小时。
5. **把 placeholder 留着** —— 在 `project.yaml` 顶部保留 `placeholder` module / environment 不删。**反例**：schema 允许 `minProperties: 1` 但实际项目有 4 个 module，placeholder 残留污染 SSOT。
6. **混淆 module hook 与 environment hook 的边界** —— 把 `modules.<m>.build` 写成 `bash .pg/hooks/kuboard-server-build.sh`，期望它走 hook 协议。**错**：`modules.<m>.build` 是 `executable_command` 字段，runner 直接渲染为 `timeout N bash -c '<cmd>'` 执行，**不**调用 `.pg/hooks/<m>-<action>.sh`。`pg-invoke-hook.py` 只服务于 `environments.<env>.{prepare_env,clean_env}` 与 `environments.<env>.roles.<r>.{start,stop,...}`。项目里如果残留 `<module>-{build,test,lint}.sh`，是历史模板的产物，删除即可。
7. **忘记复制 `lib/common.sh`** —— 只复制 role/env 模板但漏掉 `lib/common.sh`。**反例**：新项目跑 hook 时日志写到 `scripts/logs` 而非 `.pg/agent/<session>/<env>-logs`，排错时找不到日志。`pg doctor` 会有 `hooks_lib_common_present` warning 提示。
8. **生成已移除的 schema 段** —— 按旧文档写出 `tracks` / `stages` / `describe_env` / `verify_merge` / `git` / action 级 `host` 等键。**反例**：`pg doctor` 报 `Additional properties are not allowed ('tracks' was unexpected)`。**正确做法**：只生成当前 `project.schema.json` 允许的键（modules + environments）。
9. **Phase 5 直接修改 AGENTS.md** —— 不允许！必须只产 drift 清单，让用户 review 后手动应用。**反例**：pg-init-project 静默改用户的 AGENTS.md，导致用户信任破裂。
10. **Phase 5 跳过 PG_SKIP_AGENTS_MD_MIGRATION 兜底** —— 用户拒绝时仍强行生成 patch 清单。**反例**：CI 跑 pg-init-project 时 `.pg/context/` 下出现污染 artifacts，diff 噪音。
11. **prepare_env 使用已移除的 caller** —— 模板里写 `--caller pg-build` / `--caller pg-regression`。**反例**：`pg-invoke-hook.py` 报 `invalid choice`，因为 `--caller` 白名单只有 `pg-agent` / `ad-hoc`。**正确做法**：`--caller "${PG_RUN_CALLER:-pg-agent}"`。

---

## 行为规约（必须遵守）

- **MUST**：扫完仓库**才**开始写 `project.yaml`。不允许"看名字猜结构"。
- **MUST**：每个 module 的 `root` 路径相对项目根，且与仓库里实际存在的路径一一对应。
- **MUST**：所有 `TBD:` 项集中在 `description` 字段，**不**污染 `root` / `language` / `cmd` 等结构化字段。
- **MUST**：跑 `pg doctor` 且输出 0 ERROR 才视为完成。
- **MUST**：复制 `.pg/skills/examples/shell/hooks/lib/common.sh` 到 `.pg/hooks/lib/common.sh`，让生成的 role-* / env-* hook 能调 `pg_resolve_paths` 做 per-caller 路径路由。
- **MUST**：Phase 5 不直接修改任何 AGENTS.md——只产 drift 清单。
- **MUST**：Phase 5 必须在 `PG_SKIP_AGENTS_MD_MIGRATION=1` 时完全跳过。
- **MUST**：prepare_env 模板中调用 `pg-invoke-hook.py` 的 `--caller` 只能传 `pg-agent` / `ad-hoc`（或 `"${PG_RUN_CALLER:-pg-agent}"` 继承当前值）。
- **MUST NOT**：引入 `additionalProperties: false` 之外的 schema 字段，尤其**不生成**已移除的 `tracks` / `stages` / `describe_env` / `verify_merge` / `git` / `instance.libvirt_uri` / action 级 `host` / `hosts` / `parallel`。
- **MUST NOT**：把 `modules.<m>.build` 写成 `bash .pg/hooks/<m>-build.sh`——那是双重封装 + 双重 timeout，runner 不识别。
- **MUST NOT**：动 `pg` CLI / `hook-helpers.sh` / `error-categories.yaml` / `hook-env-vars.yaml` / `project.schema.json`。
- **MUST NOT**：改写 `.pg/skills/examples/shell/agent-protocol.md` 模板内容——它是 SSOT，Phase 5 只做复制。
- **MUST NOT**：修改 `pg-auto-pilot` SKILL 或其它已注册 SKILL 的定义。
- **SHOULD**：每个 module 至少生成 `build` 和 `test` 命令；`lint` 仅在 language 习惯上有独立命令时（go: `go vet`）才生成。
- **SHOULD**：在最终汇报里把所有 TBD 项用清单列出来，让用户一次性 review 完。
- **SHOULD**：Phase 5 的 patch 清单按严重度排序（c → b → a），让用户优先看最严重的。

---

## 文档变更记录

- **v1.0（当前版本）**：基于当前仓库状态全面重写（对齐 pg-skills 工作流收敛到 pg-auto-pilot 单一 SKILL）：
  - **移除** tracks / stages 生成：schema 已删除这两个顶层键（`pg doctor` 不再接受），Phase 2 只生成 modules + environments。
  - **移除** Phase 2.5 code-review profile 生成：`examples/code-review/` 模板目录与 review phase 一并移除。
  - **移除** describe_env 相关：schema 字段、hook 模板、`PG_CHANGE_ID` / `PG_OUTPUT_PATH` env vars 均已删除。
  - **更新** caller 白名单：`--caller` 仅允许 `pg-agent` / `ad-hoc`（原 `pg-build` / `pg-regression` / `pg-fix-issue` 均已移除）；prepare_env 模板改用 `"${PG_RUN_CALLER:-pg-agent}"`。
  - **更新** hook 模板清单：restart 脚本不再生成（`pg-invoke-hook.py` fallback 处理）；health_check / logs 按声明 opt-in；模板来源 = `examples/shell/hooks/` 现有 6 个模板 + `lib/common.sh`。
  - **更新** Phase 5 日志路由表：仅 `pg-agent → .pg/agent/<session>/<env>-logs` 与 `ad-hoc → .pg/ad-hoc/<session>/<env>-logs` 两档。
  - **保留**：Phase 1 扫描、三态原则、hooks 走模板不发明、Phase 4 doctor 收尾、Phase 5 AGENTS.md drift 检测 + agent-protocol 注入（该注入同时满足 `pg doctor` 的 `context_protocol_present` 检查——`pg init` 不安装此文件）。
  - **失败模式 / 行为规约** 同步更新（新增 #8 生成已移除 schema 段、#11 使用已移除 caller）。
