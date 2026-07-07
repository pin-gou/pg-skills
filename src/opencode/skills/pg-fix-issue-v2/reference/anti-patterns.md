# 禁用模式（Anti-Patterns）

本文件列出**禁止行为**。主流程中不重复，引用本文件。

## A1: 禁止绕过 Executor

| 错误做法 | 正确做法 |
|---------|---------|
| 手 curl 验证 API | 构造 `api_call` operation 派遣 executor |
| 手读日志（journalctl / tail） | 构造 `log_filter` operation |
| 手 git diff / git log | 构造 `git_diff_check` operation |
| 手 mvn / go build | 走 Phase 5 的 mvn test operation（编译不属于验证，必须配合 test）|
| 手启服务（systemctl / docker） | 走 `invoke-hook --action start`（v3.0+ 协议）|

**经验教训**：executor 返回非预期结果（如 code 40001 而非 1001）时，编排器跳过 executor 直接手动 curl 验证是**最常见的绕过模式**。

正确做法：构造更精确的 `log_filter` 或 `api_call` operation 重派 executor，在 JSON 证据中确认。

## A2: 禁止手直接调用 hooks 脚本

```bash
# ❌ 禁止
bash .pg/hooks/role-backend-start.sh
bash .pg/hooks/env-dev-local-prepare.sh
```

```bash
# ✅ 正确：走 invoke-hook 协议
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \
  --session <S> --env <ENV> --role backend --instance backend-1 \
  --action start --skill pg-fix-issue
```

## A3: 禁止在 operations 中包含 service 启停

operations 列表**只包含**：模块命令（`test` / `lint`）+ 辅助验证（`shell` / `api_call` / `log_filter` / `git_diff_check`）。

service 启停**一律由编排器在 Phase 5 显式调 invoke-hook**（不进 operations 列表）。

## A4: 禁止硬编码示例值

```yaml
# ❌ 禁止
url: http://localhost:9080/api/...
service: backend-1
```

```yaml
# ✅ 正确：从 project.yaml 读
url: http://<service-host>:<port>/api/<module>.webvirt.../v3/...
service: <role>  # 来自 environments.<env>.roles[*].name
```

## A5: 禁止超过 3 处 DIAG 日志

```java
// ❌ 禁止：同时加 5 处 DIAG 调试
log.error("DIAG: 1");
log.error("DIAG: 2");
log.error("DIAG: 3");
log.error("DIAG: 4");
log.error("DIAG: 5");
```

规约：
- 位置：只打在入口/边界/消息分发器
- 数量：单次诊断不超过 3 处
- 形式：稳定前缀 `DIAG:`
- 生命周期：Phase 5b 验证通过后**必须清理**

## A6: 禁止假设单测通过 = 修复成功

**核心原则**：单元测试通过 ≠ 修复成功。

- 单测只验证代码逻辑
- 部署到运行中服务可能因环境差异失败
- DB schema、端口、依赖服务都可能有问题

必须按 verify_level 完成对应验证：
- L1: invoke-hook + api_call + log_filter
- L2: 集成测试 + DB schema
- L3: 编译 + lint + 单测（**最低限度**）

## A7: 禁止 executor 伪造结果

executor 在同 session 重复调用时可能伪造结果。防护：

1. 第二次重试派遣放到新 message 中
2. 编排器亲自交叉验证 1-2 条关键 operation
3. JSON 的 evidence 缺失或异常时判定伪造

## A8: 禁止手 mvn compile 作为验证

```bash
# ❌ 禁止：手 mvn compile 当作验证
cd webvirt-backend && mvn compile
```

mvn compile 只是编译，**不是验证**。必须配合 test：

```yaml
# ✅ 正确
- name: compile_and_test
  type: test
  module: backend
  test_key: unit
  output_mode: summary_plus_failures
```

## A9: 禁止跳过 phase-progress.md 更新

每次进入 phase **必须** read + write phase-progress.md。不更新就进下一阶段 = 状态丢失。

## A10: 禁止 L1 → L3 自动降级

L1 失败时**必须先升级到 L2**，不能直接降到 L3。理由：L1 失败说明环境基础设施有问题，L2 可能也跑不通。L3 是兜底，需要用户显式同意。

## A11: 禁止 root_cause 模糊归类

```yaml
# ❌ 禁止
root_cause: "未知"
root_cause: "其他"
root_cause: "看代码"

# ✅ 正确：具体到文件和列
root_cause: "MetricsSlotRouter.java:330 DDL 缺 noise_cpu_percent"
```

模糊归类会导致强制终止条件 `same_root_cause_threshold` 失效。

## A12: 禁止 exec "complete" 但 iterator != done

```yaml
# ❌ 禁止：谎报状态
phases:
  - id: 5
    status: completed  # 但实际还在 L1-3 步骤
    steps_pending: [S5-L1-2, S5-L1-3, S5-L1-4]  # 矛盾
```

`status` 与 `steps_completed/steps_pending` 必须一致。

## A13: 禁止手 git push / 手 commit

修复完成后，**禁止编排器**手 git push。手 commit 仅在用户显式要求时执行。详见 `using-agent-skills` 中的 git-workflow-and-versioning。

## A14: 禁止在前端浏览器中未硬刷新

前端问题**必须**要求用户在 Phase 3 前完成硬刷新（Ctrl+Shift+R），否则 Phase 5 验证可能因前端缓存失败。