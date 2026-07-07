# 成功/失败标准模板

## success_criteria 模板

```yaml
success_criteria:
  # SC-FORCE-1: 必含项，确认运行版本包含本次修复
  - id: SC-FORCE-1
    description: "修复代码已部署到运行中的服务（binary 版本包含本次修复）"
    verify_method: log_filter  # 或 api_call
    verify_args:
      service: <role>
      patterns: ["<fixed-symbol-or-class-name>"]
      expect_found: true
    timeout: 30s

  # 用户自定义标准（按 bug 性质选 1-3 条）
  - id: SC-1
    description: "<具体可测量标准>"
    verify_method: api_call  # / shell / log_filter / browser / test
    verify_args:
      method: GET  # 或 POST/PUT/DELETE
      url: <完整 API URL>
      expect_field: <response 字段名>
      expect_value: <期望值>
    timeout: <超时秒数>s

  - id: SC-2
    description: "<具体可测量标准>"
    verify_method: shell
    verify_args:
      cmd: "<shell 命令>"
      expect_match: "<期望输出 regex>"
    timeout: <超时秒数>s
```

## failure_criteria 模板

```yaml
failure_criteria:
  - id: FC-1
    description: "<什么算没修好>"
    verify_method: api_call  # / shell / log_filter
    verify_args:
      method: GET
      url: <完整 API URL>
      expect_field: <response 字段名>
      expect_value: <不应出现的值>
    timeout: <超时秒数>s

  - id: FC-2
    description: "后端日志包含 '<错误关键词>'"
    verify_method: log_filter
    verify_args:
      service: backend
      patterns: ["<错误关键词>"]
      expect_found: false  # 不应出现
    timeout: <超时秒数>s
```

## verify_method 类型对照

| 类型 | 适用场景 |
|------|---------|
| `api_call` | 后端接口返回特定值 |
| `shell` | virsh / kubectl / journalctl 等命令行 |
| `log_filter` | 关键事件日志匹配 |
| `browser` | 前端 UI 验证（用 chrome-devtools MCP）|
| `test` | 单元测试 |
| `e2e_flow` | 多步骤组合（API→命令行→日志）|

## SMART 准则

| 准则 | 含义 | 例子 |
|------|------|------|
| **S**pecific | 具体可测量 | "API 返回 code=0"（不是"API 正常"）|
| **M**easurable | 可被机械验证 | "virsh list 显示 running" |
| **A**chievable | 可达成 | 不要写"100% 完美" |
| **R**elevant | 与 bug 直接相关 | 不要列无关指标 |
| **T**ime-bound | 有时间窗口 | "30 秒内完成" |

## L1 路径特有 SC

L1 必须包含至少 1 条 `api_call`，expect `code=0`。这是 L1 强度的核心。

## L2 路径特有 SC

L2 必须包含至少 1 条 `shell` 或 `test`，且能用测试 DB / 集成测试验证。

## L3 路径特有 SC

L3 的 SC 限制较多：
- 可用 `test`（单元测试）
- 可用 `shell`（编译命令）
- 不可用 `api_call`（无运行中服务）
- 不可用 `log_filter`（无运行中服务）