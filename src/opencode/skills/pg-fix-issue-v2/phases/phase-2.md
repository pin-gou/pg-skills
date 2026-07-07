# Phase 2: 规划复现步骤 + 成功标准

## 必做动作（顺序固定）

- [ ] **S2-1**: 更新 phase-progress.md `current_phase: 2`
- [ ] **S2-2**: 定义 reproduction_steps（≥3 步，可机械执行）
- [ ] **S2-3**: 定义 success_criteria（≥1 条，含 SC-FORCE-1）
- [ ] **S2-4**: 定义 failure_criteria（≥1 条）
- [ ] **S2-5**: 输出 phase2_output 到 `.pg/fix-issue/<session>/phase2.md`
- [ ] **S2-6**: 更新 phase-progress.md `phases[2].status: completed`

## S2-2: reproduction_steps 要求

- 至少 3 步，每步可机械执行
- 前端问题必须包含「浏览器 DevTools 观察」操作
- 步骤不依赖阅读代码推测
- 必须用 `pg-parse-config.py` 解析的命令

## S2-3: success_criteria 要求

每条必须是 SMART 准则（Specific/Measurable/Achievable/Relevant/Time-bound）。

**强制必含项 SC-FORCE-1**：

```yaml
- id: SC-FORCE-1
  description: "修复代码已部署到运行中的服务（binary 版本包含本次修复）"
  verify_method: log_filter  # 或 api_call
  verify_args:
    service: <role>                              # 来自 project.yaml environments.<env>.roles[*].name
    patterns: ["<fixed-symbol-or-class-name>"]   # 本次修复引入的独有可识别符号
    expect_found: true
  timeout: 30s
```

支持类型：

| 类型 | verify_method |
|------|--------------|
| API 响应字段 | `api_call` |
| 命令行输出 | `shell` |
| 日志匹配 | `log_filter` |
| 前端 UI | `browser` |
| 单元测试 | `test` |
| 端到端流 | `e2e_flow` |

**L1 路径特有 SC**：必须包含至少 1 条 `api_call`，且 expect `code=0`。

## S2-4: failure_criteria 要求

至少 1 条，描述「什么算没修好」。优先级**高于** success_criteria：

```yaml
- id: FC-1
  description: "metrics API 仍返回 1006"
  verify_method: api_call
  verify_args:
    method: GET
    url: /api/compute.../hosts/{hostId}/metrics
    expect_field: code
    expect_value: 0
```

## Phase 2 自检清单

进入 Phase 3 前**必须全部满足**：

- [ ] reproduction_steps 至少 3 步，每步可机械执行
- [ ] 前端问题包含「浏览器 DevTools 观察」操作
- [ ] 至少 1 条 success_criteria
- [ ] 每条 criteria 满足 SMART 准则
- [ ] 至少 1 条 failure_criteria
- [ ] bug 根因所在的文件已确认
- [ ] 所有周边文件已识别

## Phase 2 输出 phase2.md

写到 `.pg/fix-issue/<session>/phase2.md`，格式：

```yaml
phase2_output:
  reproduction_steps:
    - step: 1
      action: "..."
      command: "..."
    - step: 2
      action: "..."
      command: "..."
    - step: 3
      action: "..."
      command: "..."
  
  success_criteria:
    - id: SC-FORCE-1
      ...
    - id: SC-1
      ...
    - id: SC-2
      ...
  
  failure_criteria:
    - id: FC-1
      ...
```