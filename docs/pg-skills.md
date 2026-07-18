# 品构 pg-skills：让 AI 写出可托付的代码

> **品构** = 品质 × 构造。一套让 AI 编码流程可治理、可审计、可复用的能力层。

---

## 一、问题的本质：AI 不是"写不快"，而是"管不住"

过去两年，团队里 AI 编码助手的渗透速度肉眼可见。但当一个工程团队真正把 AI 写进日常流水线，撞到的几乎不是同一面墙：

- **同一个项目里，五个 AI agent 对"什么是后端启动命令"有五种答案**——有的读 `package.json`，有的问 README，有的直接猜 `mvn spring-boot:run`；
- **改一行配置，AI 翻了八处才告诉你"已同步"**——因为它不知道哪份 YAML 才是真正的"权威来源"；
- **后台服务起来一半卡死，AI 说"已启动"**——但日志空空、端口不通、连 `kill` 都找不到 PID；
- **dev-local、staging、prod 三个环境，三套启动方式**——本地 setsid、远端 SSH、线上 systemd/k8s，AI 全部靠猜；
- **上一轮的构建命令、env 注入、错误分类，每个项目都要重新教 AI 一遍**——换个仓库、换个环境，又从零开始。

这些不是模型能力问题，是**流程治理问题**。模型越强，流程没跟上，产出越危险——它能跑得更快，也能错得更自信。在**分布式团队 + 多环境适配**的场景下，这种危险会被指数放大：本机跑通的命令，CI 一调就挂；staging 跑通的，prod 一调又挂——AI 不会告诉你它在哪一步猜错了。

**可托付的代码，靠结构本身托付，而不是靠人盯。** 这是「品构」名字里藏着的全部设计哲学——**品质**不是由模型单方面决定的，而是由**配置化的构造**来保障的。一套好的工程结构，能提升 AI 产出的品质下限，从而**降低对模型的要求**。`pg-skills` 解决的就是"构造"这一层：用结构托住品质，而不是靠模型本身变强。

---

## 二、pg-skills 是什么

`pg-skills` 是一个**跨项目、语言无关的 AI 编码能力层**，也是一个**自包含的完整工作流**：从 spec 生成、TDD 验证、到真实环境 E2E 验证，**它自己跑完一圈**，不需要外部 spec 框架或方法论框架来拼。

它以 `git subtree` 的方式嵌入到任何项目仓库的 `.pg/skills/` 目录下，配合 opencode（以及任何兼容 slash command 的 IDE / agent host）为团队提供：

- **8 个标准化 slash command**——把"定义 → 提案 → 构建 → 验证 → 回归 → 修复 → 归档"压成一条可追溯的流水线；
- **11 个 SKILL**——每个 SKILL 是一份契约化的工作流文档，约束 agent 在特定阶段必须做什么、不能做什么；
- **`pg-propose` 完整的 spec 生成链路**——proposal / specs / design / tasks 一站式产出，全部带 SSOT 校验；
- **`pg-build` 基于事件溯源的 pipeline 引擎**——Event Sourcing + Reducer 模式取代过程式状态机，编排过程可重放、可追溯、杜绝 LLM 自由发挥；
- **Scenario 真实环境 E2E 验证**——跨 backend / frontend / agent 的端到端跑在真实环境里，不靠 mock；
- **统一的 Hook 协议 + SSOT 规范**——environments / modules / roles 的生命周期、`project.yaml` 字段、错误分类、环境变量，全部从单一事实来源取值。

它不是另一个"AI 工具"，也不是 prompt 模板合集。**它是一条从 spec 到真实环境验证的端到端流水线**——能独立运行，也能被嵌入到更大的工程实践里。

> **如果你是从 OpenSpec / Superpowers 这类工具过来的**：pg-skills 与它们是**同赛道**，不是互补。它的 spec 生成、TDD 红 phase、subagent 评审、真实环境验证都是自包含的——不需要拿 OpenSpec 出 spec 再拿 Superpowers 跑 TDD、再拿 pg-skills 做环境治理。**一套 pg-skills 走完。**

---

## 三、使用 pg-skills 的日常体感

