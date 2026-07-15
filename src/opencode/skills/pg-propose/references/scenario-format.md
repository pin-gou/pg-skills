# Scenario YAML 格式与 placeholder 协议

本文档定义 `scenario-<track>.yaml` 的格式、`pg-gen-scenario.py` 生成的 skeleton 占位符约定，以及 v3.7 起 `pg-validate-proposal.py` 的 placeholder 校验协议。

---

## 文件位置

每个**启用**的 scenario track 拥有一个独立 YAML 文件：

```
.pg/changes/<change-name>/scenario-<track-id>.yaml
```

**禁用**的 scenario track 不生成文件——SSOT 在 `on-conditions-eval.md` 的 `scenario_tracks_decision` 段。

---

## Skeleton 生成（`pg-gen-scenario.py`）

`pg-gen-scenario.py` 生成的 skeleton 含两类占位符：

### 1. LLM 必填段占位符

skeleton 中的"sentinel"占位符，LLM 编辑时**必须**替换为真实内容，**否则** `pg-validate-proposal.py` 会报 `scenario_placeholder_unfilled` 错误。

**协议**：

| 占位符 | 含义 | LLM 必须替换为 |
|--------|------|---------------|
| `S-<unique-name>` | scenario 唯一 ID | 形如 `S-create-bucket-success`、`S-list-host-returns-200` |
| `<一句话描述此 Scenario 验证目标（LLM 必填）>` | description 字段 | 一句话验证目标 |
| `<前置条件 1>`, `<前置条件 2>` | given 数组元素 | 真实前置条件（已创建资源、依赖服务就绪等） |
| `method: GET` | HTTP method（默认值） | 真实 method（POST/PUT/DELETE/db query） |
| `url: /api/.../...` | 端点 URL | 真实 URL 路径 |
| `expect_status: 200` | 期望响应码 | 真实期望码 |
| `status_code == 200` | then 断言 | 真实断言（如 `response.bucket.id matches "^[a-f0-9]{32}$"`） |
| `<cleanup>` action | and 段动作 | 真实 cleanup（HTTP DELETE / db DELETE） |

### 2. 注释占位符

`_meta._comment` 段是注释，**不属于** schema，pg-build scenario-execute agent 会忽略。LLM 可保留也可删除，不触发 placeholder 校验。

---

## placeholder 校验协议（v3.7 新增）

### 触发时机

`pg-validate-proposal.py manifest <change>` 在以下条件**全部满足**时执行 placeholder 校验：

1. manifest 含至少一个 `enabled=true` 且 `type=scenario` 的 track
2. 对应 `scenario-<track>.yaml` 文件存在

### 校验实现

`_validate_scenario_placeholders(manifest, change_root)` 在 `pg-validate-proposal.py` 中新增，返回 issue 列表。

每个 scenario 文件的校验规则：

1. **scenario_id 占位符**：`scenarios[].scenario_id == "S-<unique-name>"` → 报 `scenario_placeholder_unfilled: scenario_id`
2. **description 占位符**：`scenarios[].description` 含 `（LLM 必填）` → 报 `scenario_placeholder_unfilled: description`
3. **given 占位符**：`scenarios[].given[]` 含 `<前置条件 ...>` 字面量 → 报 `scenario_placeholder_unfilled: given`
4. **URL 占位符**：`scenarios[].when[].url == "/api/.../..."` → 报 `scenario_placeholder_unfilled: when.url`
5. **method 占位符**：`scenarios[].when[].method == "GET"` **且** url 含 `...` → 报 `scenario_placeholder_unfilled: when`（与 URL 联动）

每个占位符未替换 → 各自发 issue（不合并）。

### 错误码

| 错误码 | 含义 | 修复动作 |
|--------|------|---------|
| `scenario_placeholder_unfilled` | LLM 必填段仍有占位符 | 编辑对应 `scenario-<track>.yaml`，替换占位符 |

### 错误格式示例

```
[scenario_placeholder_unfilled] scenario-test.yaml scenarios[0].scenario_id 含占位符
  'S-<unique-name>', LLM 必须替换为 S-<verb>-<obj>-<result> 风格
```

---

## Scenario schema 完整定义

参见 [pg-propose SKILL.md "scenario.yaml 生成指引"段](../SKILL.md#scenario-yaml-生成指引v36-仅当-scenario-track-启用)。

简版：

```yaml
scenarios:
  - scenario_id: S-<unique-name>
    critical: true
    description: <一句话描述>
    given:
      - <前置条件 1>
      - <前置条件 2>
    when:
      - name: <动作名>
        method: <HTTP method | db query>
        url: <endpoint | SQL>
        body: <payload>
        expect_status: <int>
    then:
      - status_code == <int>
      - response.<field> matches <regex>
      - response.<field> == <literal>
    and:
      - name: <cleanup>
        action: <HTTP DELETE | db DELETE>
    evidence:
      - <curl 输出文件路径>
      - <journalctl 片段路径>
```

---

## 写入规则

| 项 | 规则 |
|----|------|
| 文件命名 | `scenario-<track>.yaml`（**每个启用** track 一个） |
| 必填段 | `scenario_id` / `description` / `given` / `when` / `then` / `evidence` |
| 条件段 | `and`（强烈推荐，cleanup 必备）；`when[].body`（按需） |
| `critical` | `true` = 禁止 SKIP；`false` = 可记录 SKIPPED 后继续 |
| `_meta` | 自由字段，pg-build 会忽略，可保留也可删除 |
| 顺序 | 所有 `critical: true` 排在 `critical: false` 之前 |
| 数量 | 1-5 个；超出后提示用户拆分 |
| `evidence` 字段 | 写 `2-build/<report_seq>-<scenario_id>-evidence.json` 等**带占位符**的相对路径。LLM 只需把 `<scenario_id>` 替换为真实 id；`<report_seq>` 由 pg-build 编排器在 dispatch 时注入。scenario-execute agent 写盘时会按 `{report_seq}` 前缀拼接出最终绝对路径，避免多次派遣（首次 execute / fix 后重跑 execute）覆盖同 scenario 的历史 evidence |

---

## 相关文档

- pg-propose SKILL.md "scenario.yaml 生成指引"段
- pg-build scenario-execute agent SKILL.md
