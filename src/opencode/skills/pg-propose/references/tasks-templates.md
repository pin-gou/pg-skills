# Tasks Templates

本文档定义 `tasks.md` 的生成算法与各子章节模板。
tasks.md 的章节顺序由 **stages × tracks** 二维展开驱动。

> **编排模型**：见 [./orchestration-model.md](./orchestration-model.md)
> **environment 选择**：见 `.pg/changes/<change>/environment.yaml`（SSOT），不再由 tasks.md 承载

---

## 生成算法（stages × tracks 二维展开）

```python
# 阶段零：决定哪些 stage 实际启用（受 on_conditions 控制）
# 见 references/orchestration-model.md「on_conditions & stage 动态启用」段
enabled_stages = []
for stage in config.stages:
    on_conditions = stage.get("on_conditions")
    if not on_conditions:
        # 无 on_conditions = 常驻 stage，永远启用
        enabled_stages.append(stage)
        continue
    # LLM 推理（pg-propose 阶段 2c.5）
    if any(evaluate_condition(c, affected_paths, proposal_text) for c in on_conditions):
        enabled_stages.append(stage)

# 阶段一：按 enabled_stages 顺序、每个 stage 的 tracks 顺序生成章节
N = 1
chapters = []

for stage in enabled_stages:  # 改用 enabled_stages 而非 config.stages
    for track_id in stage.tracks:
        # ---- 关键改动: 按 track 类型分流 ----
        # standard track: 走 4 子章节模板 (test/dev/verify/gate)
        # simple track:   走 1 个 simple 章节模板 (派遣 pg-build/simple agent 执行 commands)
        # 详见 orchestration-model.md「Track 类型」段
        track_cfg = (config.get("tracks") or {}).get(track_id) or {}
        is_simple = track_cfg.get("type") == "simple"

        if is_simple:
            # simple track: 无论是否在 affected_tracks 中, 都生成 1 个章节
            # runner 在 cmd_next 时会改写为 canonical form (已对齐)
            chapters.append(generate_simple_chapter(N, track_id, stage))
            N += 1
            continue

        # 标准 track: 检查是否在 affected_tracks (real-integration 总是算)
        is_affected = track_id in affected_tracks or track_id == "real-integration"
        if is_affected:
            # 每个 track 生成 4 个子章节
            chapters.append(generate_chapter(N, track_id, stage, "test"))
            N += 1
            chapters.append(generate_chapter(N, track_id, stage, "dev"))
            N += 1
            chapters.append(generate_chapter(N, track_id, stage, "verify"))
            N += 1
            chapters.append(generate_chapter(N, track_id, stage, "gate"))
            N += 1
        else:
            # 未改动 track：每个子章节写 "- 无"
            for sub in ["test", "dev", "verify", "gate"]:
                chapters.append(generate_empty_chapter(N, track_id, sub))
                N += 1

# 阶段二：final-gate 章节强制追加
chapters.append(generate_final_gate(N))
N += 1
```

### 关键变化（v3.0 升级）

- **新增阶段零** `enabled_stages`：根据 `on_conditions` 过滤 config.stages
- **无 on_conditions 的 stage 视为常驻**，永远生成章节
- **有 on_conditions 的 stage** 仅当 LLM 推理命中规则时才生成章节
- tasks.md 章节顺序由 enabled_stages 顺序决定，**不再等同于 config.yaml.stages 数组顺序**
- runner 按 tasks.md 实际章节执行，与 config.stages 数组解耦

### v3.1 升级（simple track 分流）

- **新增 track 类型分流**：生成算法增加 `is_simple` 判断
- simple track 在 tasks.md 中**只占 1 个章节号**（替代原来 4 个 - 无 占位）
- canonical form 一步到位生成（heading 含 `(simple track: 派遣 pg-build/simple agent 执行 commands)` + body 单 `- 无` 行），与 runner 的 `_noopify_simple_track_sections` canonical form 完全对齐
- validator 仍 `valid: true`（`_is_simple_track()` 检测 simple type 后列入 `skipped_items`，不强制要求 4 子章节）

### 关键变化（v3.0 升级）