这是开发者用 pg-skills 跑一个 change（一次变更）时，时间线上大致发生的事：

```
1. /1-pg-define             约 20 分钟
   跟 AI 对齐"这次变更要做什么 / 不做什么 / 边界在哪"
   产出：清晰的范围定义（写入 .pg/context/）

2. /2-pg-propose            自动
   按定义生成完整的 spec 链路：
   · proposal.md      这次变更为什么做、做什么
   · design.md        技术方案
   · tasks.md         任务拆分（每个任务 2-5 分钟可完成粒度）
   · execution-manifest.yaml   本次执行的 SSOT（哪些 stage、每个 stage 在哪个环境）
   · scenario-<track>.yaml     真实环境 E2E 场景定义

3. /3-pg-build              1 - 8 小时不等（取决于变更规模）· 无人工干预
   编排器自动跑完：
   · 自动准备环境（按 manifest 的 stage→env 映射调 hook）
   · 自动写失败的测试（TDD 红 phase，确认真的失败后才进入实现）
   · 自动写实现代码 · 自动做代码 Review
   · 自动门控检查（gate + final-gate）
   · 真实场景验证（Scenario 真起 dev/staging 环境跑端到端）
   跑完直接产出一个可以验收的 change。

4. 你验收              几分钟
   · 大多数情况下（约 80%）一次就达到预期，直接合并；
   · 剩余约 20% 的情况，AI 的产出跟预期有偏差——
     你用 vibecoding（人机对话微调）做细微调整即可达到预期。
```

跑完这一轮后，产品上线不是终点。pg-skills 还有两条**长期迭代**的回路：

- **自动回归**：`/4-pg-regression <suite>` 选定一套测试套件，pg-skills 自动初始化环境跑回归，对失败的测试自动修复测试脚本（不创建 PR），对失败的生产代码自动修复并创建 PR 给你审；
- **Bug 修复**：`/5-pg-fix-issue` 拿到 bug 描述后，按 propose → build 同款流程产出修复 patch。

所以**开发者要做的两件事**其实非常少：

1. **定义每个 change 的边界**（用 `/1-pg-define` 想清楚要做什么 / 不做什么）；
2. **验收每个 change 的结果**（看 build 产物是否符合预期，不符合就 vibecoding 微调；之后审 /4-pg-regression 提的 PR 即可）。

中间那 1-8 小时的实现 + 验证过程，全部由 pg-skills 在事件溯源 pipeline 引擎上跑完——**人不参与**。

---

## 四、pg-skills 的三条主轴：SSOT、Hook 协议、事件溯源引擎

> 这一章会出现一些缩写，先给个索引方便回查：
> - **SSOT** = Single Source of Truth，"单一事实来源"。意思就是一个项目里某件事只有一处定义，别处都引用它。
> - **Hook** 在这里特指 pg-skills 的"环境生命周期回调"，类似 CI 里的 pre/post 阶段钩子，但作用对象是 environments / modules / roles。
> - **Manifest** = 变更执行清单，`execution-manifest.yaml`，记着"这次变更要跑哪些 stage、每个 stage 在哪个环境"。
> - **事件溯源（Event Sourcing）** = 一种架构模式：把每次状态变更记成一个事件，应用状态 = 所有事件从头回放的累积结果。pg-build 的 pipeline 引擎走的就是这条路。
> - **Reducer** = 纯函数：给定当前状态 + 一个事件，返回下一个状态。pg-build 用它替代传统 if/else 状态机。
> - **Agent host** = 能跑 slash command + skill 的工具，比如 opencode、Claude Code、Cursor。

`pg-skills` 不靠 prompt 模板撑场面，也不靠"希望 LLM 自觉守规矩"。它把整条流水线拆成三条**可机械执行的主轴**，每条都用工程手段把"AI 自由发挥"压到最低：

1. **SSOT** —— 配置和规范的单一事实来源，让 agent 不再自由心证；
2. **Hook 协议层** —— 把"启动 / 停止 / 跟环境互动"收口为统一调用；
3. **事件溯源 pipeline 引擎** —— pg-build 的编排器用 Event Sourcing + Reducer 跑 TDD 红 phase 和 Scenario 真实环境验证，每一步都有事件可重放，杜绝 LLM 在执行阶段的随意性。

