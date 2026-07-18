# `.pg/project.yaml` 编辑器 — 设计方案

> `.pg/skills/tools/project-editor/` —— 取代现有 `.pg/skills/tools/yaml-editor/` 的全新编辑器

## 1. 背景与目标

### 1.1 现有 `yaml-editor/` 的局限

现有 `.pg/skills/tools/yaml-editor/`（1195 行 Vue 源码 + dist 构建）虽已覆盖 6 个 Tab + ajv 校验 + Ctrl+S 保存，但仍存在以下门槛：

| 痛点 | 现状 | 影响 |
|---|---|---|
| **概念结构不清** | 6 个平铺 Tab 各自孤立，看不到 Stage→Track→Module 的归属关系 | 新人"改了 backend test 不知道它属于哪个 track" |
| **6 段缺失** | rules / build_rules / proposal_rules / verify_merge / flyway / git 完全没有 UI | 这些段只能手写 YAML |
| **字段偷偷消失** | EnvironmentFlow 等 canvas 仅编辑 `script`，吞掉 `args / timeout_seconds / wait_for_completion / parallel / hosts / description` | 数据正确性无保障 |
| **diff 缺失** | 保存是"开盲盒"，看不到改了哪里 | "怕改坏" 是项目 yaml 的核心恐惧 |
| **冷启动劝退** | 依赖 `python3 -m http.server 8000`，README 与 vite 配置端口不一致（3028 vs 3008） | 首次启动就卡住 |
| **YAML 仍是门槛** | `prompt()` 输入名称后仍是表单填写 + YAML 心智模型 | 对 PM/QA 不友好 |

### 1.2 目标

设计一个**全员可用**（PM/QA/新人/老手）的 `.pg/project.yaml` 编辑器，达成：

- **清晰看到** project.yaml 的概念结构（关系，不是段）
- **安全修改**（强校验 + diff 确认 + 直接覆写）
- **不被 YAML 语法吓退**（纯表单）

### 1.3 目标用户

| 用户 | 占比 | 关注点 |
|---|---|---|
| 老手（已会用 project.yaml） | 30% | 改起来不费手、diff 确认 |
| 熟悉项目但怕改错的开发者 | 40% | 字段解释、默认值、模板 |
| PM / QA | 20% | 完全不接触 YAML，能看到模块怎么跑测试 |
| 首次接触项目的新人 | 10% | 仪表板引导、字段级说明 |

---

## 2. 总体形态

### 2.1 4 个视图