- **新增阶段零** `enabled_stages`：根据 `on_conditions` 过滤 config.stages
- **无 on_conditions 的 stage 视为常驻**，永远生成章节
- **有 on_conditions 的 stage** 仅当 LLM 推理命中规则时才生成章节
- tasks.md 章节顺序由 enabled_stages 顺序决定，**不再等同于 config.yaml.stages 数组顺序**
- runner 按 tasks.md 实际章节执行，与 config.stages 数组解耦

### on_conditions 触发的 stage 模板

若 enabled_stages 含 `prepare-env-scripts`，按以下模板生成 4 章：

```markdown
## 1. prepare-env-scripts.env-scripts:test - prepare-env-scripts 测试先行（unit）

- [ ] 1.1 编写最小 SQL fixture + 验证 psql --dry-run 通过 (红)
- [ ] 1.2 在 setup 脚本新增 step placeholder + 验证 shellcheck 失败 (红)

## 2. prepare-env-scripts.env-scripts:dev - 实现开发

- [ ] 2.1 创建 fixtures/*.sql
- [ ] 2.2 改造 dev-local-setup.sh
- [ ] 2.3 同步 dev-3tier-setup.sh

## 3. prepare-env-scripts.env-scripts:verify - prepare-env-scripts 集成验证

- [ ] 3.1 执行 lint（runner 通过 modules.env-scripts.lint 注入命令）
- [ ] 3.2 执行测试（runner 通过 modules.env-scripts.test.unit 注入命令）
- [ ] 3.3 启动服务：runner 按 environment.yaml 中 prepare-env-scripts: <env> 启动（若 required=true）
- [ ] 3.4 验证 V-env-scripts-N：来自 design.md 的 Verification Criteria

## 4. prepare-env-scripts.env-scripts:gate - prepare-env-scripts 门控审查

- [ ] 4.1 gate agent 读取 verification report 证据
- [ ] 4.2 gate agent 审计 shellcheck 输出与 SQL fixtures 安全性
- [ ] 4.3 gate agent 检查 pg-spec-deprecated/scripts/ 改动范围（白名单约束）
- [ ] 4.4 gate agent 输出 Gate Assessment
```

### 核心规则

- **environment 选择已由 environment.yaml 决定**
- 章节编号 N 从 1 开始顺序递增
- 每个 track 生成 4 个子章节：`test`、`dev`、`verify`、`gate`
- 每个章节使用 `## <N>. {stage.name}.{track_id}:{sub} - <label>` 格式
- 任务编号使用 `- [ ] <N>.<M>` 格式（N=章节号，M=任务序号，从 1 开始）
- 不在 `affected_tracks` 中的 track 所有任务写 `- 无`
- 所有 track 结束后必须追加 `final-gate` 章节（用于归档前对跨 stage 依赖项的最终审查）

---

## 环境选择产物：environment.yaml

per-change environment 选择由
`.pg/changes/<change>/environment.yaml` 承载（详见 [./orchestration-model.md](./orchestration-model.md)「per-change environment 选择」段）。

---

## 各子章节模板

### track:test（测试先行）

```markdown
## {N}. {stage.name}.{track_id}:test - {stage.name} 测试先行（{stage.test_key}）

- [ ] {N}.1 编写 {stage.test_key} 测试：{具体测试场景描述}
```

未改动的 track 该章节写：

```markdown
## {N}. {stage.name}.{track_id}:test - {stage.name} 测试先行（{stage.test_key}）

- 无
```

### track:dev（实现开发）

```markdown
## {N}. {stage.name}.{track_id}:dev - 实现开发

- [ ] {N}.1 实现具体功能描述
- [ ] {N}.2 补充相关逻辑
```

**删除前置检查**：涉及删除字段、方法、接口、API 时，必须先执行 grep/rg 全项目扫描确认无外部引用，验证后再执行删除。建议在 dev 章节前置位置加一条：

```markdown
- [ ] {N}.1 grep 扫描确认无外部引用：`rg "<待删除符号名>" <module-dir>/ <other-module-dir>/` 仅返回本次变更范围内的引用
- [ ] {N}.2 删除具体功能描述
```

未改动的 track 该章节写：

```markdown
- 无
```

