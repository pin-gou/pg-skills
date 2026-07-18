# project-editor — `.pg/project.yaml` 编辑器

Vue 3 + TypeScript + Vite 构建的**全员可用**（PM/QA/新人/老手）`.pg/project.yaml` 编辑器。

取代旧的 `.pg/skills/tools/yaml-editor/`。

## 快速启动

```bash
cd .pg/skills/tools/project-editor
pnpm install
pnpm dev
```

浏览器打开 <http://localhost:3028/>（vite 默认端口，配置见 `vite.config.ts`）。

> 端口已被 `pnpm dev` 自动选择：若 3028 被占用会递增。
> 项目根目录的 `.pg/project.yaml` 通过 vite 中间件直接提供，**无需 `python3 -m http.server`**。

## 4 个视图

| 视图 | 默认 | 角色 |
|---|---|---|
| 📊 **仪表板** | ✅ 首次启动 | 项目全貌 + 健康度 + 流水线概览 |
| 📋 **表单** | ✅ 常规默认 | 日常编辑主入口（左侧段导航 + 右侧详情面板） |
| 📐 **画布** | 辅助 | 4 列垂直流：Stages → Tracks → Modules，旁路 Environments；只读，与表单双向联动 |
| 🔍 **对比** | Ctrl+S 触发 | 保存前逐字段 diff，必须 Confirm 才落盘 |

## 编辑流程

1. 左侧段导航选择要编辑的段（如 `modules`）。
2. 右侧详情面板修改字段（表单控件，**完全无 YAML**）。
3. `Ctrl+S` → Diff 弹窗显示改动 → `Confirm Save` 落盘（File System Access API）或下载。

字段级悬浮 ⓘ 提示来自 `project.schema.json` 的 `description`。

## 段级覆盖（100% schema 顶层段）

- ⚙ **顶级参数** (schema / $schema)
- 📦 **modules** （含 root / language / build / lint / test / review_level / description；test 是动态 key 集合）
- 🌐 **environments** （roles × instances × actions；prepare_env / clean_env；actions cross-role orchestration）
- 🛤 **tracks** （type=standard/simple；commands 简写/对象切换；lint 模块 override）
- ⏱ **stages** （顺序执行，gate 策略；environment.required / selection_rules / on_conditions）
- 🔧 **fix_issue** （max_iteration_count / partial_success_threshold 等）
- 📊 **regression** （suite.{name}.module / test_keys / environment）
- 📐 **rules** （proposal / design / tasks 自由对象）
- 🔨 **build_rules** （id / type / target_agent / position / template）
- 📋 **proposal_rules** （同上结构）
- ✅ **verify_merge** （skip_tests_if_no_conflict）
- 🗄 **flyway** （migration_path）
- 🌿 **git** （default_branch）

## 关键能力

- ✅ 修复字段丢失 bug：所有 action 字段（host/hosts/parallel/script/args/timeout/description）均可在 UI 中编辑
- ✅ Diff 模态：保存前必看，每条改动标 `+ / - / ~`
- ✅ ajv 实时校验：0 错误才允许保存
- ✅ 新增 module 模板：Java+Maven / Go+Make / TS+Vite+pnpm / Shell / Proto 5 个预设
- ✅ 「复制现有 module」按钮
- ✅ 画布 ↔ 表单双向联动：点击画布节点跳表单，表单选中后画布节点高亮
- ✅ URL hash 跳转：`?view=form&module=backend#test.unit`
- ✅ 状态栏：未保存改动数 + schema 校验状态 + 快捷键提示
- ✅ 字段级 ⓘ 悬浮提示（schema.description）

## 快捷键

| 键 | 动作 |
|---|---|
| `Ctrl+S` / `Cmd+S` | 打开 Diff 弹窗 |
| `Esc` | 关闭弹窗 |

## 技术栈

- Vue 3 + TypeScript + Vite
- Pinia（状态管理；含 selection 状态驱动画布联动）
- ajv 8 + ajv-formats（schema 校验）
- yaml (eemeli/yaml)（YAML round-trip 保留 token）
- 自实现字段级 diff（避免引入额外依赖）

## 与 yaml-editor 的差异

| 维度 | 旧 yaml-editor | 新 project-editor |
|---|---|---|
| 视图数 | 6 个平铺 Tab（孤立） | 4 个视图（仪表板/表单/画布/对比） + 段导航 |
| 8 段覆盖 | ❌ 缺失（只能手写 YAML） | ✅ 全部 UI 化 |
| 字段丢失 bug | ❌ EnvironmentFlow 吞字段 | ✅ ActionEditor 完整保留 |
| 保存确认 | ❌ 开盲盒 | ✅ Diff 模态逐字段确认 |
| 启动 | ❌ `python3 -m http.server 8000`（README 与 vite 端口不一致 3028/3008） | ✅ `pnpm dev`，无外部依赖 |
| 双向联动 | ❌ 无 | ✅ 画布 ↔ 表单 + URL hash |
| 全员可用 | ❌ 仍需 YAML 心智 | ✅ 纯表单 + 字段 ⓘ |

## 目录结构

```
.pg/skills/tools/project-editor/
├── README.md
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.ts
│   ├── App.vue                 # 4 视图路由
│   ├── views/
│   │   ├── Dashboard.vue       # 📊 仪表板
│   │   ├── FormView.vue        # 📋 表单 (段导航 + 详情)
│   │   └── CanvasView.vue      # 📐 画布
│   ├── components/
│   │   ├── shared/
│   │   │   ├── TopBar.vue
│   │   │   ├── StatusBar.vue
│   │   │   ├── DiffModal.vue
│   │   │   └── FieldTooltip.vue
│   │   ├── sections/           # 段级表单组件 (14 个)
│   │   └── fields/             # 字段组件 (String/Number/Enum/Boolean/Command/Args/ActionEditor/FormField)
│   ├── stores/
│   │   └── projectStore.ts     # Pinia: data + dirty + errors + view + selection
│   ├── schema/loader.ts        # ajv 编译
│   ├── utils/
│   │   ├── yaml.ts             # round-trip
│   │   ├── diff.ts             # 字段级 diff
│   │   └── hash.ts             # URL hash 解析
│   └── templates/modules.ts    # 5 个语言预设
└── dist/                       # vite build 产物
```

## 构建生产版

```bash
pnpm build    # 输出到 dist/
```

构建后的产物路径为 `/.pg/skills/tools/project-editor/dist/`，可作为静态资源部署。