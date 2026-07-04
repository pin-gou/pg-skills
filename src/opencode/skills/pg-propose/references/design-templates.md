# Design Templates

本文档定义 `design.md` 的默认模板与 Verification Criteria 的生成规则。

---

## 默认模板

路径：`.pg/changes/<change-name>/design.md`

```markdown
# {change-name} 设计
## 架构概览
{涉及的后端模块、前端组件、数据流}

## API 设计（如有）
{接口路径、请求/响应格式、状态码}

## 数据模型（如有）
{新增或修改的数据库表、字段}

## 组件设计（如有）
{前端组件拆分、交互逻辑}

## 关键约束与契约

**本章节是变更的"硬约束集合"，必须显式写明。后续 reviewer、implementer、gate agent 都以此为准。**

### 前置条件
- {数据库 schema 版本要求、依赖服务、配置项等}
- {如果涉及 Flyway 迁移，必须标注 "forward-only 不可回滚，发布前必须在测试库完整跑通"}

### 影响面
- 哪些表/索引/字段会变更：{列出}
- 哪些 service / controller / mapper 方法签名会变：{列出}
- 是否破坏任何对外 API：{是/否 + 具体路径}

### 性能契约
- {关键查询的性能要求：单次查询 / 禁止 N+1 / 最大响应时间}
- {事务边界要求：哪些方法必须 @Transactional}

### 错误码与编号段
- 新增错误码必须落在对应模块的编号段内（参考 `.pg/context/summary.yaml` 的 `rules` 或 AGENTS.md）

### 可观测性
- 关键日志点：{INFO/WARN/ERROR 级别、含哪些字段}
- 关键指标：{Counter/Gauge 名}
- RequestId 追踪：{本变更是否需要额外埋点}

## Verification Criteria

按 `stages` 顺序遍历，对其中每个 stage 的每个 track 生成对应的 Verification Criteria 章节。
章节标题使用 `{stage.name} {track_id}` 格式，编号前缀使用 track_id。
```

---

## Verification Criteria 生成规则

### 章节组织

按 `stages` 顺序遍历，对每个 stage 的每个 track 生成一个子章节。
**章节标题格式**：`### {stage.name} {track_id} Verification Criteria`

### V-* 编号规则

- **编号格式**：`V-{track_id}-N`（如 `V-backend-1`、`V-frontend-1`）
- **跨 stage 全局递增**：同一 track 的 V-* 在不同 stage 独立编号（dev-isolated 的 V-backend-1、dev-mock-integration 的 V-backend-2、real-integration 的 V-backend-3）
- **顺序**：按 stage 在 `config.stages` 中的顺序、track 在 `stage.tracks` 中的顺序排列

### 模板

```markdown
### {stage.name} {track_id} Verification Criteria
| ID | 验证项 | 前置/数据准备 | 方法 | 预期结果 |
|-----|--------|---------------|------|---------|
| V-{track_id}-1 | ... | {测试数据、登录、依赖服务} | ... | ... |
```

### 实际输出示例（`affected_tracks = [backend, frontend]`，三 stage 全展开）