### track:verify（集成验证）

运行顺序随 stage 不同：
- `dev-isolated`（test_key=unit）：lint → unit test（不启动服务）
- `dev-mock-integration`（test_key=integration）：lint → integration test（runner 部署 role 后）
- `real-integration`（test_key=e2e）：lint → e2e test（runner 部署 full env 后）

实际执行的命令来自 `modules.<module_name>.test.<test_key>` + `modules.<module_name>.lint`，由 runner 通过 `stage_test_key` 和 `stage_test_commands` 注入。

tasks.md 的 verify 阶段只需写：

```markdown
## {N}. {stage.name}.{track_id}:verify - {stage.name} 集成验证

- [ ] {N}.1 执行 lint（runner 通过 modules.<track.modules>.lint 注入命令）
- [ ] {N}.2 执行测试（runner 通过 modules.<track.modules>.test.<{stage.test_key}> 注入命令）
- [ ] {N}.3 启动服务：
  - 常规 stage：runner 按 environments.<stage.env_name>.roles.<role>.actions.start 查找并执行
  - real-integration：runner 按 environments.<stage.env_name>.actions.verify 查找并执行
- [ ] {N}.4 验证 V-{track_id}-N：来自 design.md 的 Verification Criteria

  **Evidence 要求**（verify agent 在验证报告中产出，gate agent 据此评审）：
  - 每个 V-* 必须有对应的原始输出（curl 响应 / 命令行输出 / 日志片段）
  - SKIP 的 V-* 必须注明豁免理由
  - 测试结果（Tests run: N, Failures: 0, Errors: 0）必须有日志摘要
```

> ⚠️ **常见错误**：不要写成 `${注入路径}：\`实际命令\`` 的形式。runner 不读取 tasks.md 中的 shell 命令，只按 config.yaml 查找并执行。lint/test/start 等步骤的任务应只写 placeholder 引用，禁止在后面追加具体脚本路径或 shell 命令。

未改动的 track 该章节写：

```markdown
- 无
```

### track:gate（门控审查）

gate 阶段由编排器在 verify 阶段 PROCEED 后自动执行。仅包含审查任务，**不启动服务、不执行代码**。

```markdown
## {N}. {stage.name}.{track_id}:gate - {stage.name} 门控审查

- [ ] {N}.1 gate agent 读取 verification report 证据
- [ ] {N}.2 gate agent 审计 design.md 一致性（API / DTO / 数据模型 / 组件）
- [ ] {N}.3 gate agent 检查 scope creep
- [ ] {N}.4 gate agent 输出 Gate Assessment 到独立文件 `{track}-{stage}-gate-assessment.md`
```

未改动的 track 该章节写：

```markdown
- 无
```

### simple track 章节模板

simple track（`tracks.<id>.type == "simple"`）在 tasks.md 中**只占 1 个章节号**，且不走 4 子章节（test/dev/verify/gate）模板。本模板与 runner 的 canonical form 完全对齐——runner 在 cmd_next 时调用 `_noopify_simple_track_sections`，检测到非 canonical 时会改写为同一形式（idempotent）。

**章节标题**：

```markdown
## {N}. {stage.name}.{track_id} - {stage.name} {track_id}  (simple track: 派遣 pg-build/simple agent 执行 commands)
```

**注意 heading 中的双空格**：`{track_id}  (simple track...` 中 `{track_id}` 与 `(simple track` 之间是 **2 个空格**（runner noopify 实现的精确格式，见 `pg-build/scripts/pg-pipeline-runner.py:1541`）。

**章节正文**：

```markdown
- 无
```

**完整 simple track 章节样例**：

```markdown
## 9. dev.openapi-gen - dev openapi-gen  (simple track: 派遣 pg-build/simple agent 执行 commands)

- 无
```

**说明**：
- 章节标题保留 `{stage.name}.{track_id}` 前缀（与 standard track 一致），便于 validator 解析
- 不参与 `lint` / `test` / `verify` / `gate` 四阶段
- runner 派遣 `pg-build/simple` agent 执行 `tracks.{track_id}.commands`，按命令的 `on_failure` 处理结果（`fail` / `continue` / `retry`）
- 详见 [./orchestration-model.md](./orchestration-model.md)「Track 类型」段
- validator 检测到 `tracks.<id>.type == "simple"` 时，将其列入 `skipped_items` 而不强制要求存在 4 子章节；本模板兼容该行为

