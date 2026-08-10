# define-summary.yaml 模板与字段定义（pg-1-define 产物）

> **SSOT**：字段定义以 `.pg/skills/src/runtime/spec/define-summary.schema.json` 为准；本文件只承载模板、填写指引与约束。完整示例见 `.pg/skills/examples/define-summary.example.yaml`。

**产物来源**：pg-1-define 的「定界后环境验证」环节（用户明确授权后才落盘）。

**消费方**：pg-propose 阶段 1.8（加载 + 校验）→ 阶段 2 全产物写作输入。

**文件位置**：`.pg/changes/<change-id>/0-define/define-summary.yaml`

**机械校验**（唯一校验点）：

```bash
python3 .pg/skills/src/core/workflows/skills/pg-propose/scripts/pg-validate-proposal.py define-summary <change-id>
```

---

## 完整模板

```yaml
schema_version: 1
change_id: <kebab-case，与 .pg/changes/<change-id>/ 目录名一致>
defined_at: "<ISO8601 UTC>"
defined_by: define
target_environment: <目标 environment 名，与 .pg/project.yaml environments.<name> 对应>

problem: >-
  要解决的问题（1-3 句话）。

solution: >-
  拟采用的方案（1-3 段）。

boundary:
  in_scope:
    - <本次变更 in-of-scope 的功能 / 模块 / API>
  out_of_scope:
    - <明确排除的功能 / 模块 / API>

verification_needs:
  - id: V-backend-1     # 必须 V-{track_id}-{seq} 形态, 与 design.md 保持一致
    # track_id 字段可选 (PR-C1 起): 省略时 validator 自动从 id 派生
    name: <验收点名称，一句话>
    what: >-
      要验证什么（业务语义，不写具体实现）。
    requires_capabilities:
      - capability: <业务语义级能力名，开放枚举，与 env-description.yaml 能力语义对应>
        min_quantity: 1
        description: <能力的额外说明，可选>
    downgrade_when_missing: >-
      能力缺失时的降级路径（可选）。
    post_discussion_status: verifiable   # verifiable | degraded | skipped
    env_resource_refs:                    # 仅 verifiable 时必填，其余必须为空
      - "{env.<段>[name=<资源名>]…}"

risks:            # 可选
  - <已知风险>
open_questions:   # 可选
  - <定界阶段尚未解决的开放问题>
```

---

## 字段定义

### 顶层必填字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `schema_version` | int | 固定 `1` | schema 版本 |
| `change_id` | string | pattern `^[a-z0-9][a-z0-9-]*$`，且必须与目录名一致 | 变更 ID |
| `defined_at` | string | ISO8601 UTC，**必须加引号**（避免 YAML 解析为 datetime） | 定界完成时间 |
| `defined_by` | string | 固定 `"define"` | 产出方标识 |
| `target_environment` | string | pattern `^[a-z][a-z0-9-]*$`，必须与 env-description.yaml 的 `described_for.environment` 一致 | 目标环境 |
| `problem` / `solution` | string | 非空 | 问题与方案 |
| `boundary` | object | `in_scope` + `out_of_scope` 均为数组 | 边界 |
| `verification_needs` | array | 至少 1 项 | V-* 列表 |

### verification_needs 单项

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | ✅ | `V-{track_id}-{seq}` 格式（如 `V-backend-1`），与 design.md / tasks.md / scenario 的 V-* 编号保持一致，全文唯一；可选连字符描述后缀（`V-backend-install-command-token`） |
| `track_id` | 可选 | V-* 归属 track id。**PR-C1 起改为可选**：省略时 `pg-validate-proposal.py` 自动从 `id` 按 `^V-([a-z][a-z0-9-]*)-` 派生。显式声明时必须与 id 中的 track 段一致 |
| `name` | ✅ | 验收点名称（一句话） |
| `what` | ✅ | 要验证什么（业务语义） |
| `requires_capabilities` | ✅ | 业务语义级能力需求清单，至少 1 项 |
| `requires_capabilities[].capability` | ✅ | **开放枚举**，由项目自行约定（如 `postgresql` / `redis_cache` / `object_storage` / `k8s_cluster` / `libvirt_local` / `multi_tenant_data` / `agent_grpc` / `iam_rbac` / `external_http`），必须与 env-description.yaml 中可探测的能力语义对应。propose 阶段 1.8 校验：每个 capability 必须在 env-description 中至少一个 infra_service / business_system / data_resource 的 `capabilities[]` 字段中声明，且 `min_quantity` ≤ 累计数量（infra_service 按 instances 数累加，business_system / data_resource 按 resource 数累加） |
| `requires_capabilities[].min_quantity` | ✅ | 最小数量要求（如"至少 2 个租户"→ `2`） |
| `downgrade_when_missing` | 可选 | 能力缺失时的降级路径 |
| `post_discussion_status` | ✅ | `verifiable` / `degraded` / `skipped`——define 阶段与用户讨论后基于真实 env-description 得出的最终状态 |
| `env_resource_refs` | 条件必填 | `verifiable` 时必填且非空；其他状态必须为空数组或不填 |

---

## env_resource_refs 占位约定

`env_resource_refs` 元素必须用 `{env.<段>[name=<资源名>]…}` 占位格式，与 scenario given 的 `{env.…}` 占位约定一致（propose 阶段 2.6.2 会把这些引用直接映射到 scenario given/then）。

- `<段>` ∈ `infra_services` / `business_systems` / `data_resources` / `config_resources` / `runtime_environment` / `external_dependencies`
- 括号内必须含 `name=<资源名>`，资源名必须在 env-description.yaml 的 `environments.<target_environment>.<段>[].name` 中真实存在
- 可继续下钻字段路径，如 `{env.infra_services[name=kubernetes].instances[0].id}`

**正确示例**：

```yaml
env_resource_refs:
  - "{env.infra_services[name=object-storage]}"
  - "{env.data_resources[name=bucket-catalog]}"
  - "{env.infra_services[name=kubernetes].instances[0].id}"
```

**错误示例**（会被校验器拦截）：

```yaml
env_resource_refs:
  - "infra_services[name=object-storage]"        # ❌ 缺 {env. 前缀
  - "{env.foo[name=x]}"                          # ❌ 段名非法
  - "192.168.122.221"                            # ❌ 硬编码 IP
```

---

## 约束

- **禁止复制 env-description.yaml 内容到 define-summary.yaml**——env-description 只是约束看的
- **禁止硬编码 IP / hostname / 端口**到 `env_resource_refs`（必须 `{env.…}` 占位）
- **禁止把 V-* 讨论变成实现细节设计**——实现细节是 design.md 的职责
- **`post_discussion_status` 是最终结论**——propose 阶段 1.8 只加载与校验，不做"假设 vs 事实"对账（对账在 pg-1-define 定界环节已完成）
- **propose 阶段 1.6 仍会重新调 describe_env**——防止 define→propose 之间环境漂移；若 env-description 资源发生变化导致 `env_resource_refs` 失效，1.8 校验会报错，需回到 pg-1-define 重新讨论