```markdown
## Verification Criteria

### dev-isolated backend Verification Criteria
| ID | 验证项 | 前置/数据准备 | 方法 | 预期结果 |
|-----|--------|---------------|------|---------|
| V-backend-1 | POST 创建 bucket 返回 201 | 需先以 admin 登录获取 token | curl POST /api/... | 返回 201 + bucket ID |
| V-backend-2 | GET 查询返回正确字段 | 需已存在 bucket | curl GET /api/.../{id} | 返回完整 bucket 信息 |

### dev-isolated agent Verification Criteria
- （agent 未改动，无 V-*）

### dev-isolated frontend Verification Criteria
| ID | 验证项 | 前置/数据准备 | 方法 | 预期结果 |
|-----|--------|---------------|------|---------|
| V-frontend-1 | 列表页正确渲染 | 需已存在测试数据 | 浏览器访问 | 显示 bucket 列表 |

### dev-mock-integration backend Verification Criteria
| ID | 验证项 | 前置/数据准备 | 方法 | 预期结果 |
|-----|--------|---------------|------|---------|
| V-backend-3 | mock 联调场景：POST 后 GET 拿到新数据 | 需 backend 服务在跑 | curl POST + GET | GET 返回新创建的 bucket |

### dev-mock-integration frontend Verification Criteria
| ID | 验证项 | 前置/数据准备 | 方法 | 预期结果 |
|-----|--------|---------------|------|---------|
| V-frontend-2 | 创建后列表更新 | 需 backend mock 已就绪 | 填写表单提交 | 列表新增一行 |

### real-integration Verification Criteria
| ID | 验证项 | 前置/数据准备 | 方法 | 预期结果 |
|-----|--------|---------------|------|---------|
| V-real-integration-1 | 跨模块联调：端到端流程通过 | change 的 execution-manifest.yaml 中 real-integration.environment 选定的环境就绪 | 浏览器跑完整流程 | 无报错、数据正确 |
```

---

## V-* 编写要求

- **"前置/数据准备"列必填**，避免执行者漏掉初始化步骤
- 每条 V-* 必须能验证 proposal.md"风险和注意事项"中的某一条（无法直接验证的需说明验证方式）
- 每条 V-* 必须包含具体的 HTTP 状态码、响应格式或 UI 行为
- 同一 V-* 在 tasks.md 不同 stage 的 verify 章节会被引用多次（每个 stage 引用本 stage 范围内的 V-*）

---

## 变更类型判定

**本章节留痕"哪些 track 被影响 / 哪些被跳过"的决策过程**，供 final-gate 跨 track 依赖项审查时参考。

| track | 是否影响 | 理由 |
|-------|---------|------|
| backend | ✅/❌ | {具体改动列举} |
| agent | ✅/❌ | {是否涉及 gRPC / libvirt / capability} |
| frontend | ✅/❌ | {是否涉及组件 / API 调用} |

**affected_tracks**：{列出被影响的 track id 列表}

> **注意**：
> - `agent-proto` 模块归入 agent track 的 modules list
> - `openapi-gen` 在 `.pg/project.yaml` 中是 `type: simple` 的**独立 track**（**不属于** frontend track 的 modules list）。pg-propose 生成 tasks.md 时为它生成 1 个 simple track 章节（canonical form heading 含 `(simple track: 派遣 pg-build/simple agent 执行 commands)` + body 单 `- 无` 行），runner 派遣 `pg-build/simple` sub-agent 执行其 `commands`，不走 TDVG
> - 详细编排模型见 [./orchestration-model.md](./orchestration-model.md)「Track 类型」段

---

## 约束

- 使用中文撰写
- UI 布局：使用 ASCII box 可视化界面结构（用于展示组件位置、嵌套关系）
- 代码示例：使用标准 markdown 代码块（```），禁止用 ASCII 框包裹
- 组件描述：使用结构化格式（表格、编号列表、bullet points），不用 ASCII 框
- 前端列表页必须包含 ID 列
- Verification Criteria 的编号规则：`V-{track_id}-N`，跨 stage 全局递增
- 每个验证项需包含具体的 HTTP 状态码、响应格式或 UI 行为
- V-* 表必须包含"前置/数据准备"列，避免执行者漏掉初始化步骤
- design.md 必须包含"关键约束与契约"章节，承载前置条件/影响面/性能契约/错误码编号段/可观测性
- design.md 必须包含"变更类型判定"留痕章节，供 final-gate 审查跨 track 依赖项
- design.md 的每条 V-* 必须能验证 proposal.md"风险和注意事项"中的至少一条风险

---

## 相关文档

- 字段索引：[./config-fields.md](./config-fields.md)
- 编排模型：[./orchestration-model.md](./orchestration-model.md)
- proposal 模板：[./proposal-templates.md](./proposal-templates.md)
- tasks 模板：[./tasks-templates.md](./tasks-templates.md)