下面逐条展开。

### 3.1 SSOT：让"对错"不再是 agent 自由心证

任何一个长期维护的项目，都会沉淀出几十条隐式规范：build 用哪条命令、test 怎么分层、环境变量怎么注入、错误怎么分类、日志写到哪里……这些东西在团队脑子里是清楚的，但 agent 看不到。

`pg-skills` 把这些规范全部拍平到**一个文件**——`.pg/project.yaml`——作为整个项目的**单一事实来源**（SSOT）。日常打交道的内容长这样：

```yaml
# .pg/project.yaml · 日常会改的几样东西
project:
  name: my-app
  language: go                # 告诉 agent 用 go toolchain

environments:
  dev-local:                  # 本地开发环境
    backend:                  # 后端角色
      instances:
        backend-1:            # 第 1 个实例
          transport: local    # 本地启动方式
          health_check: tcp:8080
  staging:                    # 预发环境
    backend:
      instances:
        backend-1:
          transport: ssh      # 远端 SSH 转发
          ssh_target: stg-bastion

modules:                      # 怎么编译 / 测 / 静态检查
  backend:
    build: "go build -o bin/ ./..."
    lint:  "golangci-lint run ./..."
    test:  "go test ./..."
```

你需要做的事就两件：**改这个文件 + 重启 agent**。agent 想要任何配置，**唯一合法路径**是查工具：

```bash
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py --key <dotted.path>
```

禁止凭记忆取上一次的值。禁止"差不多就行"。带来的好处是直白的：今天换个人换台机器换个 agent，行为一致；下个季度升级 pg-skills 到 v0.9.x，老项目里 agent 拿到的字段还是同一个 schema，不会悄悄漂移。

> **不关心实现的可以略过**：文件背后有 JSON Schema 校验、有错误分类常量、有 12 个 `PG_*` 环境变量的 SSOT 表。但作为用户，**你不需要知道它们长什么样**——只要你不动 SSOT 文件，pg-skills 升级时这些会自动跟着走。

### 3.2 Hook 协议层：把"启动一个服务"这件事标准化

AI 编码最难治的不是写代码，是**让代码跑起来并保持运行**。一个 Java Spring Boot 服务启动涉及：

- 加载正确的 `application-{profile}.yml`（用哪套配置）
- 把密钥 / 端口 / 中间件地址注入为环境变量
- setsid 脱离父 shell（不然 agent 会话一退，服务跟着死）
- 写 PID 文件、写日志路径、health check 等待端口
- 后续 stop / logs / tail 都能幂等定位到这次启动

`pg-skills` 把这套生命周期收口为**统一的 Hook 协议**——任何项目、任何语言，启动后台服务的姿势只有一种：

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
  --caller pg-agent \
  --session 2026-07-18-fix-bug-42 \
  --env dev-local \
  --role backend \
  --action start \
  --instance backend-1