---

## 完整示例

假设 `affected_tracks = [backend, frontend]`，按 3 stages 输出。

### 场景 A：仅 backend + frontend 改动（无 agent）

```markdown
## 1. dev-isolated.backend:test - dev-isolated 测试先行（unit）

- [ ] 1.1 编写 unit 测试：验证业务正常创建和异常处理

## 2. dev-isolated.backend:dev - 实现开发

- [ ] 2.1 新增 Xxx 数据模型
- [ ] 2.2 实现 Xxx 业务逻辑

## 3. dev-isolated.backend:verify - dev-isolated 集成验证

- [ ] 3.1 执行 lint（runner 通过 modules.backend.lint 注入命令）
- [ ] 3.2 执行测试（runner 通过 modules.backend.test.unit 注入命令）
- [ ] 3.3 验证 V-backend-1：来自 design.md 的 Verification Criteria
  **Evidence 要求**：每个 V-* 必须有原始 curl 输出；SKIP 注明豁免理由

## 4. dev-isolated.backend:gate - dev-isolated 门控审查

- [ ] 4.1 gate agent 读取 verification report 证据
- [ ] 4.2 gate agent 审计 design.md 一致性
- [ ] 4.3 gate agent 检查 scope creep
- [ ] 4.4 gate agent 输出 Gate Assessment

## 5. dev-isolated.agent:test - dev-isolated 测试先行（unit）

- 无    # agent 未改动

## 6. dev-isolated.agent:dev - 实现开发

- 无

## 7. dev-isolated.agent:verify - dev-isolated 集成验证

- 无

## 8. dev-isolated.agent:gate - dev-isolated 门控审查

- 无

## 9. dev-isolated.frontend:test - dev-isolated 测试先行（unit）

- [ ] 9.1 编写 frontend 单元测试：列表组件渲染

## 10. dev-isolated.frontend:dev - 实现开发

- [ ] 10.1 新增 Xxx 组件

## 11. dev-isolated.frontend:verify - dev-isolated 集成验证

- [ ] 11.1 执行 lint（runner 通过 modules.frontend.lint 注入命令）
- [ ] 11.2 执行测试（runner 通过 modules.frontend.test.unit 注入命令）
- [ ] 11.3 验证 V-frontend-1

## 12. dev-isolated.frontend:gate - dev-isolated 门控审查

- [ ] 12.1 gate agent 读取 verification report 证据
- [ ] 12.2 gate agent 审计 design.md 一致性（组件 / 路由 / API 调用）
- [ ] 12.3 gate agent 检查 scope creep
- [ ] 12.4 gate agent 输出 Gate Assessment

## 13. dev-mock-integration.backend:test - dev-mock-integration 测试先行（integration）

- [ ] 13.1 编写 integration 测试：POST 后 GET 验证数据落库

## 14. dev-mock-integration.backend:dev - 实现开发

- （dev 任务已在 dev-isolated.backend:dev 章节完成，此处不重复）

## 15. dev-mock-integration.backend:verify - dev-mock-integration 集成验证

- [ ] 15.1 执行 lint
- [ ] 15.2 执行测试（runner 通过 modules.backend.test.integration 注入命令）
- [ ] 15.3 启动 backend：runner 通过 environments.<stage.env_name>.roles.backend.actions.start
- [ ] 15.4 验证 V-backend-3（mock 联调场景的 V-*）

## 16. dev-mock-integration.backend:gate - dev-mock-integration 门控审查

- [ ] 16.1 gate agent 读取 verification report 证据
- [ ] 16.2 gate agent 审计 design.md 一致性
- [ ] 16.3 gate agent 输出 Gate Assessment

## 17. dev-mock-integration.agent:test

- 无

## 18. dev-mock-integration.agent:dev

- 无

## 19. dev-mock-integration.agent:verify

- 无

## 20. dev-mock-integration.agent:gate

- 无

## 21. dev-mock-integration.frontend:test - dev-mock-integration 测试先行（integration）

- [ ] 21.1 编写 mock 联调场景的 frontend 测试

## 22. dev-mock-integration.frontend:dev

- （dev 任务已在 dev-isolated.frontend:dev 章节完成）

## 23. dev-mock-integration.frontend:verify - dev-mock-integration 集成验证

- [ ] 23.1 执行 lint
- [ ] 23.2 启动 frontend
- [ ] 23.3 验证 V-frontend-2

## 24. dev-mock-integration.frontend:gate - dev-mock-integration 门控审查

- [ ] 24.1 gate agent 读取 verification report
- [ ] 24.2 gate agent 输出 Gate Assessment

## 25. real-integration:verify - real-integration 真机联调

- [ ] 25.1 runner 通过 environments.<stage.env_name>.actions.verify 启动 verify 脚本
- [ ] 25.2 验证跨模块 V-*：所有 track 的 real-integration V-* 集合

## 26. final-gate - 最终门控审查

- [ ] 26.1 收集所有 stage 的 Gate Assessment
- [ ] 26.2 检查跨 stage 依赖项
- [ ] 26.3 输出 Final Gate Assessment
```

