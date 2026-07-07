# Verify Levels 详细定义

## L1: 完整部署验证（默认，最强）

**适用条件**：
- env 选 `dev-local` 或 `multi-tier`
- `invoke-hook` 可用
- 运行中服务可重启

**前置检查清单**：
- [ ] `.pg/hooks/role-backend-start.sh` 存在
- [ ] PostgreSQL 运行中
- [ ] Redis 运行中（如果用得到）
- [ ] gRPC agent 可连

**必做序列**：

```yaml
- [ ] S5-L1-1: invoke-hook --action start
      ↓ 等待服务就绪（health_check 返回 200）
- [ ] S5-L1-2: api_call 调用核心 metrics 接口
      ↓ expect code=0, expect_field=data 不为 null
- [ ] S5-L1-3: log_filter 验证运行版本含本次修复符号
      ↓ expect_found=true
- [ ] S5-L1-4: 单元测试（如有 affected_modules）
```

**L1 → L2 自动升级触发**（任一）：

1. `invoke-hook` 返回非零退出码
2. `invoke-hook` 完成后 health_check 失败（30s 内未就绪）
3. invoke-hook 调用超时
4. 端口被占用（executor 自决重试 1 次后仍失败）

**升级时**：在 phase-progress.md 记录：

```yaml
phases:
  - id: 5
    status: in_progress
    verify_level: L2  # 从 L1 升级
    level_upgrade_reason: "invoke-hook 启动超时"
    steps_completed: [S5-L1-1]  # 原 S5-L1-1 标记为 completed 但失败
```

**禁止 L1 → L3 自动降级**：必须在 phase-progress.md 显式记录升级到 L2 的原因。

## L2: 编译+集成测试（中等强度）

**适用条件**：
- `invoke-hook` 不可用（env 没启动服务）
- maven 可用
- 测试 DB 可用（docker-compose.test.yml）

**必做序列**：

```yaml
- [ ] S5-L2-1: mvn compile -pl <affected_module> -am
      ↓ 编译干净，无 error
- [ ] S5-L2-2: mvn test -Dtest="*Integration*"
      ↓ 集成测试全绿（需要 docker-compose.test.yml）
- [ ] S5-L2-3: 查测试 DB schema 验证（如本次 bug 涉及 DDL）
      ↓ SELECT column_name FROM information_schema.columns
```

**L2 修复成功判定**：
- 编译通过
- 集成测试全绿
- DB schema 与修复预期一致

**L2 适用 bug 类型**：
- DDL 变更（新增列、新增表）
- 跨模块集成问题
- 涉及测试 DB 的功能

## L3: 仅编译验证（兜底）

**适用条件**：
- 仅有源码，无运行服务
- 无测试 DB
- 仅有 maven / gradle

**必做序列**：

```yaml
- [ ] S5-L3-1: mvn compile -pl <affected_module> -am
- [ ] S5-L3-2: checkstyle / lint
- [ ] S5-L3-3: 单元测试（不需要外部依赖）
```

**L3 修复成功判定**：
- 编译通过
- lint 干净
- 单元测试全绿（限不依赖外部服务的）

**L3 最终结论必须显式标注**：

> ⚠️ **本次修复使用 L3 验证（仅编译验证）。环境无法启动服务，端到端测试需要人工补跑。**

**L3 适用 bug 类型**：
- 纯前端 bug
- 文档/注释 typo
- 简单逻辑错误（可被单元测试覆盖）

## L1 vs L2 vs L3 决策矩阵

| 环境状态 | 推荐 |
|---------|------|
| 有 env 启动脚本 + DB 可连 | L1 |
| 无 env 启动 / DB 不可连 / 网络受限 | L2 |
| 仅本地代码，无外部依赖 | L3 |

## 自动降级保护

L1 → L3 自动降级**禁止**。必须经过 L2 中转：
- L1 失败 → 升级到 L2
- L2 失败 → 升级到 L3（必须显式记录）

理由：L1 失败说明环境基础设施有问题，L2 集成测试可能也跑不通（同样的基础设施依赖）。L3 是兜底，需要用户显式同意。

## verify_level 与 success_criteria 关系

| verify_level | api_call | log_filter | shell | test |
|--------------|----------|------------|-------|------|
| L1 | ✅ 必须 | ✅ 必须 | ✅ | ✅ |
| L2 | ⚠️ 不要求 | ⚠️ 不要求 | ✅ | ✅ 必须 |
| L3 | ❌ | ❌ | ✅ | ✅ 必须 |

L3 路径不可用 `api_call` 和 `log_filter`，因为没有运行中服务。