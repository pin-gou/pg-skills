---
name: pg-fix-issue
description: 复现问题、收集错误信息、进行系统化诊断、对根因进行修复。触发词："修复问题"、"fix issue"
license: MIT
compatibility: 项目根目录需要 `.pg/project.yaml` (v3.0 4-段结构 modules/environments/tracks/stages + fix_issue). service 启停走 `pg-invoke-hook.py` (runtime 层独立 CLI, 与 pg-build 共享入口, hooks 协议; 旧路径 `pg-pipeline-runner.py invoke-hook` 仍作为 thin wrapper 可用).
metadata:
  author: pg-spec
  version: "3.0"
---

> **v3.0 Breaking Change**：本 SKILL 的 service 启停入口从"编排器在 SKILL 端预渲染
> `resolved_actions` 字符串 → 塞进 executor 的 `type: shell`"模式，改为"编排器 LLM
> 显式调用 `pg-invoke-hook.py invoke-hook` CLI"模式（runtime 层独立 CLI, 与 pg-build 共享）。
> executor agent (`pg-fix-issue/executor`) 的 `type: rebuild_and_restart` operation 已删除，executor
> 职责收窄到模块命令（build/lint/test）+ 辅助验证（shell/api_call/log_filter/git_diff_check）。
>
> **Migration Note**：
> - 旧 prompt 含 `type: rebuild_and_restart` 不再有效；service 启停改由编排器调 invoke-hook
> - `pg-parse-config.py pg-fix-issue` 不再输出 `resolved_actions`（统一由 runner 渲染）
> - `fix_issue.ask_prepare_env` / `ask_clean_env` 字段保留，但语义改为"runner 是否在编排器跑 prepare/clean 后给用户展示日志"——不改变 prepare/clean 的执行入口（仍是 invoke-hook）

此 SKILL 的所有 track 相关命令、路径、验证策略**不写死任何具体 track**，一律从 `.pg/project.yaml` 的 `tracks` 段解析。

# pg-fix-issue

用户描述问题后，主 agent 规划调用链路分析，然后**主 agent 自己修复 bug**，派遣 `pg-executor` 执行验证流水线（机械操作），主 agent 审核结果并按模板输出最终结论。

## 前置条件

### 1. .pg/project.yaml 配置（v3.0 4 段结构 + fix_issue）

项目根目录的 `.pg/project.yaml` **v3.0 必须包含 `modules` / `environments` / `tracks` / `stages` 四段**。`pg-fix-issue` 还额外依赖顶层 `fix_issue` 段（缺失则使用下文默认值）。

**pg-fix-issue 使用的 config.yaml 字段约定**：

| 字段路径 | 用途 | Phase | 缺失处理 |
|---------|------|-------|---------|
| `modules.<id>.root` | 文件路径 → module 反查 | Phase 0.1 | 该 module 不可识别 |
| `modules.<id>.build` / `.lint` / `.test.<test_key>` | 验证/编译/lint 命令 | Phase 5 | 该类操作跳过 |
| `environments.<env>.description` | Phase 3 列出可用环境给用户选 | Phase 3 | 不列出该 env |
| `environments.<env>.prepare_env` / `.clean_env` | 准备/清理环境脚本 | Phase 3/6 | 不询问 prepare/clean |
| `environments.<env>.roles.<role>.actions.{start,stop,restart,logs,tail,health_check}` | 精细化启停（替代整栈重启） | Phase 5 | 由 LLM 调 `pg-invoke-hook.py invoke-hook` 触发（不进 executor operations）；字段缺失时回退到让编排器用 `type: shell` 调 `pg-run-hook.py`（不推荐） |
| `tracks.<id>.modules` | file path → track 反查 | Phase 0.1 | 该 track 不可识别 |
| `fix_issue.max_iteration_count` | **主 agent 整体迭代上限** | Phase 6 | 默认 5 |
| `fix_issue.partial_success_threshold` | 成功率低于此值算修复失败 | Phase 5b | 默认 0.7 |
| `fix_issue.ask_environment_choice` | Phase 3 是否询问环境选择 | Phase 3 | 默认 true |
| `fix_issue.ask_prepare_env` / `.ask_clean_env` | Phase 3 是否询问 prepare/clean 时机 | Phase 3 | 默认 true |
| `fix_issue.allow_manual_verification` | executor 全失败时是否允许切人工 | Phase 6 ESCALATE | 默认 true |
| `fix_issue.escalation_artifacts` | ESCALATE 时保留的产物清单 | Phase 6 | 默认全保留 |
| `stages[*].test_key` | 决定 `modules.<m>.test.<test_key>` 用哪个 key | Phase 5 | 默认 unit |

**pg-fix-issue 显式忽略的字段**（属于其他 SKILL）：

| 字段 | 归属 | 为什么忽略 |
|------|------|----------|
| `build_rules` | pg-build | pg-fix-issue 不派 dev/verify agent，无需 prompt 注入 |
| `pipeline.order` | 已废弃 | v3.0 不再用 |
| `verifyMerge` | pg-verify-and-merge | 与单 bug 修复无关 |
| `flyway` | pg-verify-and-merge | pg-fix-issue 不动 schema |

**⚠️ 命令执行位置规约**：
- 所有命令从**项目根路径**执行，executor 不会自动切换 cwd
- 需切换目录的命令在配置中**显式写 `cd <dir> && <cmd>`**
- `actions.*.script` 引用的脚本应自包含 cwd 处理（脚本内部自己 `cd`）
- 每次 `pg-parse-config.py` 会在 stderr 输出规约提示

### 2. 子 agent 定义

此 SKILL 期望以下子 agent 存在：

| Agent | 角色 |
|-------|------|
| `pg-fix-issue/executor` | 执行编排器下发的验证流水线（机械操作），返回结构化 JSON |

**`pg-fix-issue/coder` 已被删除** — 编排器自己修复所有 bug。

---

## 工作流程

```
用户描述问题
    ↓
[Phase 0: 加载配置 + 决定链路分析模式]
    ↓
[Phase 1: 调用链路分析]（编排器主导 + explore 辅助）
    ↓
[Phase 2: 规划复现步骤]
    ↓
[Phase 3: 用户确认]
    ↓
[Phase 4: 编排器自己修复]（用 Edit/Write 工具）
    ↓
[Phase 5: 验证]
    ↓
[Phase 6: 失败处理 / 滚动修复]
    ↓
按模板输出最终结论
```

---

## Phase 0: 加载项目配置

编排器在开始任何 phase 之前**必须先**执行：

```bash
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-fix-issue
# ↑ stdout 输出 modules / environments / tracks / stages / fix_issue 五段；失败 → ESCALATE
# v3.0: 不再输出 resolved_actions（统一由 runner invoke-hook 渲染）
```

**修复上下文（编排器内存中维护，v3.0 新增）**：

由于 pg-fix-issue 编排器不写 state 文件（不进入 runner state machine），以下字段由编排器 LLM 在 conversation context 里维护，并在调用 invoke-hook 时透传给 runner：

```yaml
fix_issue_context:
  change_name: "fix-<YYYY-MM-DD>-<bug-slug>"     # 编排器 Phase 0 自动生成, 用于 invoke-hook --session
                                                 # (独立于 pg-build 的 change, 避免污染 .pg/changes/)
  selected_env: "<env-name>"                      # Phase 3 用户选的环境
  affected_modules: [...]                          # Phase 0.1 反向匹配得到
  affected_tracks: [...]                            # 同上
  prepare_env_requested: true|false                # Phase 3.3 用户选项
  clean_env_requested: true|false                  # Phase 3.4 用户选项
  reproduction_steps: [...]                        # Phase 2 产出
  success_criteria: [...]                          # Phase 2 产出
  failure_criteria: [...]                          # Phase 2 产出
  iteration_count: 0..N                            # Phase 6 累加
```