```
┌─────────────────────────────────────────────────────────────────┐
│  📋 .pg/project.yaml 编辑器           ⓘ 仪表板   ✓ Pass        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [📊 仪表板]  [📋 表单]  [📐 画布]  [🔍 对比]                  │
│   (Onboarding) (默认主)   (辅助)    (保存前)                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

| 视图 | 角色 | 默认？ |
|---|---|---|
| 📊 **仪表板** | 项目全貌 + 健康度 + 最近变更 | ✅ 首次启动默认 |
| 📋 **表单** | 日常编辑主入口 | ✅ 常规默认 |
| 📐 **画布** | 看 Stage→Track→Module 关系 | 辅助 |
| 🔍 **对比** | 保存前逐字段 diff | 触发型（不常驻） |

### 2.2 主视图（表单）布局

```
┌─────────────────────────────────────────────────────────────────┐
│  📋 表单              ⚠ 未保存 3 处    Ctrl+S 保存    ↻ 重载   │
├──────────────────────┬──────────────────────────────────────────┤
│ 段导航 (可折叠)       │ 详情面板                                  │
│                      │                                          │
│ ▾ modules (6)        │ modules.backend                            │
│  ▸ backend           │ ┌──────────────────────────────────┐   │
│  ▸ agent             │ │ root: webvirt-backend             │   │
│  ▸ frontend          │ │ language: [java ▾]                │   │
│  ▸ agent-proto       │ │ timeout_seconds: [1800]           │   │
│  ▸ openapi-gen       │ │ review_level: [standard ▾]        │   │
│  ▸ env-scripts       │ │ description: [________________]   │   │
│                      │ │                                  │   │
│ ▸ environments (2)   │ │ build:                            │   │
│ ▸ tracks (5)         │ │   [cd webvirt-backend && mvn ...] │   │
│ ▸ stages (2)         │ │ ⓘ module.timeout_seconds 优先级:  │   │
│ ▸ fix_issue          │ │   command > module > 1800         │   │
│ ▸ regression (3)     │ │                                  │   │
│ ▸ rules              │ │ test:                             │   │
│ ▸ build_rules (5)    │ │   ▸ unit   [cd ... && mvn test]   │   │
│ ▸ proposal_rules (1) │ │   ▸ integ  [.pg/hooks/backend-...]│   │
│ ▸ verify_merge       │ │                                  │   │
│ ▸ flyway             │ │ [复制此 module]                    │   │
│ ▸ git                │ │ [从预设新增 ▾]                     │   │
│                      │                                          │
│                      │  ⓘ schema.description 渲染为悬浮提示      │
│                      │  ⚠ ajv 错误显示在字段下方红色提示        │
└──────────────────────┴──────────────────────────────────────────┘
```

### 2.3 画布（4 列垂直流）

```
┌─────────────────────────────────────────────────────────────────┐
│  📐 画布  ─── 关系图谱 (只读)                                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Stages (顺序)      Tracks           Modules        Environments │
│  ┌──────────┐                                                        │
│  │ prepare  │──→ env-scripts ──→ env-scripts                       │
│  │ -env     │                                                        │
│  └──────────┘                                                        │
│                                                                 │
│  ┌──────────┐                                                        │
│  │ dev      │──→ backend      ──→ backend                          │
│  │          │──→ agent        ──→ agent        ──→ dev-local       │
│  │          │──→ openapi-gen  ──→ openapi-gen      ┌─────────┐    │
│  │          │──→ frontend     ──→ frontend         │ backend │    │
│  └──────────┘                                       │ agent   │    │
│                                                     │ frontend│    │
│                                                     └─────────┘    │
│                                                                 │
│  ┌─ 联动 ─────────────────────────────────────────────────────┐   │
│  │ 表单中选中 modules.backend.test.unit                      │   │
│  │ → 画布高亮 backend 节点 + dev stage 节点                  │   │
│  │ → 蓝色虚线连接 backend → dev → unit                       │   │
│  └────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**关键特性**：
- 4 列垂直流：**Stages → Tracks → Modules**，旁路 **Environments**
- **双向联动**：表单选中 → 画布高亮；画布点击 → 跳到对应表单字段
- **只读**：所有修改在表单完成

### 2.4 对比（Diff）

触发：`Ctrl+S` 或点击保存按钮。

```
┌─────────────────────────────────────────────────────────────────┐
│  🔍 对比                                                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  即将修改 .pg/project.yaml                                       │
│                                                                 │
│  modules.backend:                                              │
│   - timeout_seconds: 1800                                       │
│   + timeout_seconds: 3600                                       │
│                                                                 │
│  + modules.newmodule:                                           │
│   + root: webvirt-newmodule                                     │
│   + language: typescript                                        │
│   + timeout_seconds: 1800                                       │
│                                                                 │
│  environments.dev-local.roles.backend.instances[0]:             │
│   - port: 9080                                                  │
│   + port: 9081                                                  │
│                                                                 │
│  ⚠ schema 校验: 通过 (0 errors)                                │
│                                                                 │
│                          [Cancel]      [Confirm Save]           │
└─────────────────────────────────────────────────────────────────┘
```

**关键特性**：
- 逐字段标注 `+ / - / ~`（修改）
- 保存前 ajv 校验，关卡
- 「Confirm Save」 是唯一落盘入口

---

## 3. 关键交互

### 3.1 保存

```
Ctrl+S
  ↓
┌──────────────────────┐
│ ajv 校验             │
│   ├─ 通过 → Diff 弹窗 │
│   └─ 失败 → 状态栏报错 │
└──────────────────────┘
  ↓ (通过)
Diff 弹窗
  ↓
[Confirm Save]
  ↓
直接覆写 .pg/project.yaml
  ↓
状态栏: ✓ 已保存
```

**无 git 拦截**：保存即落盘，靠 diff 模态让人"看清"。

### 3.2 新增 Module 模板

**两条路径**：

```
                              新增 module
                                  │
                ┌─────────────────┴─────────────────┐
                ▼                                   ▼
      [复制现有 module]                    [从预设新增 ▾]
                                              │
                                ┌─────────────┼─────────────┐
                                ▼             ▼             ▼
                            Java+Maven    Go+Make     TS+Vite+pnpm
                            (默认 build   (默认 build  (默认 build
                             mvn install)  make build)  pnpm build)
                                │             │             │
                                └─────────────┴─────────────┘
                                              ▼
                                     详情面板打开新条目
```

### 3.3 URL hash 跳转