```

参数含义用大白话解释：

| 参数 | 含义 |
|------|------|
| `--caller` | 谁在调（pg-agent / pg-build / pg-fix-issue 等） |
| `--session` | 本次任务的会话编号（一次任务复用同一个） |
| `--env` | 跑在哪个环境（dev-local / staging / prod） |
| `--role` | 哪个角色（backend / frontend / db 等） |
| `--instance` | 这个角色的哪个实例（backend-1 / backend-2） |
| `--action` | 想做什么（start / stop / logs / health-check） |

背后发生的事：

1. 读取 env / role / instance 的 spec；
2. 按 `.pg/project.yaml` 自动注入任务需要的会话编号、环境名、角色、实例等信息给 hook；
3. start 类用 fire-and-forget（spawn 完即返回，服务 detach 后不被杀），stop / logs 强制等完；
4. 统一负责 setsid + PID 写入 + 立即 crash 检测；
5. 日志落到 `.pg/agent/<session>/<env>/logs/`，按 caller 分目录，方便事后审计。

Java 的 `mvn spring-boot:run`、Go 的 `go run ./cmd/api`、Python 的 `uvicorn main:app`、Node 的 `pnpm dev`——填进 spec 就行，**agent 不需要为每个项目重新发明轮子**。而且每个 hook 调用都自动记录调用者 / 会话 / 环境 / 角色 / 实例 / 退出码 / 日志路径。三个月后回看，AI 在那次会话里跑了什么、为什么跑、跑的结果是什么，**全在日志里**。

### 3.3 为什么这套设计天然适合分布式 + 多环境

没有 `project.yaml`，AI 只能"猜"——而猜的能力不会随环境数线性增长，而是随环境数**指数衰减**。一个项目三种环境，AI 猜对启动命令的概率就从 100% 掉到约 1/3。换成十种环境、五种语言、三套编排平台（systemd / k8s / nomad），AI Coding 就彻底失效。

`pg-skills` 用一个**结构化的「环境 × 角色 × 实例」矩阵**治掉这个问题：

```yaml
# .pg/project.yaml · 节选
environments:
  dev-local:                                  # 本地环境
    roles.backend.instances.backend-1:
      transport: local                        # setsid + pg_start_bg
      health_check: tcp:8080
  staging:                                    # 预发环境
    roles.backend.instances.backend-1:
      transport: ssh                          # 走 SSH hook 自动转发
      ssh_target: stg-bastion
  prod:                                       # 生产环境
    roles.backend.instances.backend-1:
      transport: systemd                      # 走 systemctl，不套 pg_start_bg
      service_unit: backend.service
```

对 agent 而言，**永远只发同一条命令**：

```bash
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py \
  --caller pg-agent --session "$SESSION_ID" \
  --env dev-local --role backend \
  --action start --instance backend-1
```

唯一变量是 `--env`。背后按 transport 字段自动路由：

| `--env` | transport | 实际执行 |
|---|---|---|
| `dev-local` | local | 本地后台启动 + 端口健康检查 |
| `staging` | ssh | 通过跳板机转发到目标机器跑 hook |
| `prod` | systemd | 走 `systemctl`，不套后台启动（systemd 自身已隔离进程组） |
| `prod-cluster` | k8s | `kubectl rollout`，由对应 hook 实现 |

**新成员入职、新环境接入、新集群上线，成本都接近 0**——agent 不需要学习"这个环境有什么特殊的"，因为所有特殊性都沉淀在 `project.yaml` 这一个文件里。AI Coding 因此第一次能跨环境复用，而不是每开一个环境就重写一套 prompt。

这也是为什么"项目独立"和"语言无关"能做到——`pg-skills` 自己不写任何环境特定逻辑，所有环境特定性都通过 spec 注入。

### 3.4 propose 选环境、build 自动准备并互动

SSOT 解决了"配置在哪里"，Hook 协议解决了"怎么跟环境互动"，但这两者还缺一环：**谁决定某个任务该跑在哪个环境**。`pg-skills` 把这一环下沉到 slash command 协议层，让 AI 既不需要临场判断、也不会写死。

**第一步：`pg-propose` 为每个阶段选环境。**

LLM 按预定义规则（按阶段类型 / 按是否涉及 schema 改动等）自动决定环境组合，结果写入 manifest（变更执行清单）：

```bash
/2-pg-propose \
  --environment "test→dev-local,build→dev-local,verify→staging,gate→prod"
```

manifest 是这次变更的 SSOT——下游会强制校验环境名必须出现在 `project.yaml environments` 里，否则直接阻塞。这一步把"我猜你该跑 staging"变成了"manifest 里写的就是 staging"。

**第二步：`pg-build` 在编排循环里自动准备并互动环境。**

`pg-build` 读 manifest，遇到"该切环境了"就自动调 hook，而不是让 agent 凭记忆去敲命令：

```
loop bootstrap → next
  需要准备环境？  → 准备环境（走 hook）→ 记录结果 → 再查下一步
switch(action):
  "切环境"     → 准备环境 → 跑命令 → 记录结果
  "派任务"     → 派 sub-agent 跑当前阶段的活
  "推进"/"完成"/"失败" → 继续 / 收尾 / 终止
