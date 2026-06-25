# Orchestration Model

本文档定义 pg-propose / pg-build 在生成产物和编排任务时使用的统一编排模型。
所有引用此模型的下游 skill（pg-build、pg-verify-and-merge、pg-regression 等）
都必须以本文档为唯一事实来源。

---

## 三层模型

```
stages ── 一组阶段（dev-isolated → dev-mock-integration → real-integration）
   │
   └─ tracks ── 在该 stage 内顺序执行的轨道（backend / agent / frontend / real-integration）
                  │
                  └─ modules ── 该轨道包含的代码模块（如 backend track → backend module → mvn 命令）
```

| 层 | 字段来源 | 职责 | 例子 |
|---|---|---|---|
| **stages** | `config.stages[*]` | 阶段编排，决定跑哪些 track、用哪个 test_key、是否需要部署、gate 是什么 | `dev-isolated` (test_key=unit, requires_deployment=false) |
| **tracks** | `config.tracks[*]` | 轨道，跨 stages 复用，决定在哪些 modules 上跑 TDV 循环 | `backend`、`agent`、`frontend`、`real-integration` |
| **modules** | `config.modules[*]` | 代码模块，绑定真实的 build/lint/test 命令 | `backend` (root=<module-name>, test.unit="cd <module-name> && mvn test") |

## Track 类型

`config.tracks[*]` 区分两种类型，pg-propose 生成 tasks.md 时**必须**按类型分流（见 [./tasks-templates.md](./tasks-templates.md)「生成算法」段）。

| 类型 | 判定字段 | runner 行为 | tasks.md 章节形态 |
|------|---------|-------------|-----------------|
| **standard** | `tracks.<id>.type` 不存在或 != `"simple"` | TDVG 四阶段（test → dev → verify → gate），由编排器派遣 sub-agent | 4 个子章节 |
| **simple** | `tracks.<id>.type == "simple"` | runner 派遣 `pg-build/simple` sub-agent 执行 `tracks.<id>.commands`，无 TDVG | **1 个章节**（派遣 pg-build/simple agent 执行 commands） |

**判定时机**：pg-propose 阶段二 2e 生成 tasks.md 之前，按 `config.tracks[track_id].type` 判定；与 `affected_tracks` 无关。

**与 `affected_tracks` 的关系**：
- 即使 track 不在 `affected_tracks` 中（本次变更未触发），只要它出现在某 `enabled_stage.tracks` 列表里，pg-propose **必须**为它生成对应章节——standard track 生成 4 个 `- 无` 占位章节；simple track 生成 1 个 simple track 章节；保持 tasks.md 与 pipeline order 完整对齐
- 这与 validator 的 `_is_simple_track()` 行为一致：simple track 章节被列入 `skipped_items` 但仍要求存在

**simple track 与 runner 的契约**（来自 `pg-build/scripts/pg_pipeline_common.py:146-167` 和 `pg-pipeline-runner.py:_build_simple_dispatch`）：
- runner 看到 simple type → `get_track_type()` 归类为 `"phase"`，作为 dispatch 类型处理
- runner 在 `cmd_next` 时调用 `_noopify_simple_track_sections()`，把 simple track 对应章节自动改写为 canonical form（heading 后追加 `(simple track: 派遣 pg-build/simple agent 执行 commands)` + body 单 `- 无` 行）
- 改写 idempotent：已是 canonical form 的章节保持原样
- 改写后 `cmd_detect` 把这些章节视为 `all_noop`，跳过 TDVG 直接进入 `_execute_phase` → 内部 redirect 到 `_build_simple_dispatch`
- `_build_simple_dispatch` 构造 `commands_normalized` + decision table 渲染到 prompt，派遣 `pg-build/simple` sub-agent 执行 `tracks.<id>.commands`，按命令的 `on_failure` 处理结果（`fail` / `continue` / `retry`）；失败时尝试自动修复（缺依赖等）

**典型 simple track 例子**：`openapi-gen`（执行 `pnpm openapi` 重生成前端 API 客户端，幂等且可重跑的代码生成）

### 跨层查找命令的规则

runner 执行 test 命令时按以下规则查找：

```
(stage, track) → track.modules[*] → 每个 module:
  1. test_key = stages[?].test_key（命中当前 stage）
  2. test_cmd = modules[m].test[test_key]
  3. lint_cmd = modules[m].lint
  4. build_cmd = modules[m].build
```

### stages × tracks × modules 的真实样例（来自 config.yaml）

| stage | tracks | test_key | environment.required | per-change 选择落地 |
|---|---|---|---|---|
| `dev-isolated` | backend, agent, frontend | unit | false | 无（runner 不启停服务） |
| `dev-mock-integration` | backend, agent, frontend | integration | true | `environment.yaml: dev-mock-integration: <env-name>` |
| `real-integration` | real-integration | e2e | true | `environment.yaml: real-integration: <env-name>` |

---

## per-change environment 选择（SSOT：environment.yaml）

