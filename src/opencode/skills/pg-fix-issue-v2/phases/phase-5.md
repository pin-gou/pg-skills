# Phase 5: 验证（按 verify_level 分支）

**核心原则**：单元测试通过 ≠ 修复成功。编排器必须确保修复代码已**部署并通过端到端验证**。

## 入口检查（强制）

每次进入 Phase 5 前：

- [ ] 读 phase-progress.md，确认 verify_level
- [ ] 读 phase 4 files_changed，确认改动文件
- [ ] 读 iteration_count

## L1 路径（默认，最强）

适用：env 选 dev-local 或 multi-tier，`invoke-hook` 可用，运行中服务可重启。

```yaml
- [ ] S5-L1-1: invoke-hook --action start  # 编译 + 部署
- [ ] S5-L1-2: api_call 调用 metrics 接口  # expect code=0
- [ ] S5-L1-3: log_filter 验证运行版本含本次修复符号  # SC-FORCE-1
- [ ] S5-L1-4: 单元测试（affected_modules）
```

**L1 → L2 自动升级条件**：
- invoke-hook 返回非零退出码
- invoke-hook 完成后 health_check 失败
- 任一服务 30s 内未就绪

## L2 路径（中等强度）

适用：`invoke-hook` 不可用，但 maven 可用、测试 DB 可用。

```yaml
- [ ] S5-L2-1: mvn compile -pl <affected_module> -am
- [ ] S5-L2-2: mvn test -Dtest="*Integration*"
- [ ] S5-L2-3: 查测试 DB schema 验证（如本次 bug 涉及 DDL）
```

L2 修复成功判定 = 测试全绿 + DB schema 正确。

## L3 路径（兜底）

适用：仅有源码，无运行服务、无测试 DB。

```yaml
- [ ] S5-L3-1: mvn compile -pl <affected_module> -am
- [ ] S5-L3-2: checkstyle / lint
- [ ] S5-L3-3: 单元测试（不需要外部依赖的）
```

L3 修复成功判定 = 编译通过 + lint 干净 + 单元测试通过。

**L3 最终结论必须显式标注**：

> ⚠️ **本次修复使用 L3 验证（仅编译验证）。环境无法启动服务，端到端测试需要人工补跑。**

## 任意路径共通

```yaml
- [ ] S5-X-1: 收集本次 iteration 暴露的所有新问题
- [ ] S5-X-2: 更新 phase-progress.md.waterfall
- [ ] S5-X-3: 重算 completion_metrics
- [ ] S5-X-4: 更新 phase-progress.md.phases[5].status
```

## Phase 5 入口自检清单

- [ ] verify_level 已选定（L1/L2/L3）
- [ ] 当前路径所有 S5-X-Y 步骤已勾选
- [ ] SC-FORCE-1 已包含在 operations（仅 L1 路径）
- [ ] operations 列表至少包含 1 个 api_call 或 log_filter（L1）或 mvn test（L2/L3）

## L1 操作示例

```yaml
operations:
  - name: compile_and_start_backend
    type: invoke_hook   # 编排器在 Phase 5 开头调
    env: dev-local
    role: backend
    instance: backend-1
    action: start

  - name: verify_api_response
    type: api_call
    method: GET
    url: http://localhost:9080/api/compute.webvirt.../v3/hosts/<hostId>/metrics
    expect_field: code
    expect_value: 0

  - name: verify_running_version_contains_fix
    type: log_filter
    service: backend
    patterns: ["<fixed-symbol>"]   # 如 "noise_cpu_percent DOUBLE PRECISION"
    expect_found: true

  - name: run_unit_tests
    type: test
    module: backend
    test_key: unit
    output_mode: summary_plus_failures

  - name: verify_clean_diff
    type: git_diff_check
    forbid_markers: ["DIAG:"]
```

## L2 操作示例

```yaml
operations:
  - name: compile_module
    type: shell
    cmd: "mvn compile -pl <module> -am"

  - name: run_integration_tests
    type: test
    module: <module>
    test_key: integration

  - name: check_db_schema
    type: shell
    cmd: "psql ... -c \"SELECT column_name FROM information_schema.columns WHERE table_name='host_metrics_<suffix>' AND column_name='noise_cpu_percent'\""
    expect_match: "noise_cpu_percent"

  - name: verify_clean_diff
    type: git_diff_check
    forbid_markers: ["DIAG:"]
```

## L3 操作示例

```yaml
operations:
  - name: compile_module
    type: shell
    cmd: "mvn compile -pl <module> -am"

  - name: lint
    type: lint
    module: <module>

  - name: unit_tests
    type: test
    module: <module>
    test_key: unit

  - name: verify_clean_diff
    type: git_diff_check
    forbid_markers: ["DIAG:"]
```

## 不允许绕过 Executor

编排器**必须**派遣 executor 执行。禁止：
- 手 curl 验证
- 手读日志
- 手 git diff
- 手 mvn 命令

详细信息见 `reference/anti-patterns.md`。