**关键引用约定**：后续 phase 引用任何命令时，**按以下路径读取**，**绝不写死**：

| 引用路径 | 读取方式 |
|---------|------|
| `modules.<m>.build` | `pg-parse-config.py --resolve-module-build <m>` → `{cmd, timeout_seconds}` |
| `modules.<m>.test.<test_key>` | `pg-parse-config.py --resolve-module-test <m> <test_key>` → `{cmd, timeout_seconds}`（test_key 由 `stages[*].test_key` 决定） |
| `modules.<m>.lint` | `pg-parse-config.py --resolve-module-lint <m>` → `{cmd, timeout_seconds}` |
| `environments.<env>.roles.<role>.actions.{start,stop,restart,logs,tail,health_check}` | **v3.0 变更**：不再由 parser 预渲染。LLM 调 `pg-invoke-hook.py invoke-hook --session <C> --env <ENV> --role <ROLE> --instance <INSTANCE> --action <ACTION> [--tail-lines N]` 触发；runner 内部从 project.yaml 反查 spec |
| `environments.<env>.prepare_env` / `.clean_env` | LLM 调 `pg-invoke-hook.py invoke-hook --session <C> --env <ENV> --action prepare_env\|clean_env`（v3.0 新增，无需 `--role`/`--instance`） |
| `tracks.<t>.modules` | `pg-parse-config.py pg-fix-issue` 输出 `tracks.<t>.modules` |
| `fix_issue.max_iteration_count` | 5（**主 agent 整体迭代上限，Phase 6 用**） |
| `fix_issue.ask_environment_choice` | true（Phase 3 是否问环境） |

> **模块命令必须经 helper 解析**：直接读 `modules.<m>.test.<key>` 字段在 schema 升级后可能是 object 形式（带 `cmd` + `timeout_seconds`），手动 `cmd: "{modules.X.Y}"` 会把 dict 当字符串运行。helper 统一处理 string/object 两种形式 + timeout 注入。

**配置校验失败**：pg-parse-config.py 会自动校验所有 `bash <path>.sh` 引用，缺失脚本会 exit 1 → 编排器 ESCALATE。

### 0.1 确定受影响 track（file path → track 反向匹配）

```yaml
affected_tracks: [<track-id-1>, <track-id-2>, ...]
affected_modules: [<module-id-1>, <module-id-2>, ...]
```

**匹配算法**（编排器执行）：

```
1. bug_files = [调用链路分析中识别出的所有相关文件路径]
2. for each file in bug_files:
     for each (track_id, track_def) in tracks:
       for each module_id in track_def.modules:
         if file.startswith(modules[module_id].root + "/"):
            affected_tracks.add(track_id)
            affected_modules.add(module_id)
3. affected_tracks = sort by config.yaml 中 tracks 的声明顺序
```

**反例**：不要按"复杂度"分类 track 数 → 不存在"简单 / 复杂"模式分支，Phase 1 统一派遣 explore。

### 0.2 加载 `fix_issue` 段（缺省值兜底）

如果 `config.fix_issue` 不存在，使用以下缺省值（与 schema default 一致）：

```yaml
fix_issue_defaults:
  max_iteration_count: 5
  partial_success_threshold: 0.7
  ask_environment_choice: true
  ask_prepare_env: true
  ask_clean_env: true
  allow_manual_verification: true
  escalation_artifacts:
    - diag_logs
    - call_chain_analysis
    - phase2_output
    - executor_json_history
    - git_diff_state
```

---

## Phase 1: 调用链路分析（**统一派遣 explore**）

**规则**：所有情况统一派遣 `explore` subagent 做全景代码探索。**不根据 track 数、文件数、复杂度做条件判断**——没有小于 medium 的 bug。

### 1.1 派遣 explore 的标准 prompt 模板

```
Task 工具调用:
  - description: "探索 <具体问题>"
  - prompt: |
      探索目标：[一句话描述你要查的 bug 相关代码]

      关键关注点：
      1. <符号1> 定义在哪、做什么
      2. <符号2> 被谁调用、调用链如何
      3. <关键函数> 的输入输出、边界条件

      输出要求：
      - 文件:行号 + 函数签名
      - 关键代码片段（不超过 20 行/片段）
      - 可能的故障点（基于代码逻辑推断）
  - subagent_type: "explore"
```

### 1.2 输出模板

```markdown
## 调用链路分析

### 1. 正向链路
[画出来]

### 2. 反向链路
[画出来]

### 3. 关键代码位置（含受影响 track）
| 链路段 | 文件:行号 | 关键函数 | 候选故障 | 受影响 track |
|--------|----------|---------|---------|------------|

### 4. 候选故障点
| 段 | 候选故障 | 怎么验证 |
|----|---------|---------|

### 5. 开放问题
[需要用户补充的信息]

### 6. 关键日志搜索关键词
[executor 复现时直接 grep]

### 7. 验证策略（自动生成）
[每个受影响 track 的字段列表]

### 8. 影响半径（修复波及的文件）
**强制扫描范围**（必须逐项检查，不可跳过）：

| 扫描目录 | 说明 | 检查方法 |
|---------|------|---------|
| 1. 根因函数/方法所在的文件 | bug 发生的直接位置 | explore 已定位 |
| 2. 同 Controller/Handler 的同名/同模式方法 | 相同的 bug pattern 可能被复制到邻近方法 | grep `auth.getName()`、`getPrincipal()` 等当前 bug 的特征模式 |
| 3. 同模块/同包的相似逻辑 | 相同的调用模式（如当前 bug 中的工具方法在其他类中的复制） | 按当前 bug 的特征字符串（方法名 / 关键 token）逐一 grep |
| 4. 跨模块的同逻辑 | 其他模块的同类工具方法（按 `project.yaml` 的 `modules[*]` / `tracks[*].modules` 列表逐个搜索） | 在每个 module 目录下 grep 特征字符串 |

**输出格式**：

| 文件 | 为什么受影响 | 是否需要同步修改 |
|------|------------|----------------|
```

**反模式**：
- ❌ 主 agent 亲自 `Read` 5+ 个大文件
- ❌ 派遣 explore 后又自己重读全文
- ❌ 写死具体 track 名或命令

### 1.3 Phase 1 → Phase 2 衔接

explore 返回的摘要**直接**作为 Phase 2 的输入。编排器**不再重读** explore 已读过的全部内容，只在构造 `success_criteria` 或 `reproduction_steps` 时读 1-2 个关键段落（如 `Read(file, offset=100, limit=50)`）。

**⚡ 强制门控**：Phase 2（reproduction_steps + success_criteria + affected_files）全部完成后，方可进入 Phase 3（用户确认）。以下条件全部满足前，严禁进入 Phase 3：
- ✅ `reproduction_steps` 已定义（至少 3 步，可机械执行）
- ✅ `success_criteria` 已定义（至少 1 条，SMART 准则）
- ✅ `failure_criteria` 已定义（至少 1 条，避免漏判）
- ✅ `affected_files` 已识别完毕（含测试、配置等周边文件）
- ✅ 影响半径扫描（第 8 节）已完成，无遗漏的同模式方法
- ✅ Phase 2 自检清单已逐项确认

---

## Phase 2: 规划复现步骤 + 成功标准

**复现步骤的颗粒度由 Phase 1 候选故障点驱动**。每个候选故障点都对应一段"怎么验证"命令。

**本阶段必须产出 2 个东西**：
1. `reproduction_steps` — 如何**复现** bug
2. `success_criteria` — 修复到什么程度**算成功**（**新增**）

### 2.1 复现步骤（reproduction_steps）

**复现步骤要求**：
- 步骤必须具体、可操作，基于真实执行
- 前端问题必须包含「浏览器 DevTools 观察」操作
- 必须使用 `pg-parse-config.py` 解析的命令
- 步骤**可被机械执行**（不是阅读代码推测）

### 2.2 成功标准（success_criteria）

