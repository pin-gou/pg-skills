---
name: pg-regression
description: "回归测试流水线：跑测试 → 按单元调度 fix-test agent 修测试脚本 → 输出 JSON 问题清单 → [可选] 启动 runner 修复生产代码。suite 编排完全由 regression.suite 段声明（env/required_roles/module/test_keys）。"
license: MIT
compatibility: "项目根目录需要 .pg/project.yaml（v3.0 modules/environments 段 + regression.suite 段）。"
metadata:
  author: pg-spec
  version: "2.1"
---
# pg-regression

测试回归。对每个失败的测试单元使用 `pg-regression/fix-test` agent 进行系统性根因分析和修复，输出 JSON 问题清单，由 `pg-fix-regression-runner.py` 串行修复生产代码。

## 前置条件

### 1. .pg/project.yaml 配置

`pg-regression` 只依赖 `modules` / `environments` / `regression` 三段，与 `tracks` / `stages` / `fix_issue` **完全解耦**。

```yaml
regression:
  suite:
    <suite-name>:
      environment:                       # 必填, 每个 suite 独立声明
        name: <env-name>                 # ∈ environments
        required_roles: [role1, role2]   # ∈ environments.<env>.roles, 单元测试可空 list []
      module: <module-id>                # 必填, ∈ modules
      test_keys: [unit|integration|e2e, ...]  # 必填, 非空, 每项 ∈ modules.<m>.test.*
```

完整示例：

```yaml
regression:
  suite:
    frontend:
      environment:
        name: dev-local
        required_roles: [backend, agent]
      module: frontend
      test_keys: [e2e]
    backend:
      environment:
        name: dev-local
        required_roles: []
      module: backend
      test_keys: [unit]
    agent:
      environment:
        name: dev-3tier
        required_roles: [backend, agent]
      module: agent
      test_keys: [unit]
```

`pg-parse-config.py pg-regression` 会校验 7 条规则（任何一条违反即 exit 1）：

1. `regression.suite` 段存在且非空
2. 每个 suite 必填 `module` / `test_keys` / `environment.name` / `environment.required_roles`
3. `suite.<s>.module` ∈ `modules` 已定义 key
4. `suite.<s>.test_keys[i]` ∈ `modules.<m>.test.*` 已定义
5. `suite.<s>.environment.name` ∈ `environments` 已定义
6. `suite.<s>.environment.required_roles[j]` ∈ `environments.<env>.roles` 已定义
7. `regression` 顶层**禁止** `environment` 字段（防残留，硬报错）

### 2. 子 agent 定义

| Agent | 角色 |
|-------|------|
| `pg-regression/fix-test` | 执行测试脚本，诊断每个失败，调用 pg-systematic-diagnosing 判定根因，决定是否修复 |

### 3. regression 输出文件

每个 suite 的生产代码问题清单写入 `.pg/regression/<suite>.json`，供 `pg-fix-regression-runner.py` 消费：

```
.pg/regression/<suite>.json                          # 问题清单（SSOT, 跨 run 累积）
.pg/regression/<suite>-<YYYYMMDD>-<NN>/              # 单次 run 目录（本 SKILL 自动创建）
├── temp/                                            # 编排器中间文件
├── <env>/logs/                                      # hooks 日志（prepare_env/start/stop）
│   └── env.prepare_env.log
│   └── role.<role>.<action>@<instance>.log
├── fix-issues/                                     # per-issue 审计目录（prompt + log + result）
│   └── <idx>-<slug>/
│       ├── 1-prompt.md          # 发给 fix-prod agent 的提示词
│       ├── 2-agent.log          # opencode run 的 stdout + stderr
│       └── 3-result.json        # 修复结果（含 PR 链接）
├── fix-test/                                      # Phase 2 fix-test 留痕（prompt + response + result）
│   └── <idx>-<target-slug>/
│       ├── 1-prompt.md          # 发给 fix-test agent 的提示词
│       ├── 2-response.md        # fix-test agent 的完整回复
│       └── 3-result.json        # 结构化结果
├── fix-issue-runner-summary.md                    # 人类可读汇总报告
└── report.md                                      # 单次运行的人类可读汇总报告
```

首次运行自动创建目录。

---

## 整体流程

```
Phase 0: 前置检查 → Phase 1: 执行测试并按单元分组 → Phase 2: 按 concurrency 并行/串行调度 fix-test agent → Phase 3: 导出 JSON 问题清单 + 汇总报告 → [可选] Phase 4: 启动 runner 修复生产代码
```

---

## 编排器执行工作流

### Phase 0: 前置检查

#### 0.1 验证 pg-regression/fix-test agent 可用性

向 `pg-regression/fix-test` agent 发送可用性确认消息，agent 必须在回复中明确包含 "✅ pg-regression/fix-test 已就绪"。

未正确响应 → 判定不可用，终止。

#### 0.1a 计算 run 目录

```bash
SUITE=<suite>
DATE=$(date +%Y%m%d)
EXISTING=$(ls -d .pg/regression/${SUITE}-${DATE}-* 2>/dev/null | wc -l)
SEQ=$(printf "%02d" $((EXISTING + 1)))
RUN_DIR=".pg/regression/${SUITE}-${DATE}-${SEQ}"
CHANGE="${SUITE}-${DATE}-${SEQ}"
mkdir -p "$RUN_DIR/temp"
echo "📁 Run dir: $RUN_DIR"
```

#### 0.2 环境初始化（envSetup）

从 `regression.suite.<s>.environment.name` 读取目标 env（**不**读全局，每个 suite 独立）。

```bash
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-regression --suite {suite}
```

