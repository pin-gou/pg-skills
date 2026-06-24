# Proposal Templates

本文档定义 `proposal.md` 的默认模板与 `proposal_rules` 的注入机制。

---

## 默认模板

路径：`.pg/changes/<change-name>/proposal.md`

```markdown
# {change-name}
**关联 issue**：{issue 编号或链接，暂无则写"无"}
**变更类型**：{bugfix | feature | refactor | chore}

## 背景
{为什么需要这个变更}

## 目标
{要解决什么问题、达到什么效果}

## 范围
### 包含
{本变更要做的事}

### 不包含
{明确不做的事}

## 方案概述
{简要描述技术方案}

## 风险和注意事项
{潜在风险、注意事项}

**约束**：本节列出的每条风险，design.md 的 Verification Criteria 必须有至少一条 V-* 能验证它。如果某条风险无法直接验证（例如"产品反馈渠道收集意见"），需明确说明验证方式。
```

> **注意**：上面的 `## 风险和注意事项` 段是默认模板的最后一个二级标题。
> `proposal_rules` 可通过 `after_section` 字段在此标题之后插入自定义章节，
> 也可不指定 `after_section` 而直接追加到模板尾部。

---

## proposal_rules 注入机制

### 字段约定

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | ✅ | 规则唯一标识（v1 不做唯一性校验，建议语义化） |
| `after_section` | ❌ | 插入到 `proposal.md` 哪个二级标题之后（如 `风险和注意事项`）。缺省时追加到模板尾部 |
| `template` | ✅ | 实际注入的 markdown 文本 |

### 注入算法（伪代码）

```python
# 伪代码：实际由 LLM 在生成 proposal.md 时按以下规则改写模板
proposal_template = "<上文的默认模板>"
proposal_text = proposal_template

for rule in config.get("proposal_rules", []):
    if rule.get("after_section"):
        # 把 rule.template 插到 proposal_text 中 "## {after_section}" 标题之后
        proposal_text = insert_after_heading(
            proposal_text, f"## {rule['after_section']}", rule["template"]
        )
    else:
        # 缺省 after_section:追加到模板尾部
        proposal_text += "\n" + rule["template"]
```

### 示例

`.pg/project.yaml` 当前注册一条 capability 评估规则：

```yaml
proposal_rules:
  - id: capability_assessment
    after_section: 风险和注意事项
    template: |
      ## Capability 影响评估

      **本节为涉及 capability 演进时必填；若本次变更不涉及 capability，可写"无"并附简要说明。**

      - [ ] 本次变更涉及 capability 新增吗？（是 / 否）
      - [ ] 本次变更涉及 capability 废弃吗？（是 / 否）
      - [ ] 本次变更涉及 capability 重命名/数值变更吗？（是 / 否）
      - [ ] 本次变更涉及 backend action 新增吗？（是 / 否）
      - [ ] 本次变更涉及 action → capability 映射变化吗？（是 / 否）

      如果以上任一为"是"，请在以下表格中列出：

      | Capability / Action | 变更类型 | 涉及端（proto / Java / Go / yaml） |
      |---------------------|---------|-----------------------------------|
      | {name}              | 新增/废弃/修改 | {端列表} |

      **Capability 一致性验证**：本次变更必须在 verify 阶段运行 `python3 <module-dir>/scripts/check-capability-consistency.py`，结果作为 PR 准入条件。
```

生成 `proposal.md` 时，模板自动在"## 风险和注意事项"标题后插入该章节，LLM 只需填充内容。

---

## 约束

- `proposal_rules` 只影响 `proposal.md` 模板；`design.md` / `tasks.md` 的扩展留待后续 change
- 规则冲突（多条 rule 同 `after_section`）时按 config.yaml 出现顺序插入
- `template` 文本原样插入，不做任何转义
- 使用中文撰写 proposal
- 保持简洁，聚焦 why 和 what
- 详细技术说明留给 design.md

---

## 相关文档

- 字段索引：[./config-fields.md](./config-fields.md)
- design 模板：[./design-templates.md](./design-templates.md)
- tasks 模板：[./tasks-templates.md](./tasks-templates.md)