**核心原则**：**修复成功的标准**必须在动手前**明确列出**，避免"修到哪算哪"。

**每条标准必须是 SMART 准则**：

| 准则 | 含义 | 例子 |
|------|------|------|
| **S**pecific | 具体可测量 | "VM status = RUNNING"（不是"VM 正常"）|
| **M**easurable | 可被机械验证 | "virsh list 显示 running" / "API 返回 RUNNING" |
| **A**chievable | 可达成 | 不要写"100% 完美"这种 |
| **R**elevant | 与 bug 直接相关 | 不要列无关指标 |
| **T**ime-bound | 有时间窗口 | "5 秒内完成" / "30 秒内显示" |

**成功标准模板**：

```yaml
success_criteria:
  - id: SC-1
    description: "VM 创建后 30 秒内变为 RUNNING 状态"
    verify_method: "api_call"  # or "shell" / "log_filter"
    verify_args:
      method: GET
      url: /api/compute.../instances/{instance_id}
      expect_field: status
      expect_value: "RUNNING"
    timeout: 30s

  - id: SC-2
    description: "virsh list 显示该 VM 处于 running 状态"
    verify_method: "shell"
    verify_args:
      cmd: "virsh list --all"
      expect_match: "<vm_name>.*running"
    timeout: 30s

  - id: SC-3
    description: "前端页面能查询到该实例并显示 RUNNING 状态"
    verify_method: "api_call"  # 或 browser (见 pg-browser-testing-with-devtools)
    verify_args:
      method: GET
      url: /api/compute.../tenants/.../instances
      expect_match: "<vm_name>"
```

**强制必含项**：所有 success_criteria 必须包含以下 **SC-FORCE-1**：

```yaml
  - id: SC-FORCE-1
    description: "修复代码已部署到运行中的服务（binary 版本包含本次修复）"
    verify_method: "log_filter"  # 或 "api_call"
    verify_args:
      service: <role>                              # 来自 project.yaml environments.<env>.roles[*].name
      patterns: ["<fixed-symbol-or-class-name>"]   # 本次修复引入的独有可识别符号（详见 §5.1.4.3）
      expect_found: true
    timeout: 30s
```

这条标准的含义是：修复不仅仅是"单元测试通过"，还必须确认运行中的服务是**编译了本次变更的 binary**。编排器必须通过 `invoke-hook --action start` 重新部署后，再通过日志匹配或 API 响应确认运行版本包含修复代码。

**支持的成功标准类型**：

| 类型 | verify_method | 适用场景 |
|------|--------------|----------|
| API 响应字段 | `api_call` | 后端接口返回特定值 |
| 命令行输出 | `shell` | virsh / kubectl / journalctl 等 |
| 日志匹配 | `log_filter` | 关键事件日志 |
| 前端 UI | `browser` | 调用 `pg-browser-testing-with-devtools` |
| 单元测试 | `test` | 调用 `pipeline.tracks.<id>.test` |
| 端到端流 | `e2e_flow` | 多步骤组合（API→命令行→日志） |

**反例标准**（必须**也**列出，明确"什么算没修好"）：

```yaml
failure_criteria:
  - id: FC-1
    description: "VM 一直停留在 PENDING 状态超过 5 分钟"
    verify_method: "api_call"
    ...
  
  - id: FC-2
    description: "agent 日志中包含 'missing domain type attribute' 或 'an os <type> must be specified'"
    verify_method: "log_filter"
    ...
```

### 2.3 完整 Phase 2 产物结构

```yaml
phase2_output:
  reproduction_steps:
    - step: 1
      action: "调用 login API 获取 token"
      command: "curl -X POST ..."
    - step: 2
      action: "创建测试 VM"
      command: "curl -X POST ..."
    - step: 3
      action: "等待 30 秒后查询 VM 状态"
      command: "curl -X GET ..."
    - step: 4
      action: "检查 virsh 实际状态"
      command: "virsh list --all"
  
  success_criteria:
    - id: SC-1
      description: "..."
      verify_method: ...
      verify_args: ...
    - id: SC-2
      description: "..."
      verify_method: ...
      verify_args: ...
  
  failure_criteria:
    - id: FC-1
      description: "..."
      verify_method: ...
      verify_args: ...
```

**最低要求**：
- 至少 1 条 `success_criteria`
- 至少 1 条 `failure_criteria`（**强烈建议**，避免漏判）

### 2.4 Phase 2 自检清单（进入 Phase 3 前的强制检查）

进入 Phase 3 前，编排器必须逐项确认以下清单全部完成：

**复现步骤检查**：
- [ ] reproduction_steps 至少 3 步，每步可机械执行
- [ ] 前端问题包含「浏览器 DevTools 观察」操作
- [ ] 步骤不依赖阅读代码推测

**成功标准检查**：
- [ ] 至少 1 条 success_criteria
- [ ] 每条 criteria 满足 SMART 准则
- [ ] 每条 criteria 的 verify_method 明确（如 `api_call` / `shell` / `log_filter`）
- [ ] 每条 criteria 的 verify_args 可直接构造 executor operation

**反例标准检查**：
- [ ] 至少 1 条 failure_criteria（明确"什么算没修好"）
- [ ] failure_criteria 可被机械验证

**影响半径检查**：
- [ ] bug 根因所在的文件已确认
- [ ] 所有会因修复而需要修改的**周边文件**已识别（测试文件、其他调用方、配置等）
- [ ] 对每个周边文件明确了是否需要同步修改

**完整性检查**：
- [ ] reproduction_steps + success_criteria + failure_criteria + affected_files 已汇总为 phase2_output

---

## Phase 3: 请用户确认

**必须使用 `question` 工具**展示复现步骤、成功标准，**并按 `fix_issue.ask_*` 配置确认环境选择**。

**关键要求**：复现步骤 + 成功标准 + 环境选择 必须**分多个 question 一次性展示**给用户确认。

### 3.1 复现步骤 + 成功标准确认

```
question 工具调用:
  questions: [{
    question: "以下复现步骤和成功标准是否准确可行？\n\n问题描述：...\n\n复现步骤：\n1. ...\n2. ...\n\n成功标准（修复后需全部满足）：\n- [SC-1] ...\n- [SC-2] ...\n\n反例标准（如出现则修复失败）：\n- [FC-1] ...\n- [FC-2] ...",
    header: "确认修复方案",
    options: [
      { label: "可以，开始执行", description: "复现步骤 + 成功标准都准确" },
      { label: "需要调整", description: "步骤或标准有问题" }
    ]
  }]
```

> **前端问题额外确认**：如果问题描述涉及前端页面行为（如页面加载、按钮点击、数据显示），则必须额外确认用户已在前端浏览器中完成以下操作再进入 Phase 4：
> 1. 硬刷新页面（Command/Ctrl + Shift + R）确保加载最新前端代码
> 2. 重新执行触发 bug 的操作步骤
> 3. 在 `reproduction_steps` 中包含"打开浏览器 DevTools → Network 面板 → 观察对应 API 请求的响应"的步骤
>
> 编排器应在 Phase 3 question 中附加提示："此问题涉及前端页面，请确认已在前端浏览器中完成一次硬刷新验证后，再继续执行"

### 3.2 环境选择（仅当 `fix_issue.ask_environment_choice == true`）

**关键约束**：环境列表从 config.yaml **顶层 `environments` 动态读取**，编排器**不预置**环境推断规则（不同项目 environment 列表不同；选错代价巨大，必须用户确认）。

```
question 工具调用:
  questions: [{
    question: "请选择修复环境（基于 config.yaml 的 environments 段）：\n
               可用环境：\n
               - dev-local: 本机全栈开发\n
               - dev-3tier: 3 机 topology（agent 在 box-1/box-2）\n
               \n
               编排器建议（基于 affected_modules）：\n
               - 若 affected_modules 含 'agent'：dev-3tier（跨主机验证）\n
               - 其他：dev-local（单模块足够）",
    header: "环境选择",
    options: [
      { label: "dev-local", description: "本机全栈，单模块验证足够" },
      { label: "dev-3tier", description: "3 机，跨 agent 验证" }
    ]
  }]
```