输出只包含本 suite 所需的 module 和 environment，不包含其他 suite/env 的无关信息。

从输出取 `regression.suite.<s>.environment.name` 作为目标 env。

若 `environments.<env>.prepare_env` 存在，通过 `pg-invoke-hook.py` 统一执行（v3.2 起, runtime 层独立 CLI, 与 pg-build / pg-fix-issue 共享入口）：

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --session "$CHANGE" --env <env-name> --action prepare_env --skill pg-regression
```

`timeout_seconds` 由 `pg-invoke-hook.py` 从 `environments.<env>.prepare_env.timeout_seconds` 自动反查并写入 spec, LLM 不传。

若 `prepare_env` 不存在 → 跳过。

> ⚠️ **强制终止规则（严格模式）**：`prepare_env` 脚本必须用 `set -e` 包裹或 `&&` 串联命令，**失败立即终止整个流程**。不允许 `;` 或裸换行连接——这不会因失败而中断，会导致后续命令在错误环境中执行。

#### 0.3 启动服务

从 `regression.suite.<s>.environment.required_roles` 读取本 suite 需要的 roles（**不**做跨 stage 累积推导，**不**用 tracks）。

> required_roles 为空 list（如 unit test）→ 跳过本步骤，不调任何 hook。

**v3.2 改动**: 不再通过 `start-services.sh` 批量启停 (该脚本会手写 yaml 解析 + spec 渲染, 绕过 hooks 协议). 编排器 LLM 在 SKILL 指引下, 对每个 role 的每个 instance 显式循环调 `pg-invoke-hook.py invoke-hook --action start`. 任一 instance 启动失败 → exit 1, 不继续, 不重试, 不探测端口——**端口冲突由 actions.start 内部处理**。

```bash
# 通用模板: 编排器按 suite.environment.required_roles 展开.
# 编排器 LLM 收到 SKILL 后, 按以下规则构造 bash 循环:
#
#   1. ROLES = suite.environment.required_roles (从 pg-parse-config 输出读)
#   2. 对每个 role, 从 .pg/project.yaml 读 instances[] (或由 SKILL 提示 LLM 调
#      python3 -c "import yaml; print([i['name'] for i in yaml.safe_load(open('.pg/project.yaml'))['environments']['<env>']['roles']['<role>']['instances']])")
#   3. 串行循环调 pg-invoke-hook.py
#
# 示例: backend suite 跑 dev-local, required_roles=[backend]:
ENV=<env-name>
for ROLE in ${ROLES[@]}; do
  for INSTANCE in $(python3 -c "
import yaml
for i in yaml.safe_load(open('.pg/project.yaml'))['environments']['${ENV}']['roles']['${ROLE}']['instances']:
    print(i['name'])
"); do
    python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
      --session ${CHANGE} --env ${ENV} --role ${ROLE} --instance ${INSTANCE} --action start \
      --skill pg-regression
    # 任一 instance 失败 → exit 1, 后续 role/instance 不再继续
  done
done
```

> **日志路径**: `pg-invoke-hook.py` 通过 `--session $CHANGE` + `--skill pg-regression` 路由，自动写入 `.pg/regression/${CHANGE}/${ENV}/logs/role.<role>.start@<instance>.log`。由于 `CHANGE` 与 `RUN_DIR` 的后缀一致（`<suite>-<date>-<seq>`），它们指向同一目录，编排器可直接 `tail -100 ${RUN_DIR}/*/logs/role.*.start@*.log` 排错。

#### 0.4 清理临时目录

```bash
mkdir -p "$RUN_DIR/temp" && rm -f "$RUN_DIR"/temp/{suite}-test-output.log "$RUN_DIR"/temp/{suite}-phase1-failures.json "$RUN_DIR"/temp/{suite}-phase1-known-issues.json "$RUN_DIR"/temp/{suite}-fix-results.json
```

所有临时文件统一写入 `$RUN_DIR/temp/`。`.pg/regression/*-[0-9]*-[0-9][0-9]/` 已在 `.gitignore` 中忽略。

#### 0.5 检查 runner 冲突

pg-fix-regression-runner.py 正在运行时拒绝执行，避免竞争：

```bash
RUNNER_PID=$(for pid in $(pgrep -f "pg-fix-regression-runner" 2>/dev/null); do
  exe=$(readlink "/proc/$pid/exe" 2>/dev/null) || continue
  case "$exe" in */python3*|*/python*) echo "$pid";; esac
done)
if [ -n "$RUNNER_PID" ]; then
  echo "❌ pg-fix-regression-runner.py (PID=$RUNNER_PID) 正在运行，拒绝启动 pg-regression"
  exit 1
