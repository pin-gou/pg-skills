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

`config.tracks[*]` 区分四种类型，pg-propose 生成 tasks.md 时**必须**按类型分流（见 [./tasks-templates.md](./tasks-templates.md)「生成算法」段）。

| 类型 | 判定字段 | runner 行为 | tasks.md 章节形态 |
|------|---------|-------------|-----------------|
| **standard** | `tracks.<id>.type` 不存在或 != `simple` / `scenario` | TDVG 四阶段（test → dev → verify → gate），由编排器派遣 sub-agent | 4 个子章节 |
| **simple** | `tracks.<id>.type == "simple"` | runner 派遣 `pg-build/simple` sub-agent 执行 `tracks.<id>.commands`，无 TDVG | **1 个章节**（派遣 pg-build/simple agent 执行 commands） |
| **scenario**（v3.5） | `tracks.<id>.type == "scenario"` | scenario-prepare → scenario-execute → (scenario-fix → scenario-execute)* → 完成。需 `scenario.yaml` 作为 SSOT | 3 个子章节（scenario-prepare / scenario-execute / scenario-fix 仅在 escalate 时物理存在） |
| **e2e** | `tracks.<id>.type == "e2e"` | （TBD：参考 scenario 实现；不属本节 SSOT） | 1 个章节 |

**判定时机**：pg-propose 阶段二 2e 生成 tasks.md 之前，按 `config.tracks[track_id].type` 判定；与 `affected_tracks` 无关。

**与 `affected_tracks` 的关系**：
- 即使 track 不在 `affected_tracks` 中（本次变更未触发），只要它出现在某 `enabled_stage.tracks` 列表里，pg-propose **必须**为它生成对应章节——standard track 生成 4 个 `- 无` 占位章节；simple track 生成 1 个 simple track 章节；保持 tasks.md 与 pipeline order 完整对齐
- 这与 validator 的 `_is_simple_track()` 行为一致：simple track 章节被列入 `skipped_items` 但仍要求存在
- **实现方式**：pg-propose 使用两阶段骨架填充法生成 tasks.md——阶段一按 `stage.tracks` 数组顺序机械生成所有 heading（禁止按 affected_tracks 分组），阶段二按 heading 顺序逐个填充 body。heading 骨架固定后，LLM 无法重排章节顺序。

**simple track 与 runner 的契约**（来自 `pg-propose/scripts/pg_pipeline_common.py:146-167`）：
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
  1. test_cmd = modules[m].test.unit
  2. lint_cmd = modules[m].lint
  3. build_cmd = modules[m].build
```

### stages × tracks × modules 的真实样例（来自 config.yaml）

| stage | tracks | test_key | environment.required | per-change 选择落地 |
|---|---|---|---|---|
| `dev`（v3.5 前为 `dev-isolated`） | backend, agent, frontend | unit | false | 无（runner 不启停服务） |
| `real-integration`（v3.5） | scenario-test (type=scenario) | scenario | true | `execution-manifest.yaml: stages[i].environment` |

---

## per-change environment 选择（SSOT：execution-manifest.yaml）

**架构变更（v2）**：per-change 的 environment 选择直接落在 `execution-manifest.yaml` 的 `stages[i].environment` 字段（string 或 `{name: string}`）。不再生成 `.pg/changes/<change>/environment.yaml`。

### 三个文件的职责

| 文件 | 职责 |
|------|------|
| `.pg/project.yaml` | 1. 架构级：modules / environments / tracks / stages 拓扑<br>2. 每个 `environment.required: true` 的 stage 加 `environment.selection_rules: list[string]` 字段，**以自然语言规则**说明该 stage 的选择依据（每条 string 一条原子规则） |
| `.pg/changes/<change>/execution-manifest.yaml` | **per-change 的具体选择结果**（SSOT）<br>结构：`stages[i].environment: <env-name>`（string）或 `stages[i].environment.name: <env-name>`（dict 形式）<br>runner 在每个 `environment.required=true` 的 stage 读取对应 stage 字段 |
| `tasks.md` | 不再含 `## Deployments` 段（SSOT 落到 execution-manifest.yaml）<br>`## Deployments` 在 archive 历史中保留为可读副本 |

### real-integration / dev-mock-integration 的 environment 选择流程

1. pg-propose 读取 `.pg/project.yaml` 的 `stages` 段
2. 过滤出所有 `environment.required: true` 的 stage
3. 对每个这样的 stage，读取其 `environment.selection_rules`（自然语言规则数组）
4. 根据本 change 的 `affected_tracks`，按规则逐条匹配，给出该 stage 选定的 environment 名称
5. 写入 `.pg/changes/<change>/execution-manifest.yaml` 的 `stages[i].environment` 字段

### runner 行为约束

- `execution-manifest.yaml` 缺失 → workflow_failed 终止
- `stages[i].environment` 未声明 → 该 stage 视为 skipped（无 env 准备）
- `environment` 值不在 `config.yaml.environments` 中 → workflow_failed 终止
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

## on_conditions & 机械评估（v3.2 升级）

### 概念