**反 SSOT 警告**：编排器**绝不**根据文件路径硬编码推荐环境。推荐只是建议，用户必须确认。

### 3.3 prepare_env 时机（仅当所选 env 有 `prepare_env` 且 `fix_issue.ask_prepare_env == true`）

```
question 工具调用:
  questions: [{
    question: "是否在修复前执行 environments.dev-local.prepare_env？\n
               prepare_env 会重置 services + db + agent（耗时 {timeout_seconds}s）\n
               \n
               v3.0 改动：\n
                - 选中“是”后, 编排器会在 Phase 4 修复前, 主动调:\n
                  python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\\n
                    --session <C> --env dev-local --action prepare_env --skill pg-fix-issue\n
               - runner 走 hooks 协议, 自动注入 PG_RUN_SESSION / PG_RUN_CALLER / PG_ENV / timeout_seconds\n
               - 失败 → 立即 ESCALATE, 不进入 Phase 4",
    header: "prepare_env 时机",
    options: [
      { label: "是，先 prepare", description: "确保环境干净，避免旧状态干扰（推荐）" },
      { label: "否，直接开始", description: "沿用当前环境状态" }
    ]
  }]
```

### 3.4 clean_env 时机（仅当所选 env 有 `clean_env` 且 `fix_issue.ask_clean_env == true`）

```
question 工具调用:
  questions: [{
    question: "修复完成后是否执行 environments.dev-local.clean_env？\n
               clean_env 会停止所有 services + 清理 db 状态\n
               \n
               v3.0 改动：\n
                - 选中“是”后, 编排器在 Phase 5 验证成功之后, 主动调:\n
                  python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\\n
                    --session <C> --env dev-local --action clean_env --skill pg-fix-issue\n
               - runner 走 hooks 协议, 自动注入 timeout_seconds",
    header: "clean_env 时机",
    options: [
      { label: "是，clean 一下", description: "释放资源，停止 services" },
      { label: "否，保留现场", description: "保留现场便于人工验证（推荐）" }
    ]
  }]
```

### 3.5 question 顺序与合并

**Phase 3 一次 conversation message 内可发多个 question tool 调用**，按 3.1 → 3.2 → 3.3 → 3.4 顺序排列。**用户必须全部回答后才能进入 Phase 4**。

**用户确认的不仅是复现步骤，还包括成功标准 + 环境选择**。

**必须等待用户明确回复确认，才能进入下一阶段**。

---

## Phase 4: 编排器自己修复

**所有 bug 由编排器自己修复**（不派遣 coder subagent）。

**操作流程**：
1. **读代码** — 使用 codegraph / Read / Grep
2. **设计修复** — 基于调用链路分析的候选故障点
3. **Edit/Write** — 实际修改代码
4. **诊断日志** — 按需添加（遵守规约，见下方）

**诊断日志规约**（仍由编排器遵守）：
1. 位置：只打在入口/边界/消息分发器
2. 形式：稳定前缀 `DIAG:`
3. 数量：单次诊断不超过 3 处
4. 生命周期：Phase 5b 验证通过后**必须清理**
5. 不替代根因分析：加日志前清楚"我在验证什么假设"

**TDD 建议**（如适用）：
- 修复前先加失败测试
- 跑测试确认红 phase
- 修复后跑测试确认绿 phase
- 测试由编排器自己写（不派遣 test agent）

### 4.1 验证假设循环（替代"随手加日志重跑"）

当 Phase 5a 返回的 executor JSON 表明修复未完全成功时，按"收集证据 → 形成假设 → 验证"循环处理。

#### 4.1.1 多组件边界逐层证据收集

当问题涉及多个组件（如前端 → API → 后端 → 数据库），在验证修复效果时逐层确认：

```
对于每个组件边界：
  - 记录请求发出时的实际数据（后端收到的参数、agent 收到的命令内容）
  - 记录边界处的响应数据（后端返回的状态码、agent 打印的日志）
  - 验证环境/配置传递是否正确（URL 格式、字段名、版本号）
  - 标记第一次数据异常的位置
```

#### 4.1.2 根因层级定位

假设失败后，用下表帮助定位根因属于哪一层：

| 症状层级 | 说明 | 例子 |
|---------|------|------|
| 数据层 | 传递的数据本身有问题 | ID 不存在、格式错误、null |
| 逻辑层 | 数据处理逻辑错误 | 过滤条件错误、计算错误 |
| 接口层 | API 契约不匹配 | 字段名不同（snake vs camel）、类型不同 |
| 配置层 | 环境或配置问题 | 端口、路径、版本 |
| 依赖层 | 外部服务问题 | 下载 URL 404、认证失败 |

#### 4.1.3 假设验证循环

```
1. 形成单一假设
   "根据 executor 返回的错误信息 XXX，我认为根因是 YYY"
   清楚写下来

2. 设计验证实验
   需要什么最小信息来验证？
   优先选择：新增 log_filter operation（匹配 agent/backend 已知日志）
   其次选择：加 1 处 DIAG 日志，用 executor 重跑验证

3. 执行验证
   → 构造 operations → 派 executor → 读 JSON
   → 确认新日志是否证明/反驳假设

4. 如果假设被推翻
   记录被推翻的假设（避免重复）
   回到步骤 1 形成新假设
```

**严禁**：同时加多处 DIAG 日志"看看哪行没执行"。

---

## Phase 5: 验证

**硬性规则**：不得以任何理由绕过 executor。

编排器必须构造 `operations` 列表，派遣 `pg-executor` 执行。禁止任何形式的直接操作（`curl`、`journalctl`、`ssh`、`systemctl`、`cp`、`go build` 等）。

**调试迭代也不例外**：编排器修复代码 → 构造 operations → 派遣 executor → 读 JSON 结果。**不允许手动重跑单步**——executor 返回的 JSON 包含失败详情、状态码、日志片段，足以定位问题。信息不足时扩 `log_filter` operations 重派，而非绕过。

### 5.1.1.1 绕过前兆识别与应对

当验证过程中**出现以下任何想法**时，必须意识到这是绕过的前兆，应立即停下改造为 executor operation：

| 想法 | 实际应做的事 |
|------|------------|
| "executor 返回的结果不太对，我手动 curl 确认一下" | 构造 `log_filter` 或更精确的 `api_call` 重新派遣 executor |
| "只需要快速手动跑一个命令" | 这是最危险的信号——立即停下，改造为 executor operation |
| "executor 编译/启动失败了，我自己操作更快" | 改进 `modules.<m>.test`/`lint` 脚本（编排器走 `runner invoke-hook` 不接触启停），而非绕过 executor 手动跑 |
| "executor 伪造了结果" | 派遣一个新的 executor 调用（新 message），并交叉验证 1 条关键 operation |

**经验教训**：executor 返回非预期结果（如 code 40001 而非 1001）时，编排器跳过 executor 直接手动 curl 验证是**最常见的绕过模式**。正确做法是：构造带有 `log_filter` 的 operations 重新派遣，在 JSON 结果的 evidence 中确认后端版本。

### 5.1.2 禁止操作清单

| 操作 | 应替换为 |
|------|---------|
| `go build` / `mvn compile` | `pg-invoke-hook.py invoke-hook --action start`（由编排器 LLM 调, 触发 backend/frontend/agent 构建+启动一体化脚本） |
| `systemctl restart` | 同上 |
| `curl http://localhost:...` | `api_call` operation |
| `journalctl` / `grep 日志` | `log_filter` operation |
| `git diff --stat` | `git_diff_check` operation |
| `pgrep` / `systemctl status` | `shell` operation |
| `bash .pg/hooks/<role>-start.sh` 直接调 | `pg-invoke-hook.py invoke-hook`（**v3.0 强制**：所有 service 启停走 hooks 协议，不绕过 runner） |
| `python3 pg-invoke-hook.py invoke-hook` 在 `operations[].cmd` 里执行 | **禁止**：executor 不接触 service 启停；编排器在 Phase 5 显式调 invoke-hook（不进 operations 列表） |