```

每一次环境切换都自动完成 4 件事：

1. **读 manifest 拿到「阶段 → 环境」映射**（SSOT，不靠 agent 记忆）；
2. **调用对应 hook**——本地 setsid、远端 SSH、prod 上 systemctl，全部走同一行命令；
3. **记录成功 / 日志路径 / 退出码 / 起始时间**，整个切换可审计；
4. **失败 → 终止流水线 · 把日志路径交给人**，编排器**不会**自作主张去"再试一次"或换环境。

这套协议带来一个具体的好处：**同一个 agent 代码不用改一行，换 `--env` 就跑不同环境**。CI 上想用 staging 验证？改 propose 的 `--environment` 即可；prod 上想用 systemd？hook 自动切换 transport，agent 完全无感。

这也是 pg-skills 与"prompt 模板拼装"型工具最大的差别——后者把环境选择留给 LLM 自由心证，前者把环境选择写进 manifest、把环境执行写进编排循环。**AI Coding 因此能正确适配不同环境，而不是每次都靠运气。**

### 3.5 pg-build 的事件溯源引擎：杜绝 LLM 执行时的随意性

SSOT 管"配置从哪里来"，Hook 协议管"怎么跟环境互动"，但还有最后一公里没解决：**AI 在执行阶段会不会临时起意、漏跑一步、跳过一个 review、伪造测试结果**？

`pg-build` 用**事件溯源（Event Sourcing）+ 纯函数 Reducer** 的架构治掉这个问题：

- **状态 = 事件回放**——pipeline 的每一步推进都是一个事件（dispatch / advance / done / failed / env_switch / env-action-result），所有事件落盘到 `.pg/changes/<change>/2-build/`；
- **Reducer 是纯函数**——给定当前状态 + 一个事件，机械算出下一个状态。**LLM 不参与"决定下一步是什么"，只参与"执行这一步的子任务"**；
- **状态可重放**——任何时刻都能从事件日志重放出当前进度，三个月后回看"那次 build 为什么失败"是确定性的；
- **失败处理是显式的**——编排器遇到失败 action（如 `workflow_failed`）必须终止，把 log_path 交给人，**不允许**自作主张去"再试一次"或换环境。

跑在这套引擎之上的，是两条强制执行链：

**① TDD 红 phase**：每个 track 进入 `dev` 之前，先派 sub-agent 写**失败的测试用例**（TDD 红 phase），确认测试**真的失败**后，才允许进入实现阶段。这一步在 prompt 层和引擎层双重约束——sub-agent 收到指令"本阶段只写测试代码，绝不创建或修改任何生产代码"，dispatch_file 也只授权读测试目录。

**② Scenario 真实环境 E2E**：对于跨 backend / frontend / agent 的端到端场景，`pg-build` 走 `scenario-prepare → scenario-execute → scenario-fix` 子 pipeline，**真的把服务起在 dev-local / staging 里跑**，不靠 mock、不靠 fixture。scenario-execute 报告失败 → 自动派 `scenario-fix` 子 pipeline 修复 → 重跑，直到通过或 escalate 给人。

这一整套合起来的意思是：**LLM 只在"按事件执行子任务"这一层有自由度，在"决定下一步是什么 / 跳过哪一步 / 改测试结果"这些层面是机械的**。这就是为什么 pg-skills 不需要 prompt 模板就能跑出可托付的代码——**不是 prompt 写得好，是引擎把它卡死了**。

---

## 五、它长什么样：一个最小接入示例

把 pg-skills 接入现有项目，**5 条命令、5 分钟**：

```bash
# 1. 把 pg-skills 作为 subtree 拉进来
git remote add pg-skills git@github.com:pin-gou/pg-skills.git
git fetch pg-skills
git subtree add --prefix=.pg/skills pg-skills master --squash

# 2. 初始化 .pg/ 骨架 + .opencode/ symlink
python3 .pg/skills/src/runtime/bin/pg init

# 3. 重启 opencode，加载 pg-* slash commands 和 skills

# 4. 在 opencode 输入提示词：加载并执行 pg-init-project skill
#    （自动扫描仓库结构，生成 .pg/context/repo-scan.md + 实打实的 .pg/project.yaml）

