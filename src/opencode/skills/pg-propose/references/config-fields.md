# Config Fields Index

本文档定义 `.pg/project.yaml` 中各字段的用途与生效范围。
pg-propose 与下游 skill 在引用 config.yaml 字段时必须遵循本文档。

> **编排模型**：见 [./orchestration-model.md](./orchestration-model.md)

---

## 字段分类总表

| 字段 | 类型 | 用途 | 在 SKILL.md / runner 中的引用形式 |
|---|---|---|---|
| `modules` | dict | 代码模块与命令绑定 | `modules.<m>.build` / `.lint` / `.test.<test_key>` |
| `environments` | dict | 运行时拓扑与启停 | `environments.<env>.roles.<role>.actions.start/stop/logs` |
| `tracks` | dict | TDV 循环编排 | `tracks.<t>.modules` / `.max_fix_retries` |
| `stages` | list | 阶段编排 | `stages[*].name` / `.tracks` / `.test_key` / `.gate` / `.environment.required` / `.environment.selection_rules` |
| `verify_merge` | dict | merge 时测试策略 | （pg-build 阶段使用） |
| `flyway` | dict | 数据库迁移路径 | `flyway.migration_path`（pg-build 阶段使用） |
| `git` | dict | git 配置 | `git.default_branch` |
| `propose.guidelines` | dict | 各产物指南（LLM 咨询性约束，不注入） | `propose.guidelines.proposal` / `.design` / `.tasks` |
| `propose.injections` | dict | 按目标产物分组的注入规则 | `propose.injections.proposal` → 按 `id`/`after_section` 注入 proposal.md |
| `build.injections` | dict | 按 agent 分组的 prompt 注入规则 | `build.injections.<phase>` → 按 `position` 注入 sub-agent prompt |

---

## config.yaml 字段生效范围

明确"pg-propose 在哪个阶段用哪些字段"：

| pg-propose 阶段 | 读取的字段 |
|---|---|
| 1e 获取管线配置 | `modules`, `tracks`, `stages`, `environments`, `propose`, `build` |
| 1f 加载 propose.injections | `propose.injections` |
| 2 proposal 生成 | `propose.guidelines.proposal`, `propose.injections.proposal` |
| 2 design 生成 | `propose.guidelines.design`, `stages[*]`, `tracks[*]`, `environments[*]` |
| 2 execution-manifest.yaml 生成（环境选择字段） | `stages[*].environment.required`, `stages[*].environment.selection_rules`, `environments` |
| 2 tasks 生成 | `propose.guidelines.tasks`, `stages[*]`, `tracks[*]`, `tracks[*].on_conditions`, `modules[*]`, `environments[*]` |
| 3 自审 | `propose.guidelines`, `context-summary.yaml.rules` |

---

## environments 字段使用约束

`environments` 段定义一个或多个运行环境，每个环境包含 role 拓扑和服务生命周期（架构级定义）。

**per-change 选择哪个 environment 由 `.pg/changes/<change>/execution-manifest.yaml` 的 `stages[i].environment` 字段承载**（SSOT），
不再放在 `tracks.real-integration.environment`（已删除）；v2 也**不再生成** `environment.yaml` 文件。

`stages[i].environment` 字段的生成逻辑：读取 `stages[*].environment.selection_rules` + 本 change 的 `affected_tracks`，按规则逐条匹配，详见 [./orchestration-model.md](./orchestration-model.md)「per-change environment 选择」段。

---

## fields 详细说明

### modules

```yaml
modules:
  <name>:
    root: <path>            # 代码根目录（相对项目根）
    language: <lang>        # java / go / typescript / proto
    build: <shell cmd>      # 构建命令
    lint: <shell cmd>       # lint 命令（可选）
    test:
      unit: <shell cmd>     # 单元测试（可选）
      integration: <shell cmd>  # 集成测试（可选）
      e2e: <shell cmd>      # E2E 测试（可选）
```

### tracks