```
http://localhost:3028/?module=backend#test.unit
                                │            │
                                │            └─ 滚动到 test.unit 字段
                                └─ 默认选中 modules.backend
```

支持的锚点：
- `?module=<id>` — 选中模块
- `?env=<id>` — 选中环境
- `?track=<id>` — 选中 track
- `?stage=<idx>` — 选中阶段
- `#<field-path>` — 滚动到字段（如 `test.unit`）

### 3.4 状态栏

```
底部状态栏固定显示：
  ⚠ 未保存 3 处          ← 表单 vs 原始文件差异计数
  ✓ schema 校验通过       ← ajv 结果
  ↻ 已加载 16:32         ← 上次 reload 时间
  Ctrl+S 保存            ← 快捷键提示
```

---

## 4. 段级 UI 覆盖范围

必须 100% 覆盖 `.pg/skills/src/runtime/spec/project.schema.json` 的所有顶层段和 definitions：

| 段 / 字段 | UI 状态 | 说明 |
|---|---|---|
| `schema` / `$schema` | 折叠在「顶级参数」面板 | 高级用户可见，默认隐藏 |
| `modules.<m>` | ✅ 表单 + 画布 | 详情面板完整编辑所有字段 |
| `modules.<m>.test.<key>` | ✅ 表单 | 动态增加 test key |
| `modules.<m>.build/lint` | ✅ 表单 | 命令编辑器（string / object 切换） |
| `environments.<e>` | ✅ 表单 + 画布旁路 | |
| `environments.<e>.roles.<r>` | ✅ 表单 | host / instances / actions |
| `environments.<e>.roles.<r>.instances[].*` | ✅ 表单 | name / host / port / libvirt_uri / description |
| `environments.<e>.roles.<r>.actions.<a>.*` | ✅ 表单（修复字段丢失 bug）| host / hosts / parallel / script / args / timeout / description |
| `environments.<e>.prepare_env / clean_env` | ✅ 表单 | 完整 action 字段 |
| `environments.<e>.actions.*` | ✅ 表单 | cross-role orchestration |
| `tracks.<t>` | ✅ 表单 | |
| `tracks.<t>.commands[]` | ✅ 表单 | 兼容 string 简写与 object 完整 |
| `stages[]` | ✅ 表单 + 画布 | |
| `stages[].on_conditions[]` | ✅ 表单（补缺失）| 自然语言规则数组 |
| `stages[].environment.selection_rules[]` | ✅ 表单 | |
| `fix_issue.*` | ✅ 表单 | 已有 |
| `regression.suite.*` | ✅ 表单 | 已有 |
| `rules.*` | ✅ 新增 | 自由对象结构 + JSON 编辑 |
| `build_rules[]` | ✅ 新增 | 数组，每项编辑 id/type/target_agent/position/template |
| `proposal_rules[]` | ✅ 新增 | 同上 |
| `verify_merge.skip_tests_if_no_conflict` | ✅ 新增 | 简单 boolean |
| `flyway.migration_path` | ✅ 新增 | 路径 string |
| `git.default_branch` | ✅ 新增 | 路径 string |

---

## 5. 技术选型

| 维度 | 选型 | 理由 |
|---|---|---|
| 框架 | **Vue 3 + TypeScript + Vite** | 沿用现有 yaml-editor 技术栈 |
| 状态管理 | **Pinia** | 同上 |
| Schema 校验 | **ajv 8 + ajv-formats** | 直接消费 `project.schema.json` |
| YAML 解析 | **yaml (eemeli/yaml)** | 保留源码 token 用于 round-trip |
| Graph 渲染 | **@vue-flow/core** | 现有依赖，可继续用 |
| **Diff 引擎** | 新增依赖：**微库自实现** 或 `jsondiffpatch` | 仅需字段级 diff，无需行级 |
| 持久化 | **File System Access API** + 下载回退 | 现有逻辑 |
| 启动 | **`pnpm dev` → `python3 -m http.server 8000`** | 现状，但 README 重写对齐 |

### 5.1 关键依赖新增

```json
{
  "dependencies": {
    "jsondiffpatch": "^0.6.0"
  }
}
```

或自实现一个字段级 diff（推荐，避免引入复杂依赖）：

```ts
function diffFields(before: object, after: object): DiffEntry[] {
  // 遍历 after 所有 key, 对比 before
  // 输出 + / - / ~ 三种类型
}
```

---

## 6. 关键设计决策（来自探索会话）