# 5. 校验
python3 .pg/skills/src/runtime/bin/pg doctor
```

完成之后，你的工作流从"凭感觉指挥 AI"变成这样：

```
/1-pg-define   →  探索 / 设计 / 定界（要做什么、约束是什么）
        ↓
/2-pg-propose  →  产出 proposal.md（变更设计 + tasks.md 任务拆分）
        ↓
/2.1-pg-propose-refine  →  按评审意见迭代
        ↓
/3-pg-build    →  按 tasks.md 一步步执行构建（每步都有 hook 审计）
        ↓
pg-verify-and-merge  →  验证 + 合并
```

每一步的产物都落在 `.pg/changes/<change-id>/` 下，带时间戳、带日志、带 proposal 历史。**这不是为了好看，是为了三个月后你或者新同事能秒级回放"那次改动为什么这么做"。**

> **跟"prompt 拼装型"工具的差别**：那些工具通常只给一套 slash command 让 AI 自觉执行；pg-skills 的差别是——每一步推进都有事件落盘，下一步是什么由 Reducer 机械算出，**LLM 没法跳过或临时改流程**。

---

## 六、谁应该关心它

- **技术团队 Leader**：希望把 AI 编码从"个人英雄"变成"团队纪律"。pg-skills 提供的就是纪律层。
- **架构师 / Platform Engineer**：需要让多个项目复用同一套 AI 治理，避免每个仓库重造轮子。
- **分布式团队 / 多环境项目**：dev / staging / prod 维护多套启动脚本的团队，pg-skills 把差异收口到 `project.yaml` 一处。
- **对代码质量 / 审计有要求的团队**：金融、政企、SaaS，AI 跑的命令必须有 audit trail，pg-skills 默认就给你。
- **已经在用 opencode / Claude Code / Cursor，但苦于"换个项目 / 换个环境就失效"** 的团队：pg-skills 的 subtree 嵌入模型正好治这个。

---

## 七、不适用的情况

也讲清楚边界，避免误用：

- **你只是想找一个"更聪明的 prompt"**：pg-skills 不是 prompt 库，那是 Cursor Rules / Claude Skills 的活；
- **你的项目结构还在剧烈变动、连 README 都没稳定**：那先固化项目结构再来谈治理；
- **你不接受"agent 必须通过 SSOT 工具查询、不准直接读 YAML"这种约束**：那 SSOT 的价值就立不住——pg-skills 的设计前提就是"agent 是不可信的执行者，必须给它定规矩"。
- **你只需要在本地一台机器跑，不存在多环境 / 分布式需求**：那 Hook 协议层的价值会打折扣，可以只先用 SSOT 部分。

---

## 八、写在最后

AI 编码这件事，到 2026 年已经不再是"能不能写"，而是"能不能管"。**模型会继续变强，但治理不能等模型变完美才开始。**

`pg-skills` 的定位很朴素：**给 AI 编码流程装上一套像传统软件工程那样的运行时治理层**——单一事实来源（SSOT）、环境生命周期钩子（Hook）、自动审计（Audit）、变更执行清单（Manifest）、事件溯源的 pipeline 引擎（杜绝 LLM 执行阶段自由发挥）。它不抢模型的风头，但**完整自包含**：从 spec 生成、TDD 红 phase、到 Scenario 真实环境 E2E 验证，一套 pg-skills 走完。

跑完之后，模型产出的代码第一次真正"可托付"——**可被 checkpoint / 可被回放 / 可被追责**。

这不是靠模型变聪明实现的——是品构的结构本身把品质下限托高了，让普通模型也能产出可托付的代码。

这是「品构」对每一行 AI 生成代码的硬契约，不是产品口号。

---

- 仓库地址：`github.com/pin-gou/pg-skills`
- 当前版本：v0.8.2
- 一句话接入：`git subtree add → pg init → pg-init-project`
- 一行切换环境：`pg-invoke-hook.py --env <dev-local|staging|prod> --role <r> --action <a> --instance <i>`
- propose 选环境 + build 自动 prep：`/2-pg-propose --environment "test→dev-local,verify→staging,gate→prod"`

欢迎来聊、欢迎挑刺、更欢迎贡献。