### 5.1.3 调试特殊说明

如果 executor 返回的 JSON 不足以判断根因，编排器应做两件事之一：
1. 新增 `log_filter` operation（扩大日志匹配范围）再派一次
2. 加 DIAG 日志到源码中，再派一次（Phase 4 允许的 3 处上限）

### 5.1.4 operations 列表构造原则

**v3.0 breaking change**：operations 列表**不再包含** service 启停（`type: shell` 调 `.pg/hooks/<role>-*.sh` 或 `rebuild_and_restart`）。service 启停一律由编排器在 Phase 5 显式调 `pg-invoke-hook.py invoke-hook`（见下方"Deployment 工具调用约定"）。

**operations 只包含**：模块命令（`type: test` / `type: lint`）+ 辅助验证（`type: shell` / `type: api_call` / `type: log_filter` / `type: git_diff_check`）。

**模块命令（build / lint / test）必须用 `--resolve-module-*` 解析后再写进 operations**，因为 `modules.<m>.test.<key>` 可能是 string 或 object 形式，直接 `cmd: "{modules.X.Y}"` 会把 dict 当字符串运行，且丢失 `timeout_seconds` 覆盖。helper 返回的 `cmd` 已经是 `timeout N bash -c '<cmd>'` 形式，可直接当 shell 命令调。

```bash
# 拿 cmd + timeout:
RESOLVED=$(python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --resolve-module-test <module> <test_key>)
CMD=$(echo "$RESOLVED" | python3 -c "import json,sys; print(json.load(sys.stdin)['cmd'])")
TIMEOUT=$(echo "$RESOLVED" | python3 -c "import json,sys; print(json.load(sys.stdin)['timeout_seconds'])")
```

```yaml
operations:
  # 1. 单元测试（来自 --resolve-module-test，cmd 已是 timeout 包装过的）
  - name: run_unit_tests
    type: test
    module: agent
    test_key: unit
    output_mode: summary_plus_failures

  # 2. Lint 检查（来自 --resolve-module-lint）
  - name: run_lint
    type: lint
    module: agent

  # 3. 端到端验证
  - name: e2e_create_vm
    type: api_call
    method: POST
    url: ...
    body: ...
    capture: instance_id

  - name: verify_vm_running
    type: shell
    cmd: "virsh list --all"
    expect_match: "<vm-name>.*running"

  # 4. 日志搜索
  - name: search_logs
    type: log_filter
    service: <role>
    patterns: ["<成功关键字>", "PANIC", "FATAL"] |
  # 5. Git diff 状态校验
  - name: verify_clean_diff
    type: git_diff_check
    forbid_markers: ["DIAG:"]
```

**❌ 删除的旧示例**（v3.0 之前）：
- `restart_backend`（type: shell + `cmd: "{resolved_actions.dev-local.backend.backend-1.start}"`）
- `rebuild_stack`（type: shell + `script: "{environments.<env>.prepare_env.script}"`）

→ 全部改由编排器调 `runner invoke-hook` 触发（hooks 协议）。

### 5.1.4.1 Deployment 工具调用约定（v3.0 新增）

service 启停（backend / frontend / agent start|stop|restart|logs|tail|health_check）以及 environment-level prepare_env / clean_env，**统一由编排器 LLM 调用** `pg-invoke-hook.py invoke-hook` 触发：

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --session <C> --env <ENV> --role <ROLE> --instance <INSTANCE> --action <ACTION> \
  [--stage <ST>] [--tail-lines <N>] [--skill pg-fix-issue]
```

> v4 协议：`--change` 改为 `--session`（canonical），`--skill` 硬缺省 `ad-hoc`，SKILL 调用必须显式标注 `--skill pg-fix-issue`。

| 标志 | 必填 | 说明 |
|------|------|------|
| `--session` | ✅ | session 名（v4）。pg-fix-issue 调用 = `fix-<YYYY-MM-DD>-<slug>`。`--change` 保留 1 版本作为 deprecated alias |
| `--env` | ✅ | 必须在 project.yaml `environments` 列表中 |
| `--role` | ⚠️ | backend / frontend / agent。**仅 `--action start\|stop\|restart\|logs\|tail\|health_check` 必填**；`prepare_env\|clean_env` 时忽略 |
| `--instance` | ⚠️ | 必须在 `environments.<env>.roles.<role>.instances[]` 中。**仅 per-role action 必填**；`prepare_env\|clean_env` 时忽略 |
| `--action` | ✅ | `start` / `stop` / `restart` / `logs` / `tail` / `health_check`（per-role） 或 `prepare_env` / `clean_env`（environment-level） |
| `--stage` | ❌ | 默认 `manual`；用于 spec.stage 标记 |
| `--tail-lines` | ❌ | 仅 `--action logs\|tail` 生效 |
| `--log-dir` | ❌ | 显式覆盖日志目录（agent 调试用，透传 `PG_HOOK_LOG_DIR`） |
| `--timeout-override` | ❌ | 覆盖 `timeout_seconds`（ad-hoc 调试用，CLI 显式传时输出 WARN） |
| `--no-wait-for-bg` | ❌ | start action 的 fire-and-forget 开关（hook `pg_start_bg` setsid detach 后立即返回）；stop/logs/tail 忽略 |
| `--wait-for-completion` | ❌ | 强制等 hook 跑完（覆盖 start 默认）。调试时偶尔有用 |
| `--skill` / `--caller` | ❌ | **硬缺省 `ad-hoc`**。SKILL 调用必须显式标注（pg-fix-issue → `--skill pg-fix-issue`）。注入为 `PG_RUN_CALLER` 环境变量 |

**`--timeout` / `--host` / `--port` 均不是 CLI flag**——LLM 不传，由 runner 从 project.yaml 反查 spec，runner 内部 `subprocess.run(timeout=...)` 强制执行。

**`next_call_timeout_seconds` 处理**：runner 在 `invoke-hook` 调用时返回的 `__CONFIG__` 段包含 `action_metadata[role][action].timeout_seconds`，LLM 把它当作下一次 bash tool 的 timeout 上限；不传会让 bash tool 120s 默认超时提前杀死长时间运行的 start/stop 脚本（典型如 backend start = 300s）。

**pg-fix-issue Phase 5 触发时机**（下表中的 `backend` / `backend-1` / `agent` 等为**常见角色示例**，实际按 `project.yaml` 的 `environments.<env>.roles[*]` 替换）：

| 触发时机 | 编排器动作 |
|---------|----------|
| Phase 4 修复前用户选"是 prepare" | 调 `invoke-hook --session <C> --env <ENV> --action prepare_env --skill pg-fix-issue` |
| 修复后需要某 role 重启验证 | 调 `invoke-hook --session <C> --env <ENV> --role <role> --instance <instance-name> --action start --skill pg-fix-issue` |
| 修复后需要某 role 停止收尾 | 调 `invoke-hook --session <C> --env <ENV> --role <role> --instance <instance-name> --action stop --skill pg-fix-issue` |
| 看某 role 的日志 | 调 `invoke-hook --session <C> --env <ENV> --role <role> --instance <instance-name> --action logs --tail-lines 100 --skill pg-fix-issue` |
| 验证成功后用户选"是 clean" | 调 `invoke-hook --session <C> --env <ENV> --action clean_env --skill pg-fix-issue` |

**精细化 vs 整栈的选择规则**（v3.0 通过 invoke-hook 表达；`<role>` / `<instance-name>` 按 SSOT 替换）：

| 场景 | 推荐 invoke-hook 调用 |
|------|---------------------|
| bug 在某个 role，验证只涉及该 role | `start <role>/<instance-name>`（fine-grained） |
| bug 涉及多个 role 协同 | `start <role1>/<instance1>` + `start <role2>/<instance2>`（按依赖顺序） |
| bug 涉及数据库 schema，需要 prepare_env 重置 db | `prepare_env`（Phase 3 用户选"是"后触发） |
| Phase 1 复现前环境状态不可信 | `prepare_env`（按 3.3 用户选择） |

**收益**：滚动修复时**只重启相关 role**，不打断其它 role → 大幅缩短 iteration cycle。

### 5.1.4.2 修复上线验证必做序列（v3.1 新增）

**核心原则**：单元测试通过 ≠ 修复成功。编排器必须确保修复代码已**编译并部署到运行中的服务**，才能宣布修复成功。

**必做序列**（Phase 5 中必须按以下顺序执行，不可跳过任意一步）：

```
Step 1: invoke-hook --action start  → 编译并部署修复代码到目标服务
Step 2: api_call 或 log_filter  → 验证服务正在运行且响应正确
Step 3: 验证确认操作的 operation（如 git_diff_check） → 确认 DIAG 日志已清理
```

**如果任何一步失败，立即进入 Phase 6，不继续后续步骤。**

**invoke_then_verify 完整操作样例**（`<...>` 占位符必须按 §5.1.4.3 替换为当前项目实际值）：

```yaml
operations:
  # Step 1: 编译部署（由编排器通过 invoke-hook 触发，不在 operations 列表内）
  # 编排器在 Phase 5 开始时调：
  #   python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  #     --session <C> --env <ENV> --role <role> --instance <instance-name> \
  #     --action start --skill pg-fix-issue

  # Step 2a: 确认服务已启动并运行修复代码（log_filter 验证 binary 版本）
  - name: verify_running_version
    type: log_filter
    service: <role>
    patterns: ["<fixed-symbol-or-class-name>"]   # 本次修复引入的独有可识别符号
    expect_found: true

  # Step 2b: 端到端 API 验证（调用真实 running 服务）
  - name: e2e_api_verify
    type: api_call
    method: <HTTP-method>
    url: http://<service-host>:<port><api-path>
    headers:
      Authorization: "<auth-header-template>"
    expect_field: <response-field>
    expect_value: <expected-value>

  # Step 3: 清理检查
  - name: verify_clean_diff
    type: git_diff_check
    forbid_markers: ["DIAG:"]

  # Step 4: 单元测试（可选，在部署验证通过后执行）
  - name: run_unit_tests
    type: test
    module: <module-id>
    test_key: unit
    output_mode: summary_plus_failures