| # | 决策 | 替代方案 | 理由 |
|---|---|---|---|
| 1 | **全员用户**（PM/QA/新人） | 仅开发者 | 这是项目级 yaml，应当全员可维护 |
| 2 | **直接覆写 .pg/project.yaml** | 导出 PR / 下载 | 配合 diff 模态足够安全 |
| 3 | **完全重新设计**（取代 yaml-editor）| 在 yaml-editor 上修补 | 现有结构"乱"是基因问题，修补边际收益低 |
| 4 | **表单为主 + 画布为辅** | 概念地图为主 | 日常"改 module.test.unit"远比"看关系图"频繁 |
| 5 | **首次启动 = 仪表板** | 表单直接进入 | 新人需要看到项目全貌 |
| 6 | **4 列垂直流画布** | 双泳道 / 中心辐射 | 表达"pipeline 顺序"最自然 |
| 7 | **画布只读 + 双向联动** | 画布可编辑 | 表单是主交互，画布是导航 |
| 8 | **顶部 4 Tab（仪表板/表单/画布/对比）** | 抽屉 / 模态 | 平级视图，平等地位 |
| 9 | **保存 = Ctrl+S + Diff 模态** | auto-commit + diff | 不绕过编辑者眼睛，最简 |
| 10 | **新增 = 复制现有 + 语言预设** | 仅复制 / 仅预设 | 90/10 比例，最贴真实场景 |
| 11 | **完全不暴露 YAML 源码** | 双模式表单/yaml | 目标用户全员 → 屏蔽 YAML 概念 |
| 12 | **URL hash 跳转** | 无 | 可分享链接，降低"指给同事看"成本 |
| 13 | **未保存计数状态栏** | 简单 dirty flag | 看到 "3 处"比 "true/false" 心理负担小 |

---

## 7. 范围边界

### 7.1 In Scope

- ✅ 100% 覆盖 `project.schema.json` 顶层段
- ✅ 修复现有 EnvironmentFlow 等 canvas 的字段丢失 bug
- ✅ 4 个视图（仪表板/表单/画布/对比）
- ✅ 双向联动（画布 ↔ 表单）
- ✅ Diff 模态确认保存
- ✅ 模板化新增（复制 + 语言预设）
- ✅ URL hash 跳转
- ✅ 未保存计数状态栏
- ✅ Onboarding 仪表板
- ✅ README 与启动流程重写（端口、命令对齐）
- ✅ 取代 `yaml-editor/`

### 7.2 Out of Scope

- ❌ 直接编辑 `.pg/changes/<change>/environment.yaml` 等其他 yaml
- ❌ 多个 project.yaml 项目切换（单项目工具）
- ❌ YAML 源码预览模式（不暴露给用户）
- ❌ git 操作（commit / branch / revert）
- ❌ 服务端持久化（纯前端）
- ❌ 多用户协作（仅本地）
- ❌ CLI 调用（GUI only）

---

## 8. 目录结构

```
.pg/skills/tools/project-editor/
├── README.md                 # 启动说明
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.ts
│   ├── App.vue               # 4 Tab 路由
│   ├── views/
│   │   ├── Dashboard.vue     # 📊 仪表板
│   │   ├── FormView.vue      # 📋 表单 (默认)
│   │   ├── CanvasView.vue    # 📐 画布
│   │   └── DiffView.vue      # 🔍 对比 (Ctrl+S 触发)
│   ├── components/
│   │   ├── shared/
│   │   │   ├── TopBar.vue    # 视图切换 + 保存
│   │   │   ├── StatusBar.vue # 未保存计数 + 校验状态
│   │   │   ├── DiffModal.vue # Ctrl+S 弹窗
│   │   │   └── FieldTooltip.vue # ⓘ schema.description 提示
│   │   ├── sections/         # 段级表单组件
│   │   │   ├── ModulesSection.vue
│   │   │   ├── EnvironmentsSection.vue
│   │   │   ├── TracksSection.vue
│   │   │   ├── StagesSection.vue
│   │   │   ├── FixIssueSection.vue
│   │   │   ├── RegressionSection.vue
│   │   │   ├── RulesSection.vue        # 新增
│   │   │   ├── BuildRulesSection.vue   # 新增
│   │   │   ├── ProposalRulesSection.vue # 新增
│   │   │   ├── VerifyMergeSection.vue  # 新增
│   │   │   ├── FlywaySection.vue       # 新增
│   │   │   ├── GitSection.vue          # 新增
│   │   │   ├── TestStrategySection.vue # 新增
│   │   │   └── CodingStandardsSection.vue # 新增
│   │   └── fields/
│   │       ├── CommandField.vue        # string/object 切换
│   │       ├── ArgsField.vue           # 数组 + 模板变量
│   │       ├── EnumSelect.vue
│   │       ├── NumberField.vue
│   │       ├── StringField.vue
│   │       └── BooleanField.vue
│   ├── stores/
│   │   └── projectStore.ts   # Pinia: data + dirty + errors + diff
│   ├── schema/
│   │   └── loader.ts         # ajv 编译
│   ├── utils/
│   │   ├── yaml.ts           # round-trip
│   │   ├── diff.ts           # 字段级 diff (新增)
│   │   └── hash.ts           # URL hash 解析 (新增)
│   └── templates/            # 语言预设 (新增)
│       ├── java-maven.ts
│       ├── go-make.ts
│       ├── ts-vite.ts
│       ├── shell.ts
│       └── proto.ts
└── dist/                     # vite build 产物
```