fi
```

---

### Phase 1: 执行测试并按单元分组

#### 1.1 运行全部测试（按 test_key 串行）

`test_keys` 是 list，Phase 1 对每个 key 跑一轮（串行），每轮结果分别写入 `$RUN_DIR/temp/{suite}-{test_key}-test-output.log`。

> **为何不通过 `pg-invoke-hook.py` 走 hook 协议**: 测试命令属于 module 维度, 按 `.pg/skills/README.md` §Hook 协议边界, **不**走 `pg-invoke-hook.py`, 直接走 `pg-run-hook.py`（裸 JSON spec 形式）。`pg-invoke-hook.py` 只服务于 environments 维度 (env-level prepare_env/clean_env + per-role start/stop/logs/tail). module 维度的 `modules.<m>.test.<key>` 直接以 `executable_command` 形式渲染为 `timeout N bash -c '<cmd>'`, 由 `pg-run-hook.py` 统一执行, 注入 `PG_PROJECT_ROOT` / `PG_SKILLS_PATH` / `PG_RUN_CALLER` (硬注入) + `PG_ENV` / `log_path` (spec 注入); module 维度不依赖 `PG_MODULE` / `PG_MODULE_ROOT` 等 module-only 变量.

**runAllCommand 推导规则**（per test_key）：

通过 `pg-parse-config.py --resolve-module-test` 拿到 `{cmd, timeout_seconds}` 渲染好的对象（含 `timeout N bash -c '<cmd>'` 包装），再嵌入 `pg-run-hook.py` 的 JSON spec 的 `command` 字段。这样编排器不需要手工归一化 string/dict 形式，也不会漏掉超时。

**通用模板**：

```bash
python3 .pg/skills/src/runtime/lib/pg-run-hook.py <<EOF
{"command": $(python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test <module> <test_key>), "suite": "<suite>", "skill": "pg-regression", "env": "<env>", "module": "<module>", "log_path": "${RUN_DIR}/temp/<suite>-<test_key>-test-output.log"}
EOF
```

**⚠️ 必须用嵌套 `command` 字段，不要手写 `cmd` + `timeout` 平铺**。`modules.<m>.test.<key>` 在 config 里可能是 string 或 object 形式，手写 `cmd` 会把 dict 当字符串运行。

例如 `frontend` suite, `test_keys: [e2e]`：

```bash
python3 .pg/skills/src/runtime/lib/pg-run-hook.py <<EOF
{"command": $(python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test frontend e2e), "suite": "frontend", "skill": "pg-regression", "env": "dev-local", "module": "frontend", "log_path": "${RUN_DIR}/temp/frontend-e2e-test-output.log"}
EOF
```

例如 `backend` suite, `test_keys: [unit]`：

```bash
python3 .pg/skills/src/runtime/lib/pg-run-hook.py <<EOF
{"command": $(python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test backend unit), "suite": "backend", "skill": "pg-regression", "env": "dev-local", "module": "backend", "log_path": "${RUN_DIR}/temp/backend-unit-test-output.log"}
EOF
```

例如 `agent` suite, `test_keys: [unit, integration]`（跑 2 轮）：

```bash
python3 .pg/skills/src/runtime/lib/pg-run-hook.py <<EOF
{"command": $(python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test agent unit), "suite": "agent", "skill": "pg-regression", "env": "dev-3tier", "module": "agent", "log_path": "${RUN_DIR}/temp/agent-unit-test-output.log"}
EOF
python3 .pg/skills/src/runtime/lib/pg-run-hook.py <<EOF
{"command": $(python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test agent integration), "suite": "agent", "skill": "pg-regression", "env": "dev-3tier", "module": "agent", "log_path": "${RUN_DIR}/temp/agent-integration-test-output.log"}
EOF
```

**超时处理**：helper 返回的 `cmd` 已经是 `timeout N bash -c '<cmd>'` 形式，N 来自 `modules.<m>.timeout_seconds`（模块级默认）或 `modules.<m>.test.<key>.timeout_seconds`（per-command 覆盖）或 schema 默认 1800。编排器**不要再**手设 `timeout` 字段，会被 `command.timeout_seconds` 覆盖且徒增出错点。

如果某个 `<module>/<test_key>` 在 config 中不存在（`--resolve-module-test` 返回 `null`），**跳过**该 test_key，不要报错。

#### 1.2 按测试单元分组失败用例

**outputFormat 推导规则**（由 `suite.module` 决定）：

| suite.module | outputFormat | 传入 `--type` | groupBy |
|--------------|--------------|---------------|---------|
| `frontend` | `playwright-json` | `playwright` | `file` |
| `backend` | `maven-surefire` | `maven` | `class` |
| `agent` | `go-test` | `maven` (复用) / TODO: `go` | `class` |

> TODO: `pg-parse-test-results.py` 暂未实现 `go-test` 类型，agent suite 暂以 maven 解析（fallback）。后续补 `go` 类型。

对每个 test_key 的输出分别解析：

```bash
python3 .pg/skills/src/opencode/scripts/pg-parse-test-results.py parse \
  --type <推导出的 type> \
  --log-file ${RUN_DIR}/temp/{suite}-{test_key}-test-output.log \
  --out ${RUN_DIR}/temp/{suite}-{test_key}-phase1-failures.json
```

所有 failures 合并去重，输出到 `${RUN_DIR}/temp/{suite}-phase1-failures.json`：

```json
{
  "summary": { "total": <N>, "passed": <N>, "failed": <N>, "skipped": <N> },
  "failedUnits": [
    {
      "target": "<groupBy 决定的单元标识>",
      "count": <失败数>,
      "issues": [
        { "status": "failed|error", "test": "<测试名>", "line": <行号> }
      ]
    }
  ]
}
```

其中 `target` 的值：
- `groupBy: file` → 脚本路径，如 `tests/e2e/specs/xxx.spec.ts`
- `groupBy: class` → 类名，如 `com.example.project.compute.service.TemplateVmServiceTest`

如果 `summary.failed == 0` → 跳转到 Phase 3。

#### 1.3 读取 knownIssues

从 `.pg/regression/<suite>.json` 提取 `skipped_targets` 字段（**不再读 md 文件**）：

```bash
test -f .pg/regression/<suite>.json \
  && python3 -c "