> **重要说明**：上面每个章节的标题都带 `stage.name`，便于一眼区分 dev-isolated / dev-mock-integration / real-integration 阶段。同一 track 在不同 stage 的 verify 阶段对应不同 V-*（dev-isolated 的 V-backend-1 测单测场景，dev-mock-integration 的 V-backend-3 测 mock 联调场景）。

---

## 约束

- 使用中文撰写
- 任务编号必须使用 `- [ ] X.Y` 格式（X=章节号，Y=任务序号），禁止使用 markdown 标题或其他格式
- verify 阶段严格顺序：lint → test → start service → verify，不能调换
- verify 阶段中必须执行启动脚本（不论端口是否已被占用）
- **verify 步骤禁止写入具体命令**：`（runner 通过 modules.<m>.xxx 注入命令）` 和 `按 environments.<env>.roles.<role>.actions.xxx 查找并执行` 是 placeholder 引用，由 runner 按 config.yaml 解析实际命令。禁止在后面追加 `：具体命令` 格式的展开内容。
- 每个 track 的 verify 阶段必须包含 lint 验证（`modules.<m>.lint`）作为独立编号任务；**且末尾必须含 Evidence Block 占位**
- verify 阶段最后一个 V-* 验证任务后必须插入 Evidence 要求注释（详见 track:verify 模板）
- gate 阶段不执行任何命令，纯审查（由编排器派遣 gate agent 完成）
- final-gate 章节不包含具体的编程任务，仅作为编排器归档前的执行标记
- 测试任务必须包含强断言。**验证要求示例**：
  - 创建成功后：必须验证数据出现在列表，不只是验证消息
  - 删除后：必须验证数据从列表消失
  - 每个测试任务必须包含错误捕获和诊断信息记录
- **proposal 风险覆盖**：proposal.md "风险和注意事项" 章节列出的每条风险，design.md 的 Verification Criteria 必须有至少一条 V-* 能验证它；tasks.md 的 verify 阶段必须把这些 V-* 落到对应 track × stage 的验证任务中
- **删除前置检查**：track:dev 涉及删除字段/方法/接口时，必须先 grep 全项目扫描确认无外部引用，验证后再执行删除（见 track:dev 模板）

---

## validate-fix 循环参考

tasks.md 生成后，pg-propose 阶段 2e.5 自动执行可消费性验证。

**验证工具**：`python3 .opencode/skills/pg-build/scripts/pg-validate-tasks.py validate <change>`

验证范围：章节存在性、heading 格式、sub 命名、编号连续性——纯结构验证，不检查内容语义。

循环最多重试 2 次，第 3 次仍失败时将残留 error 记录到 review-notes「阻塞」段。

---

## 相关文档

- 字段索引：[./config-fields.md](./config-fields.md)
- 编排模型：[./orchestration-model.md](./orchestration-model.md)
- proposal 模板：[./proposal-templates.md](./proposal-templates.md)
- design 模板：[./design-templates.md](./design-templates.md)