```

**⚠️ 强约束**：编排器**不得**以"单测已通过"为由跳过 invoke-hook 和 api_call 验证。`invoke-hook --action start` 必须在所有 api_call operations 之前执行，确保被测服务运行的是当前修复代码。

### 5.1.4.3 占位符替换规约（v3.1 新增）

**核心原则**：本 SKILL 是跨语言、跨项目的通用模板。上面示例中所有 `<...>` 占位符必须根据**当前项目**的 `.pg/project.yaml` 实际定义替换，**严禁**硬编码示例中的具体值（如特定端口、特定类名、特定 module ID）。

| 占位符 | 含义 | SSOT 查找路径（按优先级） |
|--------|------|------------------------|
| `<role>` | 服务的角色名（例：backend / frontend / agent / api / web / worker） | `project.yaml` → `environments.<env>.roles[*].name` |
| `<instance-name>` | 该 role 下的实例名 | `environments.<env>.roles[<role>].instances[*].name` |
| `<service-host>` | 服务运行主机 | `environments.<env>.roles[<role>].instances[*].host` |
| `<port>` | 服务端口 | `environments.<env>.roles[<role>].instances[*].port` |
| `<module-id>` | 模块标识（用于 `modules.<m>.test.<key>` 解析） | `project.yaml` → 顶层 `modules[*]` 的 key |
| `<api-path>` | 实际 API HTTP path | 由当前项目的 API 规范决定（路径格式因项目而异，本 SKILL 不假设特定前缀） |
| `<HTTP-method>` | HTTP 方法（GET / POST / PUT / DELETE） | 由 API 端点定义决定 |
| `<auth-header-template>` | 认证头模板 | 按项目认证机制调整（Bearer token / Cookie / API Key / mTLS 等） |
| `<response-field>` | 期望匹配的响应字段名（按当前项目的 `ApiResponse<T>` 包装结构） | 由当前项目的统一响应体规范决定（不要假设 `code` / `data` / `message` 等具体字段名） |
| `<expected-value>` | 该字段的期望值 | 由当前 bug 修复目标决定 |
| `<fixed-symbol-or-class-name>` | 本次修复引入的**独有可识别符号**——服务启动日志或标准输出中可以匹配到 | 取值策略见下方"如何选定 `<fixed-symbol>`" |

**如何选定 `<fixed-symbol-or-class-name>`**：

- 优先级 1：新引入的方法名（含全限定类名或 method descriptor）—— 例：`com.example.MyController.getCurrentUserId`
- 优先级 2：新引入的字段名或常量键名
- 优先级 3：本次修复加入的独有日志前缀（DIAG 风格或业务日志），如 `[FIX-2026-07-05-me-user-not-found]`
- 优先级 4：本次修复 commit message 中的某个唯一短串

> **❌ 反模式**：在 SKILL 示例中硬编码任何项目特定的 URL（如 `localhost:9080/api/...`）、类名、模块名。这些是**教学线索**而非直接复用的字面量。

### 5.1.5 test_key 选择

`stages[*].test_key` 决定 `modules.<m>.test.<test_key>` 用哪个 key：

| bug 类型 | 推荐 test_key | 理由 |
|---------|--------------|------|
| 单函数逻辑错误 | `unit` | 快速反馈 |
| 多模块协作错误 | `integration` | 验证跨模块契约 |
| 端到端流（创建 VM 全流程） | `e2e` | 验证用户视角 |
| mock 环境模拟 | `mock_integration` | 不依赖真实外部服务 |

**编排器根据 affected_modules 数量自动推断**：1 个 module → unit；2-3 个 → integration；≥4 个 → e2e。**用户可在 Phase 3 覆盖**。

### 5.1.6 派遣 prompt 模板

```
Task 工具调用:
  - description: "执行验证流水线"
  - prompt: |
      执行以下验证流水线并返回结构化 JSON。

      operations:
      [YAML 格式的 operations 列表]

      __CONFIG__:
      modules:
        <module-id-1>:
          build: ...
          lint: ...
          test:
            unit: { cmd: "timeout N bash -c '...'", timeout_seconds: N }
            integration: ...
        <module-id-2>: ...
        # v3.0: 不再包含 tracks / pipeline / resolved_actions
        # service 启停由编排器调 invoke-hook, 不进 __CONFIG__

       严格按 prompt 执行，失败立即停止并报告。返回**只包含 JSON**，不要其他文字。
   - subagent_type: "pg-fix-issue/executor"