import json
data = json.load(open('.pg/regression/<suite>.json', encoding='utf-8'))
out = {'skipped_targets': data.get('skipped_targets', [])}
json.dump(out, open('${RUN_DIR}/temp/{suite}-phase1-known-issues.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
" \
  || echo '{"skipped_targets": []}' > ${RUN_DIR}/temp/{suite}-phase1-known-issues.json
```

文件不存在时输出空 skipped_targets（首次运行正常）。这些单元跳过 fix-test 调用。

---

### Phase 2: 调度 pg-regression/fix-test Agent

对每个失败单元（排除 knownIssues 中已记录的），按 `concurrency` 设置并行度调度 `pg-regression/fix-test` agent。

#### 2.1 构造 fix-test 调用

**runSingleCommand 推导规则**（per test_key）：

通过 `pg-parse-config.py --resolve-module-test` 拿到带 `timeout` 包装的 cmd 字符串，将 `<target>` 作为额外参数追加到末尾。

**通用模板**：

```bash
RESOLVED=$(python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test <module> <test_key>)
TIMEOUT=$(echo "$RESOLVED" | python3 -c "import json,sys; print(json.load(sys.stdin)['timeout_seconds'])")
BASE_CMD=$(echo "$RESOLVED" | python3 -c "import json,sys; print(json.load(sys.stdin)['cmd'])")
# BASE_CMD 已是 'timeout N bash -c "..."' 形式, 但要把 <target> 追加到内部 bash -c 里
# 简化做法: 提取内部 cmd, 自己重新包 timeout
INNER_CMD=$(echo "$BASE_CMD" | sed -nE "s/^timeout [0-9]+ bash -c '(.*)'$/\1/p")
runSingleCommand="timeout $TIMEOUT bash -c '${INNER_CMD} <target>'"
```

> 之所以要拆-重-包而不是简单 append：`<target>` 必须出现在 `bash -c` 内部，不然会被 `timeout` 当成它的参数而不是 shell 的。

- `outputFormat: playwright-json` → `cd <module-name> && pnpm test <target>`
- `outputFormat: maven-surefire` → `cd <module-name> && mvn test -Dtest=<target>`
- `outputFormat: go-test` → `cd <module-name> && go test -run <target>` (TODO)

向 `pg-regression/fix-test` agent 传递：
- `target` — 测试单元标识（如 `xxx.spec.ts` 或 `xxxTest.java`）
- `runSingleCommand` — 推导后的实际命令
- `testOutputFile` — 全量测试输出文件路径（per test_key）
- `issueList` — 该单元内所有失败测试的清单

调用模板：

```
pg-regression/fix-test，请诊断并修复以下测试失败：

测试目标：<target>

配置上下文：
- runSingleCommand: <runSingleCommand>
- testOutputFile: <testOutputFile>

失败问题清单：
- [failed] <test-name>: <error-summary>
- [error] <test-name>: <error-summary>

请对每个问题调用 pg-systematic-diagnosing 进行根因分析，然后统一决定修复策略。并将你最终的处理结果汇报给我。
```

> 📌 **派发后必做**：把上面构造的完整提示词先落盘到 `/tmp/fix-test-prompt-${IDX}.md`，agent 返回后把完整回复落盘到 `/tmp/fix-test-response-${IDX}.md`，再调 `pg-record-fix-test.sh`（详见 §2.3a）。**留痕是强制步骤**，不可跳过。

#### 2.2 调度策略

**concurrency 推导规则**（由 `suite.module` 决定）：

| suite.module | concurrency | 原因 |
|--------------|-------------|------|
| `frontend` | 4 | 浏览器进程争用限制 |
| `backend` | 1 | Maven 编译冲突限制 |
| `agent` | 1 | go test 缓存友好 |

| concurrency | 模式 | 行为 |
|-------------|------|------|
| 1 | 串行 | 派一个 agent → 等它完成 → 派下一个 |
| > 1 | Work Queue | 活跃池上限 = concurrency，完成一个派一个 |

串行模式也是 Work Queue 的特例（并发上限为 1），使用同一套调度逻辑即可。

#### 2.3 收集 fix-test 执行结果

每个 `pg-regression/fix-test` agent 返回：
- 诊断报告列表（每个问题一条）
- 修复执行结果（fixed/skipped/reported）
- 需要上报生产代码问题的清单

#### 2.3a 留痕 fix-test 执行记录

> ⚠️ **强制留痕约束** — 每个 `pg-regression/fix-test` agent 返回后、调度下一个 agent 前，**必须**调用 `pg-record-fix-test.sh` 把 prompt / response 落到 `${RUN_DIR}/fix-test/<idx>-<target-slug>/`。**不可跳过**——无留痕的 agent 调用视为审计缺失。

每个 agent 返回后立即调用 helper：

```bash
PROMPT_FILE="/tmp/fix-test-prompt-${IDX}.md"
RESPONSE_FILE="/tmp/fix-test-response-${IDX}.md"

# 1. 把即将发送给 agent 的完整提示词写到临时文件
cat > "$PROMPT_FILE" <<'__PROMPT_EOF__'
<phase 2.1 构造的完整提示词，含 target / runSingleCommand / testOutputFile / issueList>
__PROMPT_EOF__

# 2. 派发 agent (Task tool), 等返回

# 3. 把 agent 的完整回复写到临时文件
cat > "$RESPONSE_FILE" <<'__RESPONSE_EOF__'
<agent 返回的完整文本，含诊断报告 + 修复说明>
__RESPONSE_EOF__

# 4. 调用 helper 留痕 (Phase 2.3a 必须步骤)
bash .opencode/skills/pg-regression/scripts/pg-record-fix-test.sh \
  --run-dir "$RUN_DIR" \
  --idx "$IDX" \
  --target "$TARGET" \
  --prompt-file "$PROMPT_FILE" \
  --response-file "$RESPONSE_FILE" \
  [--result-json '{"summary":{"fixed":N,"cantFix":N},"fixes":[],"cantFixIssues":[],"modifiedFiles":[]}']
```

**留痕目录结构**：

```
${RUN_DIR}/fix-test/${IDX}-${slug}/
├── 1-prompt.md          # 编排器构造的完整提示词
├── 2-response.md        # agent 完整回复
└── 3-result.json        # 可选, 结构化结果 (与 fix-results.json 对齐)
```

- `<idx>` 使用全局递增序号（与 `fix-issues/` 共享同一序号空间），从 1 开始
- `<slug>` 由 helper 自动生成：`<target>` 转 kebab-case、限长 40、字母数字 + 连字符
- helper **幂等**：已存在的 1-prompt.md / 2-response.md 不会覆盖（防止 agent 重复 dispatch 冲掉审计）
- `--result-json` 缺失时仅落 prompt + response

**为什么使用 helper 而不是内联 Python 脚本**：
- prompt / response 通常含多行 JSON 片段、嵌套引号、特殊字符，heredoc + Python 三引号在大体积下易转义失败
- helper 走 `--prompt-file` / `--response-file` 文件路径，避免大字符串内嵌
- helper 内置参数校验（idx 正整数、文件存在、target-slug 长度限制）
- 复用 `pg-fix-regression-runner.py` 的 audit 目录布局（`fix-issues/`），便于人/工具对照

---

### Phase 2a: 提交测试脚本修改到默认分支

Phase 2 全部 fix-test agent 执行完毕后，编排器检查工作区中是否有测试文件被修改。
若有，通过 git 提交并推送到 default 分支，确保 Phase 4 的生产 PR 不会被测试修改污染。

```bash
if git status --porcelain | grep -qE '\.(spec|test)\.' 2>/dev/null ||
   git status --porcelain | grep -qE 'tests?/' 2>/dev/null; then
  git add -A
  git commit -m "fix(${SUITE}): fix test scripts after regression"
  git push origin master
  echo "✅ 测试脚本修改已提交到 master"
else
  echo "✅ 无测试脚本修改，跳过提交"
fi
```

> **为什么直接 push 到 master 而非创建 PR**：测试脚本修改风险低、不碰生产逻辑，直接提交效率更高。生产代码修复在 Phase 4 中通过独立分支+PR 走 code review。

---

### Phase 3: 导出 JSON 问题清单 + 汇总报告

#### 3.1 从 agent 报告中提取结构化数据

编排器阅读每个 agent 的报告，提取结构化字段，构造 `${RUN_DIR}/temp/{suite}-fix-results.json`：

```json
{
  "date": "<YYYY-MM-DD>",
  "suiteLabel": "<suite 名>",
  "testRun": { "total": <N>, "passed": <N>, "failed": <N>, "skipped": <N> },
  "agents": [{
    "target": "<target>",
    "overview": { "total": N, "passed": N, "failed": N },
    "stats": { "fixed": N, "unfixable": N },
    "unfixableIssues": [{
      "title": "问题标题",
      "component": "test data|backend|frontend|environment",
      "file": "path/to/source/file",
      "affectedTests": "`<target>` - test names",
      "expected": "期望行为",
      "actual": "实际行为",
      "rootCause": "根因描述",
      "orchestratorSteps": ["步骤 1", "步骤 2"]
    }]
  }]
}
```

#### 3.2 写问题清单到 `.pg/regression/<suite>.json`

将 Phase 2 agent 报告的 unfixableIssues 与 Phase 1.3 提取的 skipped_targets 合并，写入 `.pg/regression/<suite>.json`。**写入时做 schema 适配**：runner 期望的字段名（`description` / `test_targets` / `id`）与 unfixableIssues 原始字段（`rootCause` / `affectedTests` / 无 `id`）不同，统一在此阶段转换。

字段映射规则：

| runner 期望 | unfixableIssues 原始 | 适配 |
|------------|---------------------|------|
| `id` | (无) | 生成 `{slug}-{md5(suite:title)[:6]}` |
| `description` | `rootCause` | 直接复用 |
| `test_targets` | `affectedTests` (string) | 从反引号提取为 list |
| `title/component/file/expected/actual` | 同名 | 直接复用 |
| `auto_fixed` | 同名（缺省 `false`） | 透传，Phase 4 runner 只处理 `false` |
| `rationale` | 同名（仅 B 类有值） | 透传 |
| `category` | 同名（`A<id>`/`B<id>`/`C<id>`） | 透传，便于追溯 |

```bash
mkdir -p .pg/regression
python3 <<'EOF'
import hashlib, json, re
from datetime import datetime
from pathlib import Path

def make_issue_id(title, suite):
    slug = re.sub(r'[^a-z0-9-]', '-', title.lower())[:30]
    slug = '-'.join(x for x in slug.split('-') if x)[:30]
    h = hashlib.md5(f"{suite}:{title}".encode()).hexdigest()[:6]
    return f"{slug}-{h}" if slug else f"issue-{h}"

suite = "{suite}"
fix_results = json.load(open(f"{RUN_DIR}/temp/{suite}-fix-results.json", encoding="utf-8"))
known_issues = json.load(open(f"{RUN_DIR}/temp/{suite}-phase1-known-issues.json", encoding="utf-8"))

issues = []
for agent in fix_results.get("agents", []):
    for uf in agent.get("unfixableIssues", []):
        title = uf.get("title", "").strip()
        if not title:
            continue
        affected_raw = uf.get("affectedTests", "")
        if isinstance(affected_raw, str):
            test_targets = re.findall(r'`([^`]+)`', affected_raw)
        else:
            test_targets = list(affected_raw or [])
        issues.append({
            "id": make_issue_id(title, suite),
            "title": title,
            "description": uf.get("rootCause", ""),
            "component": uf.get("component", ""),
            "file": uf.get("file", ""),
            "test_targets": test_targets,
            "expected": uf.get("expected", ""),
            "actual": uf.get("actual", ""),
            "auto_fixed": uf.get("auto_fixed", False),
            "rationale": uf.get("rationale"),
            "category": uf.get("category"),
        })

suite_doc = {
    "suite": suite,
    "generated_at": datetime.now().isoformat(),
    "skipped_targets": known_issues.get("skipped_targets", []),
    "issues": issues,
}
Path(f".pg/regression/{suite}.json").write_text(
    json.dumps(suite_doc, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(f"✅ .pg/regression/{suite}.json: {len(issues)} issues, {len(suite_doc['skipped_targets'])} skipped")
EOF
```

#### 3.3 写人类可读的汇总报告

```bash
mkdir -p "$RUN_DIR"
DATE_TAG=$(date +%Y%m%d-%H%M)
python3 .opencode/skills/pg-regression/scripts/pg-regression-summary.py \
  --suites .pg/regression/*.json \
  --out "${RUN_DIR}/fix-issue-runner-summary.md"
```

#### 3.3a 写 report.md

从 `fix-results.json` 生成单次运行的人类可读报告，落盘到 run 目录：

```bash
python3 <<'REPORT_EOF'
import json, sys
from pathlib import Path

suite = "{suite}"
RUN_DIR = f".pg/regression/{suite}-..."

fix_file = Path(f"{RUN_DIR}/temp/{suite}-fix-results.json")
if not fix_file.exists():
    # fallback: 单 test_key 场景可能按 test_key 命名
    fix_file = Path(f"{RUN_DIR}/temp/{suite}-phase1-fix-results.json")
    if fix_file.exists():
        pass
    else:
        print("⚠️ fix-results.json not found, skip report.md")
        sys.exit(0)

data = json.loads(fix_file.read_text())
agents = data.get("agents", [])

total_fixed = sum(a["stats"]["fixed"] for a in agents)
total_unfixable = sum(a["stats"]["unfixable"] for a in agents)
status_icon = "✅" if total_unfixable == 0 else "⚠️"

lines = []
lines.append("# 回归测试报告\n")
lines.append(f"| 元数据 | |\n|---|---|\n")
lines.append(f"| 套件 | {data.get('suiteLabel', suite)} |\n")
lines.append(f"| 日期 | {data.get('date', 'unknown')} |\n")
lines.append(f"| 状态 | {status_icon} {'全部通过' if data['testRun']['failed'] == 0 else '部分修复'} |\n\n")

lines.append("## 测试结果\n\n")
lines.append(f"- **总计**: {data['testRun']['total']}\n")
lines.append(f"- **通过**: {data['testRun']['passed']}\n")
lines.append(f"- **失败（含已修复）**: {data['testRun']['failed']}\n")
lines.append(f"- **跳过**: {data['testRun']['skipped']}\n\n")

if agents:
    lines.append("## 测试修复\n\n")
    lines.append("| 测试目标 | 结果 | 已修复 | 无法修复 |\n")
    lines.append("|----------|------|--------|----------|\n")
    for a in agents:
        icon = "✅" if a["overview"]["failed"] == 0 else "❌"
        lines.append(f"| {a['target']} | {icon} | {a['stats']['fixed']} | {a['stats']['unfixable']} |\n")
    lines.append("\n")

unfixable = [uf for a in agents for uf in a.get("unfixableIssues", [])]
if unfixable:
    lines.append("## 生产代码问题\n\n")
    lines.append("| # | 标题 | 组件 | 文件 | 根因 |\n")
    lines.append("|---|------|------|------|------|\n")
    for i, uf in enumerate(unfixable, 1):
        lines.append(f"| {i} | {uf.get('title', '')} | {uf.get('component', '')} | {uf.get('file', '')} | {uf.get('rootCause', '')} |\n")
    lines.append("\n")
else:
    lines.append("## 生产代码问题\n\n无\n")

report_path = Path(f"{RUN_DIR}/report.md")
report_path.write_text("".join(lines), encoding="utf-8")
print(f"✅ 报告已写入: {report_path}")
REPORT_EOF
```

#### 3.4 输出最终状态

```
✅ 问题清单已写入 .pg/regression/<suite>.json
📋 汇总报告: ${RUN_DIR}/fix-issue-runner-summary.md
📋 详细报告: ${RUN_DIR}/report.md
📋 修复留痕: ${RUN_DIR}/fix-test/
📁 Run 目录: ${RUN_DIR}
```

---

### Phase 4: [可选] 启动 runner 修复生产代码

> **编排器职责**：Phase 3 完成后，检查用户提示词是否包含"完成回归测试后启动修复循环"。包含则进入 Phase 4；否则跳过渡过本阶段，流程结束。

如有生产代码问题，以子进程方式启动 pg-fix-regression-runner.py，然后 SKILL 正常退出（不等待 runner 完成）。

```bash
if python3 -c "
import json, sys, os
for f in sys.argv[1:]:
    if not os.path.exists(f):
        continue
    if os.path.basename(f).startswith('summary-'):
        continue
    d = json.load(open(f))
    if d.get('issues'):
        sys.exit(0)
sys.exit(1)
" .pg/regression/*.json 2>/dev/null; then
  nohup python3 .opencode/skills/pg-regression/scripts/pg-fix-regression-runner.py \
    --run-dir "$RUN_DIR" \
    > "$RUN_DIR/fix-issue-runner.log" 2>&1 &
  echo "✅ pg-fix-regression-runner.py 已启动 (PID=$!)"
  echo "   日志: $RUN_DIR/fix-issue-runner.log"
  echo "   结果: $RUN_DIR/fix-issues/"
else
  echo "✅ 无生产代码问题，跳过 runner"
fi

exit 0
```

---

## 多 test_keys 编排要点

`test_keys` 是 list 时，编排器对每个 test_key 独立跑 Phase 1-2：

| test_keys | 行为 |
|-----------|------|
| `[unit]` | 1 轮跑完 |
| `[unit, integration]` | 先跑 unit → 修 unit 失败 → 再跑 integration → 修 integration 失败 |
| `[unit, integration, e2e]` | 3 轮串行 |

每轮 Phase 1 产出独立的 `${RUN_DIR}/temp/{suite}-{test_key}-phase1-failures.json`，Phase 2 调度 fix-test 时 `testOutputFile` 也指向对应 test_key 的输出。

每轮的 `testRun` 统计合并到 Phase 3 的 fix-results.json。

---

## per-suite env 编排要点

不同 suite 走不同 env 时的处理：

```yaml
regression:
  suite:
    frontend:
      environment: {name: dev-local, required_roles: [backend, agent]}
    agent:
      environment: {name: dev-3tier, required_roles: [backend, agent]}
```

| 场景 | 行为 |
|------|------|
| 单 suite | 跑该 suite 的 envSetup + 启 required_roles |
| 多 suite 同 env | envSetup 跑 1 次, 各 suite 启各自 required_roles |
| 多 suite 不同 env | 每个 env 的 prepare_env 跑 1 次, 每个 env 启各自 required_roles |

注意：`/4-pg-regression <suite>` 每次只跑一个 suite。跑多个 suite 需要用户多次调用（或用脚本编排）。

---

## 修复执行原则

1. **只修测试脚本，不动生产代码** — fix-test 遵循根因边界原则
2. **保留测试意图** — 不削弱断言，不删除测试覆盖
3. **新增 skip 禁止** — 不得以任何方式将测试标记为 skip/fixme，如果前置条件不满足，应在最终报告中记录为"无法修复的问题"
4. **统一修复策略** — 所有问题诊断完毕后统一决定，不逐个临时决策

---

## 自动修复边界分类

fix-test agent 必须按下表判定每条失败属于哪一类，决定自动修或上报。三类互斥，全量问题诊断完毕后统一判定。

### 🟢 A 类 — 必须自动修

根因明确在测试脚本本身，修复不改变被测系统契约。

| ID | 类型 | 修复边界 |
|----|------|---------|
| A1 | 断言期望值漂移（字段引用/值不对） | 改测试侧引用，不动生产语义 |
| A2 | 选择器过期（前端 e2e） | 改 selector，不动组件 |
| A3 | 等待逻辑不足/过度 | 改等待条件/超时 |
| A4 | 测试框架 API 误用 | 改用正确 API |
| A5 | 测试数据构造错误（fixture/mock 本身写错） | 改 fixture 写法，不动种子数据 |
| A6 | 测试隔离问题 | 改 setup/teardown |
| A7 | 测试环境变量硬编码 | 改测试 helper 的 env 解析 |
| A8 | 断言精度问题（浮点/时间戳/JSON 字段顺序） | 改用近似匹配 |

### 🟡 B 类 — 条件性自动修（必须附 `rationale`）

可能涉及测试约定或命名规范，但只要不改变"被测什么"且依据充分，可修。每条修复必须在报告里写 `### Rationale` 段，否则归 C 类。

| ID | 类型 | 自动修条件 |
|----|------|----------|
| B1 | 断言放宽（`toBe` → `toMatchObject`） | 原始意图是"存在性/结构"而非"精确等值" |
| B2 | 新增测试 helper（重复代码抽取） | ≥3 处重复，helper 本身有单测 |
| B3 | 调整 mock 匹配新接口约定 | 接口已确认变化（有 issue/PR/CHANGELOG），改动不掩盖真实 bug |
| B4 | 重命名测试内局部变量/函数 | lint/类型报错明确指向 |
| B5 | 添加/调整 `beforeEach` 清理步骤 | 不得新增 skip，不得删断言 |
| B6 | 加 retry 限制（应对 C10 偶发失败） | playwright `retry ≤ 1` / junit `@Retry(1)` / go `-count=1`，超过即归 C 类 |

### 🔴 C 类 — 禁止自动修，必须上报为"无法修复的问题"

根因不在测试脚本，或修复会丢测试意图，或属于跨域决策。`auto_fixed=false`，进 `.pg/regression/<suite>.json`，触发 Phase 4 runner。

| ID | 类型 | 上报 component |
|----|------|---------------|
| C1 | 生产代码 bug | `production-code` |
| C2 | 生产代码行为变更（接口语义） | `production-code` |
| C3 | 数据库 schema 不匹配 | `production-code` 或 `environment` |
| C4 | 环境配置错误 | `environment` |
| C5 | 种子/测试数据缺失，或 fixture/seed 文件被改 | `test-data` |
| C6 | 新增任何 `skip` / `fixme` / `.only` / `.todo` / `@Disabled` / `@Ignore` | — |
| C7 | 删除或弱化断言（使其永远通过） | — |
| C8 | 跨服务契约变更（前端期望旧 schema、后端返回新 schema） | `production-code` |
| C9 | 第三方依赖版本/接口破坏 | `environment` 或 `production-code` |
| C10 | 并发/资源竞争偶发失败（无法稳定复现） | `production-code` 或 `environment`（**B6 例外**） |
| C11 | 测试覆盖率因修复而下降（删用例/合并用例） | — |

### 分类决策矩阵

| 类别 | 自动修 | 进 `.pg/regression/<suite>.json` | 触发 Phase 4 runner | `auto_fixed` 字段 |
|------|------|----------------------------------|--------------------|------------------|
| A 类 | ✅ | ❌（无需上报） | ❌ | `true` |
| B 类 | ✅（需 rationale） | ❌（无需上报） | ❌ | `true` + `rationale` |
| C 类 | ❌ | ✅ | ✅ | `false` |

### Phase 2 收尾校验（边界守护）

编排器在每个 fix-test agent 返回后、Phase 2a 提交前，**必须**执行 `pg-check-fix-test-boundary.py` 扫描 fix-test 提交的 git diff。命中以下任一硬规则 → 立即 `git checkout -- <test_files>` 回滚 → 把这些用例转写为 `unfixableIssues`（`auto_fixed: false`）上报：

| 规则 | 检测内容 |
|------|---------|
| **C6** | diff 中出现新增 `\.skip\|\.only\|\.todo\|@Disabled\|@Ignore\|xit(\|xdescribe(` |
| **C7** | `-` 行 `expect(`/`assert` 数量 > `+` 行对应数量 |
| **C11** | `-` 行包含 `it(`/`test(`/`@Test`（用例被删） |
| **C5** | 改动文件路径匹配 `**/fixtures/**`、`**/seeds/**`、`**/*.sql`、`**/test-data/**` |

### schema 字段约定

每条修复记录必须含 `auto_fixed` 字段（`true`|`false`），B 类额外含 `rationale` 字段。Phase 3.2 写 `.pg/regression/<suite>.json` 时透传，Phase 4 runner 只处理 `auto_fixed=false` 的 issue（已自动修的不再重复处理）。缺省值 `auto_fixed=false`（兼容旧数据）。

---

## 模板变量替换

编排器在调用前需要替换以下占位符：

| 占位符 | 替换为 |
|--------|--------|
| `{suite}` | `regression.suite` 段中被测的 suite key |
| `{target}` | 当前处理的测试单元标识 |
| `{test_key}` | 当前 test_key（unit/integration/e2e） |

---

## Troubleshooting

| 问题 | 原因 | 解决 |
|------|------|------|
| fix-test 未确认就绪 | agent 指令加载失败 | 检查 `.opencode/agents/pg-regression/fix-test.md` |
| envSetup 环境启动失败 | 依赖服务未安装 | 检查 `environments.<env>.prepare_env.script` 错误输出 |
| agent 执行超时 | 问题复杂或环境问题 | 检查 agent 日志，手动处理 |
| envSetup 中段失败后流程继续 | 编排器未用 `set -e`/`&&` 包裹命令 | 用 `set -e` 包裹整个 envSetup 序列，确保任何步骤失败立即终止 |
| `pg-parse-config.py` 报 regression.suite 缺失 | config.yaml 未配 `regression.suite` 段 | 追加 `regression:\n  suite:\n    <name>:\n      environment: {name: ..., required_roles: [...]}\n      module: ...\n      test_keys: [...]` |
| `pg-parse-config.py` 报 top-level environment 残留 | config.yaml 顶层有 `regression.environment` | 删除该行, env 必须在 suite 内声明 |
| `pg-invoke-hook.py` 报 role/instance not defined | config.yaml `environments.<env>.roles.<role>` 或 instances[] 缺失 | 检查对应 env/role 段, 确认 required_roles 名字拼写正确 |
| `pg-invoke-hook.py` 报"not in project.yaml environments" | env 名拼写错 | 检查 `regression.suite.<s>.environment.name` ∈ project.yaml `environments` 列表 |
| 启动服务时 pg-invoke-hook.py 卡住 (bash 调用未返回) | LLM 未用 `next_call_timeout_seconds` 设 bash tool timeout | 按 prompt_final_no_modify 返回的 `next_call_timeout_seconds` 设超时 (典型如 backend start = 300s) |
| Maven 增量编译未检测到变化 | Java 源文件未修改 | 手动 `touch` 源文件 |
| 测试数据库容器未运行 | docker-compose 未启动 | 执行 docker compose up -d |
| agent suite 跑 dev-3tier 失败 | 本地无 box-1/box-2 | 改用 dev-local 或配置 SSH 免密到测试机 |
| runner 被 pg-regression 拒绝启动 | runner 进程残留（或 pgrep 误判 pgrep/自身 bash 进程） | `ps aux | grep pg-fix-regression-runner | grep -v grep` 确认；确认后 `kill $(ps aux | grep pg-fix-regression-runner | grep -v grep | awk '{print $2}')` 后重试 |
| runner 报告"无 suite JSON 文件" | pg-regression 未在 runner 之前执行 | 先跑 pg-regression |
| runner 创建 PR 失败 | GITEE_TOKEN 失效 | `export GITEE_TOKEN=<新 token>` |
| Phase 3.2 写出问题清单为空 | `${RUN_DIR}/temp/{suite}-fix-results.json` 无 agents/unfixableIssues | 检查 Phase 2 fix-test agent 是否上报生产代码问题 |
| Phase 1.3 跳过列表为空但应有 known issues | `.pg/regression/<suite>.json` 不存在或缺少 `skipped_targets` 字段 | 首次运行无 known issues 正常, 旧 md 文件已废弃 |