- `affected_tracks` —— 本次变更改动了哪些业务 track
- `affected_paths` —— 本次变更触及了哪些文件路径 (glob 列表)
- `on_conditions` —— config.stages / config.tracks 中的"启用规则" (`list[string]` 自然语言)

三者协作：

- **affected_tracks** 决定 body 是 `- 无` 还是具体任务（heading 始终生成）
- **on_conditions** 决定**机械评估结果**，由 `pg-gen-tasks-skeleton.py` 自动产出，**不再影响 tasks.md 章节生成**（v3.2 起所有 stage × track × sub 都生成 heading）

### v3.2 行为变化（与 v3.1 的差异）

| 维度 | v3.1（已废弃） | v3.2（当前） |
|------|---------------|-------------|
| 章节生成 | on_conditions 未命中 → 不生成 heading | on_conditions 未命中 → 仍生成 heading，body = `- 无` |
| 章节编号 N | 受 on_conditions 跳过影响（连续递增） | 全量展开，编号稳定不变 |
| 评估执行者 | LLM 在阶段二手工推理 | `pg-gen-tasks-skeleton.py` 脚本机械评估 |
| LLM 决策时机 | 阶段二生成 tasks.md 前 | 阶段三 review 时复核机械评估 |
| 留痕位置 | review-notes.md 段落 | `on-conditions-eval.md` + review-notes.md 合并 |

**核心好处**：

- 章节编号 N 在生成后**永远不变**，LLM 反悔启用某个 stage 只需改 body
- 决策可追溯：机械评估的依据在 HTML 注释 + 评估表格中完整保留
- LLM 注意力聚焦：阶段二不再维护 on_conditions 决策逻辑，只在阶段三做最终复核

### 机械评估算法（pg-gen-tasks-skeleton.py 实现）

```python
def evaluate_on_conditions(rule, affected_paths, proposal_text):
    """Path-glob 维度 + 关键词维度, OR 语义."""
    path_hit = check_glob_match(rule, affected_paths)
    semantic_hit = check_keyword_match(rule, proposal_text)
    return {
        "rule": rule,
        "path_hit": path_hit,
        "semantic_hit": semantic_hit,
        "matched": path_hit or semantic_hit,
    }
```

- **path 维度**：从规则中提取 glob（如 `.pg/hooks/**`、`pg-spec-deprecated/scripts/**`），
  与 `affected_paths`（从 proposal.md "### 包含"段提取的 glob 列表）做 fnmatch 匹配
- **semantic 维度**：从规则中提取关键词（去停用词后），检查是否在 proposal.md 全文中出现
- 任一维度命中 → 规则成立

### on_conditions 评估记录模板

`pg-gen-tasks-skeleton.py` 自动生成 `.pg/changes/<change>/1-propose-review/on-conditions-eval.md`，
含 stage 级 / track 级每条规则的机械评估表：

| # | 规则 | 机械评估 (path) | 机械评估 (semantic) | 建议 | 最终决策 | 依据 |
|---|------|----------------|--------------------|------|----------|------|
| 1 | "本变更 affected_paths 命中 .pg/hooks/**" | ✅ | ❌ | 命中 | [ ] |  |
| 2 | "本变更包含 fixtures 修改" | ❌ | ✅ | 命中 | [ ] |  |
| **结论** | | | | | [ ] |  |

LLM 在阶段三 review 时：

1. 对每条规则勾选「最终决策」：同意 → `[x]`；覆盖 → `[~]` + 写依据
2. 把整个表格内容**合并到** `review-notes.md` 的「on_conditions 评估记录」段
3. 若某 stage/track 决策翻转为"启用"，需在 review-notes 标注"建议启用"，由用户在 review 阶段决定

### 与 runner / validator 的契约

- runner **不解析** `on_conditions` 字段
- runner 按 tasks.md 实际章节数（不被 config.stages 数组长度限制）执行
- tasks.md 含 config.stages 中没有的 stage 章节 → runner 照常执行（向后兼容）
- tasks.md 不含 config.stages 中的某 stage 章节 → runner 跳过该 stage（tasks.md 是 SSOT）

### track 级 on_conditions

与 stage 级 `on_conditions` 同机制，但作用域为单个 track 而非整个 stage。定义在 `tracks.<id>.on_conditions` 中。

```yaml
tracks:
  openapi-gen:
    type: simple
    on_conditions:
      - "本变更 backend track 在 affected_tracks 中"
      - "本变更涉及 API 端点、DTO 字段或 Bean Validation 注解的增删改"
```

| 属性 | stage 级 | track 级 |
|------|---------|---------|
| 字段位置 | `stages[*].on_conditions` | `tracks[*].on_conditions` |
| 评估时机 | 阶段二脚本生成 tasks.md 时 | 阶段二脚本生成 tasks.md 时 |
| 留痕位置 | tasks.md 章节 HTML 注释 + on-conditions-eval.md | 同上 |
| 评估语义 | 任一命中即建议启用（OR） | 同一维度的 OR 语义 |
| heading 生成 | 始终生成 | 始终生成（v3.2 起） |

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
            run(cmd.test.unit)

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