```

> ⚠️ **executor 伪造风险**：executor 在同一 conversation session 中被第二次派遣时，能访问第一次调用的历史记录。这可能导致第二次调用返回**形式正确但数据伪造**的结果（如凭空编造实例 ID 和运行状态）。
>
> **防护措施**：
> 1. 在设计好所有修复后，将第二次重试的 executor 派遣放到**新 message** 中，减少 executor 直接复制历史输出的可能
> 2. 在 Phase 5b 阶段编排器**必须亲自交叉验证 1-2 条关键 operation**（`virsh list`、`curl API` 查实例状态等）
> 3. 如果 executor JSON 的 `evidence` 字段缺失或看起来异常，直接判定伪造并进入 Phase 6

### 5a.5 executor 失败处理

**重要：executor 失败不计入滚动修复次数**（编排器的 `fix_issue.max_iteration_count` 次 retry 专用于新根因）。

executor 内部已处理：
- 端口占用 → 自决重试 1 次
- 旧进程残留 → 自决重试 1 次

executor 报告的失败（如编译错误、测试不通过、verify 失败）：
- 编排器读 JSON 详情
- 判断是"机械问题"（executor 已自决）vs"暴露新 bug"（需修复）
- "暴露新 bug"→ 编排器修复后**重跑 executor**（不计入 retry 计数）

---

## Phase 5b: 编排器审核 executor 返回的 JSON + 成功标准验证

executor 返回的 JSON 已经是结构化摘要，编排器**直接读 JSON 即可**。

> ⚠️ **executor 伪造防护**：executor 可能在同 session 重复调用时返回伪造结果（尤其在第二次调用时能看到第一次的真实输出并模仿）。编排器必须对 **涉及真实系统状态的 operation**（shell、api_call、log_filter）进行至少一次交叉验证，而不能直接信任 executor 的 JSON。交叉验证策略见下方。

### 5b.0 伪造检测（必须执行，对端到端验证）

编排器**必须**对 executor 返回的 JSON 做以下快速真实性检验（**特别在第二次重试时，executor 可能伪造结果**）：

```
针对每条涉及系统状态（非编译/非 git diff）的 operation：
  - api_call → 检查 evidence.response_first_line 是否包含真实 API 响应结构
  - shell virsh list → 证据必须有 stdout_tail，不能只写 "matched"
  - log_filter → 证据必须有 raw_matches 原始匹配行（含时间戳）

关键检验：当 executor JSON 显示全部通过时，编排器必须自己执行 1 条关键验证
命令（例如 virsh list 或 curl 查 instance 状态），验证 executor 没有伪造结果。
如果编排器自己执行的命令结果与 executor JSON 矛盾，则判定 executor 伪造，
进入 Phase 6 失败处理。
```

### 5b.1 成功标准逐项验证

**强制步骤**：编排器必须**逐项检查 Phase 2 success_criteria 是否满足**。

```python
# 伪代码
for sc in phase2_output.success_criteria:
    executor_result = run_executor_op(sc.verify_method, sc.verify_args)
    if executor_result.meets_criterion:
        sc.status = "PASS"
    else:
        sc.status = "FAIL"
        sc.actual = executor_result.actual_value  # 实际值
        sc.expected = sc.verify_args.expect_value  # 期望值

# 汇总
all_pass = all(sc.status == "PASS" for sc in phase2_output.success_criteria)
success_rate = f"{passed_count}/{total_count}"

if not all_pass:
    # 进入 Phase 6 失败处理
    escalate_to_phase6()
```

**判断矩阵**：

| success_criteria 通过 | failure_criteria 触发 | `fix_issue.partial_success_threshold` 检查 | 判定 |
|---------------------|----------------------|------------------------------------------|------|
| 全部通过 | 全部未触发 | n/a | ✅ **修复成功** |
| 部分通过，**通过率 ≥ threshold** | 全部未触发 | 跳过（视为部分成功） | ⚠️ **部分成功**（不通过项需重新诊断） |
| 部分通过，**通过率 < threshold** | 全部未触发 | 触发（视为修复失败） | ❌ **修复失败** |
| 全部通过 | 任一触发 | n/a | ❌ **修复失败**（failure 优先） |
| 部分通过 | 任一触发 | n/a | ❌ **修复失败** |

**反例标准**（failure_criteria）优先级高于成功标准 — 出现任一反例即判定失败，**不**触发 partial_success 阈值。

**`partial_success_threshold` 计算**：

```python
passed = sum(1 for sc in success_criteria if sc.status == "PASS")
total = len(success_criteria)
pass_rate = passed / total if total > 0 else 0

if pass_rate < fix_issue.partial_success_threshold:
    # 即便无 failure_criteria 触发，也判定修复失败 → Phase 6
    escalate_to_phase6(reason=f"通过率 {pass_rate:.0%} < 阈值 {threshold:.0%}")
```

**例外**：`fix_issue.partial_success_threshold == 0` 时禁用此检查（允许任何部分成功）。

### 5b.2 通用审核清单

- [ ] 修复未引入与项目规范不符的模式
- [ ] executor 报告的所有 operation 都执行了
- [ ] 测试失败的 actual/expected 对比合理（mode2 输出）
- [ ] verify 校验通过（如果配置了）
- [ ] 端到端验证结果与预期一致
- [ ] 日志搜索无 PANIC/FATAL
- [ ] git diff 干净（无 DIAG: 残留）
- [ ] **success_criteria 全部满足**
- [ ] **failure_criteria 全部未触发**

### 5b.3 命令来源校验

- 列出所有 `type: shell` / `type: test` / `type: lint` / `type: api_call` operations 里的命令
- 核对：每条命令必须能追溯到 `modules.<m>.{build,lint,test.<key>}` 字段
- service 启停命令不进入此清单（已由编排器通过 invoke-hook 触发，不在 operations 范围）
- ❌ 违规：手写 `cp ... /usr/local/bin/...`、`go build` 不通过脚本等

### 5b.4 失败详情查看

**默认 output_mode 是 `summary_plus_failures`（mode2）**：
- 失败时返回 actual/expected 对比、文件:行号、diff_summary
- **编排器直接读 diff_summary 决策下一步**

**何时切换到 `full_output`（mode3）**：
- mode2 信息不足需要看完整堆栈
- 测试本身是诊断探针（含 DIAG 日志）
- 编排器明确要求完整输出

**何时用 `summary_only`（mode1）**：
- 健康检查型测试（如 lint、覆盖率、冒烟）
- 只需要 pass/fail 计数

---

## Phase 5c: 架构验证

对照检查点：
- [ ] 修复是否遵循项目 API scope 规范
- [ ] 修复是否使用与已有相似功能一致的 API/组件模式
- [ ] 修复是否引入了新的安全隐患
- [ ] 修复是否破坏了协议语义（WS / gRPC / HTTP）
- [ ] 修复是否与上下游契约一致

---

## Phase 5d: 诊断产物清理（强制）

- [ ] 撤掉所有 `DIAG:` 临时日志
- [ ] `git diff --stat` 只显示目标文件变更
- [ ] `git_diff_check` operation 通过
- [ ] 临时脚本/复现脚本已清理

---

## Phase 6: 失败处理 / 滚动修复

### 6.1 滚动修复（**iteration 上限 = `fix_issue.max_iteration_count`**）

**关键约束**：`iteration_count` 的上限**绝不写死**，**必须从 `config.fix_issue.max_iteration_count` 读取**（缺省 5）。

每次验证发现"原根因已修但暴露新问题" → **滚动修复**
- 编排器继续修（不派遣 subagent）
- 修完**重跑 executor 验证**
- **计入 iteration_count**

```text
iteration_count = 0
loop:
  跑 executor 验证
  逐项检查 success_criteria（含 fix_issue.partial_success_threshold 检查）
  if 全部 success_criteria 通过 AND 无 failure_criteria 触发 AND pass_rate >= threshold:
    break  # 修复成功
  else:
    # 重新诊断（基于 success_criteria 实际值）
    读 executor 失败详情
    比对: success_criteria[failed].expected vs actual
    推断: 原根因？新问题？哪条 criteria 未满足？
    
    if 是新问题:
      修复（编排器 Edit）
      清理 DIAG
      iteration_count += 1
      if iteration_count > fix_issue.max_iteration_count:
        ESCALATE_WITH_MENU  # 见 6.2
      continue
    else:
      ESCALATE_WITH_MENU