---

## 9. 实施优先级（MVP 切分）

### Phase 1: MVP（核心闭环）

1. 项目脚手架（package.json / vite / tsconfig）
2. ajv + Pinia store + YAML round-trip
3. 📋 表单视图（modules / environments / tracks / stages / fix_issue / regression）
4. Ctrl+S → Diff 模态 → 覆写
5. 状态栏（未保存计数 + 校验状态）

**MVP 验收**：能完整编辑 `oc2-web-virt/.pg/project.yaml` 6 个核心段，diff 确认后保存，schema 校验通过。

### Phase 2: 补齐缺失段

6. rules / build_rules / proposal_rules 表单组件
7. verify_merge / flyway / git 表单组件
8. README 重写 + 启动流程对齐

**Phase 2 验收**：所有 schema 顶层段都有 UI，不再需要手写 YAML。

### Phase 3: 可视化与导航

9. 📊 仪表板视图（项目概况 + 健康度 + 最近变更）
10. 📐 画布视图（4 列垂直流 + vue-flow 集成）
11. 画布 ↔ 表单双向联动
12. URL hash 跳转

**Phase 3 验收**：新人打开项目 → 看仪表板 → 进画布看关系 → 点节点跳表单 → 改完看 diff 保存。

### Phase 4: 模板化与打磨

13. 5 个语言预设（java/go/ts/shell/proto）
14. 「复制现有 module」按钮
15. 字段级悬浮提示（schema.description）
16. 取代 yaml-editor（删除 .pg/skills/tools/yaml-editor/）

**Phase 4 验收**：删除 yaml-editor，新工具完全替代。

---

## 10. 验收标准

### 10.1 功能性

- [ ] 100% 顶层段都有 UI
- [ ] 修复字段丢失 bug：保存后再加载 = 字节级一致（除注释）
- [ ] ajv 校验：实时反馈，0 错误才允许保存
- [ ] Diff 模态：保存前必看
- [ ] 模板化新增：Java+Maven / Go+Make / TS+Vite+pnpm / Shell / Proto 至少 5 个预设

### 10.2 体验性

- [ ] 新人首次启动看到仪表板，30 秒内理解项目结构
- [ ] 改一个 module.test.unit 字段 < 5 次点击
- [ ] 保存流程 < 3 步：Ctrl+S → Diff → Confirm

### 10.3 工程性

- [ ] README 与 vite config 端口、命令一致
- [ ] 启动只需 `pnpm install && pnpm dev`，无 `python3 -m http.server` 依赖
- [ ] 删除 `.pg/skills/tools/yaml-editor/` 后无回归

---

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| **画布与表单双向联动状态同步** | 选中状态不一致 | 用 Pinia 单一 source of truth，selection state 全局 |
| **Diff 引擎对动态生成对象的渲染** | 多余 diff | 仅对比原始 raw 文件 vs 当前 data，不对比中间状态 |
| **模板预设过时** | 项目结构变化后预设失真 | 模板只在"新增"路径出现，不影响既有数据 |
| **YAML round-trip 丢失注释** | 编辑后注释被吞 | 用 `eemeli/yaml` 的 `keepSourceTokens: true`，逐 token 保留 |
| **大文件性能** | 676 行还 OK，未来万行 | 段级懒加载 + 虚拟滚动 |

---

## 12. 后续展望（不在本次范围）

- 支持 `.pg/changes/<change>/environment.yaml` 编辑
- 支持多个 `project.yaml` 切换（monorepo）
- YAML 高级模式（隐藏开关，老手可见）
- 协作模式（CRDT）
- 服务端持久化 + 多人共享