**架构原则**：同一架构下不同 change 的特点不一样，可以选择不同的 environment 进行开发；config.yaml 只承载**架构级**定义（modules / environments / tracks / stages），**per-change 选择必须由 pg-propose 在 `.pg/changes/<change>/environment.yaml` 中确定**。

### 三个文件的职责

| 文件 | 职责 |
|------|------|
| `.pg/project.yaml` | 1. 架构级：modules / environments / tracks / stages 拓扑<br>2. 每个 `environment.required: true` 的 stage 加 `environment.selection_rules: list[string]` 字段，**以自然语言规则**说明该 stage 的选择依据（每条 string 一条原子规则） |
| `.pg/changes/<change>/environment.yaml` | **per-change 的具体选择结果**（SSOT）<br>结构：`per-stage map`，key 是 stage name，value 是该 stage 选定的 environment 名称（或 `skip`）<br>runner 在每个 `environment.required=true` 的 stage 读取对应 stage 字段 |
| `tasks.md` | 不再含 `## Deployments` 段（SSOT 落到 environment.yaml）<br>`## Deployments` 在 archive 历史中保留为可读副本 |

### real-integration / dev-mock-integration 的 environment 选择流程

1. pg-propose 读取 `.pg/project.yaml` 的 `stages` 段
2. 过滤出所有 `environment.required: true` 的 stage
3. 对每个这样的 stage，读取其 `environment.selection_rules`（自然语言规则数组）
4. 根据本 change 的 `affected_tracks`，按规则逐条匹配，给出该 stage 选定的 environment 名称
5. 写出 `.pg/changes/<change>/environment.yaml`（per-stage map）

### runner 行为约束（硬性）

- `environment.yaml` 缺失 → workflow_failed 终止
- `environment.yaml` 中某 stage 未声明 → workflow_failed 终止
- `environment.yaml` 中 env 值不在 `config.yaml.environments` 中 → workflow_failed 终止
- `config.yaml.stages.<stage>.environment.selection_rules` **不应**被硬编码在 SKILL.md / references 中——这是项目级知识，由 config.yaml 承载

---

## affected_tracks 推导

`affected_tracks` 由 design.md 阶段的人工判定得出（pg-propose 阶段二 2c 步）。

判定流程：

1. **列举各组件改动**：遍历所有 track，列出涉及的改动：
   - backend 改动：API 端点、业务逻辑、数据模型等
   - agent 改动：gRPC 通信、VM 操作、心跳逻辑等
   - frontend 改动：组件、页面、API 模块等
2. **生成 affected_tracks**：哪些 track 有改动（如 `[backend, frontend]`）
3. **记录判定结果**：将 `affected_tracks` 记录到临时上下文，供生成 tasks.md 时引用

**注意**：无论 affected_tracks 是否包含 `real-integration`，tasks.md 都会生成一个
real-integration 章节（因为 `real-integration` 是 stage 编排的常驻节点），但当
affected_tracks 为空时（如纯文档变更），整个 verify 章节标 `无`。

---

## on_conditions & stage 动态启用

### 概念

- `affected_tracks` —— 本次变更改动了哪些业务 track
- `affected_paths` —— 本次变更触及了哪些文件路径 (glob 列表)
- `on_conditions` —— config.stages 中某 stage 的"启用规则" (`list[string]` 自然语言)

三者协作：
- **affected_tracks** 决定 `stages[*].tracks` 哪些生成 `- 无` vs 实际任务
- **on_conditions** 决定 `stages[*]` 哪些 stage 整体启用 (生成 tasks.md 章节 + 写入 environment.yaml)

### 与 selection_rules 的区别

| 字段 | 触发时机 | 语义 | 消费者 |
|---|---|---|---|
| `stages[*].environment.selection_rules` | stage 已启用 + `environment.required=true` 时 | 按顺序匹配选 env 名称 | runner |
| `stages[*].on_conditions` | pg-propose 生成 tasks.md 之前 | 任一命中即启用 stage (OR 语义) | pg-propose |

`on_conditions` 是 pg-propose 阶段的"是否生成章节"判定；
`selection_rules` 是 runner 阶段的"哪个 env"判定。

两者形态一致（都是 `list[string]` 自然语言），但消费者与触发时机完全不同。

### 解析算法（pg-propose 阶段二 2c.5）

pg-propose 收到 config.stages 时，对每个 stage 做：

```python
def is_stage_enabled(stage, affected_paths, proposal_md_text):
    on_conditions = stage.get("on_conditions")
    if not on_conditions:
        return True  # 无 on_conditions = 常驻 stage，永远启用

    # 解析每条自然语言规则（LLM 推理）
    for condition in on_conditions:
        if evaluate_condition(condition, affected_paths, proposal_md_text):
            return True
    return False


def evaluate_condition(condition, affected_paths, proposal_md_text):
    """
    自然语言规则的 LLM 推理:
    1. 提取规则中的关键路径 glob（如 pg-spec-deprecated/scripts/**）
    2. 检查 affected_paths 是否命中（机械匹配）
    3. 检查 proposal.md 文本是否包含规则中描述的语义关键词
       （如"环境层脚本"、"fixtures"、"setup 脚本注入"）
    4. 任一维度命中即认为规则成立（OR 语义）
    """
    # LLM 推理路径：返回 bool
    ...
```