```yaml
tracks:
  <name>:
    modules: [<module-name>, ...]  # 该 track 包含的 modules
    max_fix_retries: <int>          # 最大修复重试次数
    on_conditions: [<str>, ...]     # 可选：自然语言启用条件，任一命中则生成该 track heading
    description: <text>
    # v3.x 起按 track 关闭可选阶段（默认全部 true，向后兼容）
    code_review_enabled: <bool>     # 关闭后该 track 不生成 review 章节
    verify_enabled: <bool>          # v3.4 新增：关闭后不生成 verify 章节
    gate_enabled: <bool>            # v3.4 新增：关闭后不生成 gate 章节
                                     # 约束：verify + gate 同时为 false 不允许
                                     # （必须保留至少一个运行时质量门）
```

### stages

```yaml
stages:
  - name: <stage-name>
    tracks: [<track-name>, ...]
    test_key: unit|integration|e2e
    gate: all_pass|<custom>
    environment:
      required: <bool>                    # true=runner 启动 environment lifecycle hooks
      selection_rules: [...]              # list of natural-language rules (仅 environment.required=true 时有意义)
    description: <text>
```

> **注意**：`environment.selection_rules` 是项目级知识（pg-propose 推理依据），**不**在 SKILL.md 中硬编码启发式。

### environments

```yaml
environments:
  <env-name>:
    description: <text>
    roles:
      <role-name>:
        instances: [{name, host, port}, ...]
        actions:
          start: { host, script, args }
          stop:  { host, script, args }
          logs:  { host, script, args }
          tail:  { host, script, args }
    actions:
      health: { host, script }
      verify: { host, script, args }
```

### stages[].on_conditions（可选）

本 stage 是否启用的自然语言规则（pg-propose 解析，runner 不读）。

```yaml
- name: prepare-env-scripts
  on_conditions:
    - "本变更 affected_paths 命中 pg-spec-deprecated/scripts/** 任一路径"
    - "本变更 proposal.md 描述涉及环境层脚本、fixtures 或 setup 脚本"
```

- **字段缺省** → stage 视为常驻，永远生成 tasks.md 章节
- **字段非空** → pg-propose 按 LLM 推理判断每条规则是否成立，任一命中即启用 stage
- **OR 语义**：任一规则成立即 stage 启用
- **评估维度**：affected_paths glob 匹配 + proposal.md 语义关键词匹配

**与 `selection_rules` 的关键区别**：

| 字段 | 消费者 | 触发时机 | 用途 |
|---|---|---|---|
| `stages[*].on_conditions` | pg-propose | 生成 tasks.md 之前 | 决定 stage 是否存在 |
| `stages[*].environment.selection_rules` | runner | stage 启用后 + `environment.required=true` | 决定选哪个 env |

两者形态完全一致（`list[string]` 自然语言），便于项目级规则复用。

### tracks[].on_conditions（可选）

与 `stages[*].on_conditions` 同机制，但作用域为单个 track。定义在该 track 上时，仅当任一条件命中才生成该 track 的 tasks.md heading；所有条件未命中则完全跳过（不占章节号）。

```yaml
tracks:
  openapi-gen:
    type: simple
    on_conditions:
      - "本变更 backend track 在 affected_tracks 中"
      - "本变更涉及 API 端点、DTO 字段或 Bean Validation 注解的增删改"
```

- **字段缺省** → track 始终生成 heading（同当前行为）
- **字段非空** → pg-propose 在骨架阶段逐 track 评估，任一命中即生成 heading
- **与 stage 级区别**：stage 级跳过后该 stage 所有 track 均不生成；track 级仅影响自身

### propose.injections.proposal

```yaml
propose:
  injections:
    proposal:
      - id: <rule-id>
        after_section: <h2-title>   # 缺省时追加到模板尾部
        template: |
          <markdown text>
```

### build.injections

```yaml
build:
  injections:
    <phase>:                         # 例如 dev / review / verify / gate
      - position: prepend|append
        template: |
          <prompt text>
```

---

## 字段存在性校验

`.pg/skills/src/opencode/scripts/pg-parse-config.py` 在解析 config.yaml 时应：

1. 读取上述「字段分类总表」中允许的字段
2. 检测到「未声明字段」时（如拼写错误）输出 error 到 stderr 并 exit code = 1
3. 检测到 `tracks.<t>.modules` 引用的 module 不在 `modules` 字典中时，输出 error 并 exit code = 1