# 品构 pg-skills — 品质，长在结构里

> 一个让 AI 写代码真正"可托付"的框架

---

## 一句话说清楚

**"SDD 还没学明白，SEA 又来了。"** —— 品构 pg-skills 就是那个"又来了"的新概念。

别急着划走。SDD（Specification-Driven Development，"先写方案再写代码"）方向是对的，但你会发现它有个共同尴尬：**方案写完了，代码写完了，还是没人敢合。** 因为 SDD 只解决了一半：

- **不关心真实环境长什么样** —— 方案写的是"假设中的环境"
- **说不清"验证通过"是在哪跑的** —— 你分不清是真环境还是 mock

所以品构不只是又套了一层新名字，而是把 SDD 缺的这两块补上，凑成完整的 **SEA**（Spec 方案 · Environment 环境 · Acceptance 验收）三根支柱，缺一不可。方案之外的每一行代码，都是在真实环境里验证通过、用 Gherkin 场景做过端到端验收的——**不是 mock 里跑一跑就交差。**

---

## 痛点：AI 写代码，谁来兜底？

```
┌───────────────┐     ┌───────────────┐
│   盲猜模式    │     │   品构模式     │
│               │     │               │
│ AI 读到代码   │     │ 先出图纸      │
│ 猜构建命令    │     │ 按图施工      │
│ 猜环境地址    │     │ 真实环境验证  │
│ 猜验收标准    │     │ 门控打分      │
│               │     │               │
│ 猜对了是运气  │     │ 系统比人      │
│ 猜错了不敢合  │     │ 先看见问题    │
└───────────────┘     └───────────────┘
```

**你每天的日常：** 跟 AI 聊需求 → AI 改代码 → 你不敢合 → 自己改一遍 → 改完忘了测 → 上线出 bug。

**品构之后的日常：** 说需求 → 点头确认图纸 → 去做别的事 → 回来验收合入。中间 1-8 小时完全无人值守。

---

## 核心方法论：SEA-Driven Development

品构的哲学是 **"品质，长在结构里"**。那"结构"具体指什么？三根支柱：

| 支柱 | 是什么 | 解决什么问题 |
|------|--------|-------------|
| **方案 (Spec)** | proposal / design / tasks | 做什么、怎么做、怎么验证 |
| **环境 (Environment)** | 真实环境探测快照 | 不再猜环境里有什么 |
| **验收 (Acceptance)** | 端到端 Gherkin 场景 | 不在 mock 里跑，在真实服务上验证 |

缺了环境，方案写的是"假设中的环境"；缺了真实验收，验证跑的是"理想中的验证"。三者合一，系统才能完整描述"项目长什么样 → 在哪验证 → 怎么算真通过"。

---

## 一次任务的全过程

### 步骤 1：自动驾驶（`/0-pg-auto-pilot`）

AI 自主规划与执行一次会话内可完成的任务，只受两条要求约束：

1. **实施计划必须包含"启动实例并验证编码结果是否达到预期"的步骤**——改完代码必须通过 hooks 协议重启实例（restart / stop+start），health_check 通过才算真正应用了新代码
2. **执行计划前，先让用户选定环境并确认准备方式**——需要准备就 prepare_env，环境已就绪则跳过

### 步骤 2：实现 + 验证（AI 自主，手段不限）

```
写实现 → 启动实例 → build/lint/test + health_check 验证 → 失败则修复重验 → 通过
```

验证手段不限：build/lint/test、health_check、运行时检查、回归等。

### 步骤 3：你验收 — 几分钟

AI 把改动内容、验证结果、hook 日志位置汇总给你确认。达不到预期就继续修复 → 重验，直到通过或上报。

### 步骤 4：合并 — 手动

合并到 default 分支改用手动 `git merge`，不再由任何 SKILL 自动执行。

---

## 工作流说明

```
当前唯一 SKILL：pg-auto-pilot（自动驾驶）
已移除：/1-pg-define / /2-pg-propose / /3-pg-build / /4-pg-regression / /5-pg-fix-issue / /6-pg-archive / pg-verify-and-merge
```

---

## 为什么敢合？

**1. 每步留痕可回放** — 每个操作都是不可变事件，随时复盘

**2. 验证在真实环境** — 启动真实服务、跑真实 API、查真实日志，不在 mock 里跑

**3. 门控打分 ≥80 才放行** — 5 维加权评分，没评审不合入，没验证不交付

**4. 回归自动闭环** — 回归发现问题 → 自动修复 → 提交 PR，不用你操心

---

## 接入一个新项目有多简单？

```bash
# 1. 拉入 pg-skills
git remote add pg-skills git@github.com:pin-gou/pg-skills.git
git subtree add --prefix=.pg/skills pg-skills v1.0.0 --squash

# 2. 初始化骨架
python3 .pg/skills/src/runtime/bin/pg init

# 3. 重启 opencode，运行 /0-pg-auto-pilot
# 4. 验证
python3 .pg/skills/src/runtime/bin/pg doctor
```

接入后，`pg init` 生成 `project.yaml`（你的项目"户口本"）+ hooks（环境生命周期脚本）骨架。你只需要核对一遍 AI 生成的产物是否正确。

---

## 企业级特性一览

| 特性 | 说明 |
|------|------|
| **事件溯源引擎** | 每一步不可变，跑挂了从断点恢复 |
| **TDRVG 五阶段** | 测试 → 开发 → 审查 → 验证 → 门控，逐层把关 |
| **5 维门控评分** | 正确性(35%) + 安全(25%) + 可维护(15%) + 性能(15%) + 规格覆盖(10%) |
| **真实环境验证** | 启动真实服务，跑 Gherkin Given/When/Then |
| **自动回归** | 测试失败自动分类修复，A/B 类自动修，C 类提单 |
| **无人值守** | 1-8 小时 pipeline 自动跑完，人不参与 |
| **SSOT 设计** | 单一可信源，`project.yaml` 是唯一配置来源 |
| **Hook 协议** | 标准化环境生命周期，注入 PG_* 环境变量，统一审计日志 |

---

## 适合谁用？

- **技术负责人** — 想引入 AI 辅助开发，但担心代码质量失控
- **全栈/独立开发者** — 一个人干所有活，需要 AI 帮你分担实现和验证
- **技术团队** — 多个开发者 + AI 协作，需要统一流程和规范
- **开源项目维护者** — 用 AI 自动处理 issue 和 PR，保持项目质量

---

## 现在开始

```bash
# 查看项目是否适合
git clone git@github.com:pin-gou/pg-skills.git
cd pg-skills
python3 src/runtime/bin/pg doctor

# 或者直接在你的项目里接入
# 详见 https://github.com/pin-gou/pg-skills
```

---

> **品构 pg-skills** — 「品质，长在结构里」
>
> 让 AI 写代码不再是"开盲盒"，而是"出图纸 → 施工 → 验收 → 合入"的工程闭环。
>
> 你只做两件事：说清需求，在关键节点点头。
> 中间那 1-8 小时，AI 替你扛。
>
> 首页：[https://pin-gou.github.io/pg-skills](https://pin-gou.github.io/pg-skills)
> 仓库：[https://github.com/pin-gou/pg-skills](https://github.com/pin-gou/pg-skills)