### 示例

```yaml
stages:
  - name: prepare-env-scripts
    on_conditions:
      - "本变更 affected_paths 命中 pg-spec-deprecated/scripts/** 任一路径"
      - "本变更 proposal.md 包含对环境层脚本或 fixtures 的修改描述"
```

- 若 `affected_paths = ["pg-spec-deprecated/scripts/fixtures/e2e-test-users.sql"]`，
  pg-propose 判定第 1 条命中 → stage 启用
- 若 `affected_paths = []` 且 proposal.md 不含"环境层脚本"等关键词 → 两条都不命中 → stage 跳过

### 与 runner 的契约

- runner **不解析** `on_conditions` 字段
- runner 按 tasks.md 实际章节数（不被 config.stages 数组长度限制）执行
- 若 runner 收到的 tasks.md 含 config.stages 中没有的 stage 章节 → 按 tasks.md 章节照常执行（向后兼容）
- 若 tasks.md 不含 config.stages 中的某 stage 章节 → runner 跳过该 stage（因为 tasks.md 是 SSOT）

### 评审留痕

`on_conditions` 评估存在不确定性（自然语言），pg-propose 应在 `review-notes.md` 留痕
记录每条 on_conditions 的评估结果（命中/未命中 + 依据），便于 review 阶段追溯。

例：

```markdown
## on_conditions 评估记录

### prepare-env-scripts
- 规则 1: "本变更 affected_paths 命中 `pg-spec-deprecated/scripts/**` 任一路径"
  - 评估: 命中 ✅
  - 依据: affected_paths 含 `pg-spec-deprecated/scripts/fixtures/e2e-test-users.sql`
- 规则 2: "本变更 proposal.md 包含对环境层脚本或 fixtures 的修改描述"
  - 评估: 未评估（规则 1 已命中）
- 结论: stage 启用 ✅
```

### 与 validator 的契约

pg-propose LLM 在 2c.5 评估 on_conditions 得到的 `enabled_stages` 决策，需要显式通知 `pg-validate-tasks.py`：

- **tasks.md 章节** = `enabled_stages` × `stage.tracks`（pg-propose 生成产物）
- **validator 调用** = `validate <change> --skip-stages <disabled-stage-1>,<disabled-stage-2>,...`
- **review-notes.md** = 「on_conditions 评估记录」段留痕（决策的可追溯凭据）
- **runner 行为** = 无章节视为 `completed (skip)`，与 pg-propose 设计一致（不需要 `--skip-stages`）

**为什么需要显式传参**：

- `on_conditions` 是自然语言规则，评估本质是 LLM 推理
- validator 是纯 Python 脚本，无 LLM 推理能力
- 通过 `--skip-stages` 把"未启用的 stage"显式传过去，让 validator 与 runner 行为对齐

**`--skip-stages` 参数语义**：

| 属性 | 行为 |
|------|------|
| 匹配方式 | 严格按 `stage.name` 整词匹配（非前缀） |
| 多值语法 | 逗号分隔，可重复传：`--skip-stages a --skip-stages b,c` |
| 范围 | 跳过该 stage 下所有 `<stage>.<track>` item |
| 输出 | 被跳过的 item 计入 `summary.skipped_items`，出现在 human report 的 `## Skipped Items` 段 |

**漏传后果**：

- validator 报 `missing_track` error
- LLM 必须（a）补占位章节让 validator 通过（不推荐），或（b）传 `--skip-stages` 让 validator 接受
- 选 (b)：SKILL.md 2e.5 段已明确说明

---

## runner 视角的执行顺序（伪代码）

```
# runner 解析 stages，按顺序执行每个 stage
for stage in config.stages:
    # 部署（如需）
    if stage.requires_deployment:
        deploy(stage)

    # 顺序执行 stage 内 tracks
    for track_id in stage.tracks:
        # 取 track
        track = config.tracks[track_id]

        # 对 track.modules 每个 module 执行 lint → test → verify V-*
        for module_name in track.modules:
            cmd = config.modules[module_name]
            run(cmd.lint)
            run(cmd.test[stage.test_key])

        # 启动服务（仅 verify 阶段需要）
        if stage.requires_deployment:
            env = auto_select_environment(affected_tracks)
            role = track_id_to_role(track_id)
            run(environments[env].roles[role].actions.start)

        # 验证 V-*（由 verify agent 完成）
        verify_v_items(track_id, stage.name)

        # gate（如 gate=all_pass）
        if stage.gate == "all_pass":
            gate_assess(track_id, stage.name)
```

---

## 相关文档

- 字段索引：[./config-fields.md](./config-fields.md)
- tasks 生成算法：[./tasks-templates.md](./tasks-templates.md)