```

### 6.2 失败类型分类

| 失败类型 | 处理 | 计入 retry |
|---------|------|----------|
| 编译错误（新引入） | 编排器修复 | ✅ |
| 测试失败（actual ≠ expected） | 编排器判断改代码还是改测试 | ✅ |
| verify 失败（运行版本不对） | 检查上一次 `runner invoke-hook --action start` 是否成功（log_path 在 `.pg/fix-issue/<fix-change>/<env>-logs/role.*.start@*.log`） | ✅ |
| 端到端 API 失败 | 看 response body | ✅ |
| 日志 PANIC | 看 panic stack | ✅ |
| **success_criteria 未满足** | **重新诊断**（按 criteria 实际值推断根因） | ✅ |
| **failure_criteria 触发** | **重新诊断**（说明修复方向错误） | ✅ |
| executor 机械失败 | executor 自决 | ❌ |
| 端口占用 | executor 自决 | ❌ |
| 环境问题（libvirt 不可用等） | 记 KnownIssues | ❌ |

### 6.3 编排器需要用户补充信息

如果修复需要用户决策（如架构级变更），用 `question` 工具问用户：
- 补充信息**不改变诊断方向** → 续接，不计 retry
- 补充信息**改变诊断方向** → 计入 retry

### 6.4 ESCALATE 条件

- `iteration_count > fix_issue.max_iteration_count` 后仍未修好
- 修复需要架构级变更（用户决策）
- 修复范围超出原问题 scope

### 6.5 ESCALATE_WITH_MENU（**3 选项 menu**）

当触发 ESCALATE 时，编排器**不直接放弃**，而是向用户展示 **3 选项 menu**：

```
question 工具调用:
  questions: [{
    question: "已迭代 {iteration_count} 次仍未能修复。\n
               请选择下一步：",
    header: "ESCALATE 选择",
    options: [
      {
        label: "再给一次机会 (推荐)",
        description: "增加 max_iteration_count 到 {iteration_count + 2}, 重做一次 Phase 1-6 循环"
      },
      {
        label: "切人工修复",
        description: "保留全部诊断产物（DIAG 日志 + 调用链路分析 + phase2_output + executor JSON 历史 + git diff）, 输出完整 report, 停止自动修复"
      },
      {
        label: "缩范围重试",
        description: "降低 success_criteria 中非核心条目（如浏览器 E2E）, 只保留核心 unit/integration 验证, 再试一次"
      }
    ]
  }]
```

**若 `fix_issue.allow_manual_verification == true`，额外提供第 4 选项**：

```
  options: [
    ...(上 3 选项)...,
    {
      label: "手动验证后回报",
      description: "用户手动执行 verification (curl / virsh / 浏览器), 回报 pass/fail 后决定下一步. 适用于 executor 全失败但 bug 已肉眼验证修好的场景."
    }
  ]
```

**所有场景下，末尾增加第 5 选项**（用户实测 fail 回退入口）：

```
  options: [
    ...(上 N 选项)...,
    {
      label: "用户实测仍失败",
      description: "主 agent 已报告修复成功但用户实测 bug 仍在。回到 Phase 1 重新诊断，侧重检查代码是否已部署到运行中的服务。iteration_count += 1。"
    }
  ]
```

**ESCALATE 输出 report 包含的产物**（来自 `fix_issue.escalation_artifacts`）：

```yaml
escalation_report:
  artifacts:
    diag_logs: "<git diff 中 DIAG: 行原文>"
    call_chain_analysis: "<Phase 1 完整输出>"
    phase2_output: "<reproduction_steps + success_criteria + failure_criteria>"
    executor_json_history: "[每次 executor 调用的完整 JSON]"
    git_diff_state: "<git diff --stat 输出>"
  
  iteration_summary:
    total_iterations: <N>
    each_iteration_outcome: |
      iteration 1: SC-1 FAIL (expected=RUNNING, actual=PENDING), FC-1 NOT triggered
      iteration 2: SC-1 PASS, SC-2 FAIL (expected=virsh running, actual=shutoff)
      ...
  
  unfixed_root_causes:
    - "<未解决的候选根因 1>"
    - "<未解决的候选根因 2>"
```

**选项行为映射**：

| 选项 | iteration_count | max_iteration_count | 其他动作 |
|------|----------------|--------------------|----|
| 再给一次机会 | 不重置 | 增加到 `N + 2` | 继续 Phase 1 |
| 切人工修复 | 保留 | 不变 | 输出 report，停止 |
| 缩范围重试 | 不重置 | 不变 | 编辑 success_criteria（保留核心），继续 |
| 手动验证后回报 | 不重置 | 不变 | 等待用户回报 |
| 用户实测仍失败 | +1 | 不变 | 回到 Phase 1 重新诊断 |

---

### 6.6 用户实测 fail 分支（re-entry protocol）

**触发条件**：主 agent 已完成 Phase 5b 并报告"修复成功"，但用户回复"bug 仍存在"或"修复无效"。

**自动处理**（编排器在收到用户反馈后立即执行，**不**经过 ESCALATE_MENU）：
1. `iteration_count += 1`
2. 如果 `iteration_count > fix_issue.max_iteration_count`，转入 ESCALATE_MENU（见 6.5）
3. 否则，**保留**所有诊断产物（调用链路分析、phase2_output、executor JSON 历史），直接回到 Phase 1
4. 重新诊断时，编排器必须优先检查以下"假阳性"场景：
   - 修复代码已编译但未部署到运行中的服务（最常见原因）
   - 部署的 binary 不是最新版本（invoke-hook 未执行或失败）
   - 测试环境与用户使用的环境不一致
   - 前端缓存导致旧代码仍在运行（硬刷新无效）

**假阳性防范**：在最终结论末尾附加以下提示：

> ⚠️ 如果以上结论与您的实际体验不符，请回复"bug 仍存在"，我将自动回到诊断阶段重新分析。

---

## 最终结论格式

编排器**必须**严格按以下模板输出：

```markdown
## 问题修复结论

### 问题
[issue_title]

### 修复状态
[修复成功 / 修复失败 / 需人工介入] — **必须**基于 success_criteria 全部通过 + failure_criteria 全部未触发

### 根因
[一句话说明根因]

### 修复摘要
[简要说明修复了什么，列出变更文件]

### 验证结果

#### 成功标准达成情况
| ID | 标准 | 期望值 | 实际值 | 状态 |
|----|------|--------|--------|------|
| SC-1 | ... | RUNNING | RUNNING | ✅ |
| SC-2 | ... | running | running | ✅ |
| SC-3 | ... | 192.168.x.x | 192.168.122.163 | ✅ |
| **达比例** | | | | **3/3 (100%)** |

#### 反例标准触发情况
| ID | 标准 | 触发 | 状态 |
|----|------|------|------|
| FC-1 | VM 停留 PENDING > 5min | 未触发 | ✅ |
| FC-2 | 日志含 'missing domain type attribute' | 未触发 | ✅ |

#### 功能验证对比表
| 场景 | 修复前（before） | 修复后（after） |
|------|-----------------|----------------|
| ... | ... | ... |

#### Executor 验证摘要
- test: ✅ (11/11 passed)
- lint: ✅ (0 warnings)
- e2e: ✅ (instance_id: ...)
- git_diff_check: ✅ (无 DIAG: 残留)
- invoke-hook 摘要: prepare_env ✅ / backend start ✅ / agent start ✅ / backend stop ✅
- git_diff_check: ✅

#### Code Review 检查清单
- [✅] 修复只改了目标文件（无连带改动）
- [✅] 遵循项目 API scope 规范
- [✅] 可量化指标已重新测量
- [✅] 静态检查通过
- [✅] executor 验证全部通过
- [✅] 诊断产物已清理
- [✅] **success_criteria 全部通过**
- [✅] **failure_criteria 全部未触发**

### 重试次数
[实际 iteration_count / fix_issue.max_iteration_count] (默认 5)

### 备注
[如有必要：Test X 失败与本次修复根因无关，已记入 KnownIssues]

> ⚠️ 如果以上结论与您的实际体验不符，请回复"bug 仍存在"，我将自动回到诊断阶段重新分析。
```
