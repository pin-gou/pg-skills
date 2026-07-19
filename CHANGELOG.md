# 变更日志

所有对 pg-skills 的重要变更均记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

<!-- 下一版本在此累积 -->
<!-- 将 VERSION 推进到 0.8.3，分析 git log / git diff，更新 CHANGELOG.md README.md -->

## [0.8.3] - 2026-07-19

### 新增

- **pg-build 集成验证不可 SKIP**：基于 `stages[*].environment.required` 字段，集成验证环节不再允许被 SKIP，确保跨环境依赖必须满足才能继续 pipeline
- **pg-propose 强制 API 端点文档完整性**：`design.md` 中的 API 端点定义必须包含完整的 Request/Response Body 结构，由 `pg-validate-proposal.py` 在校验阶段强制检查，缺失时输出 `api_endpoint_incomplete` 错误码
- **pg-build scenario-fix drift.md 记录**：scenario-fix 在诊断后输出 `drift.md` 文档，记录 design 偏移、根因分析和修复方案，实现 scenario-fix 全流程可追溯
- **pg-build scenario track 浏览器操作支持**：scenario-execute 中新增浏览器操作步骤（点击、输入、截图等），支持前端集成场景的自动化验证
- **pg-build workflow_failed 后用户可选 reset/resume**：`workflow_failed` 状态下重新执行时，由用户选择是 reset（清空状态重来）还是 resume（从失败点继续），提升容错灵活性
- **pg-build 启动前脏分支检查**：`pg-build` 启动前先检查当前分支是否不干净，脏分支时提示用户提交或 stash，避免意外覆盖
- **AGENTS.md**：新增 AGENTS.md 文档，作为 pg-skills 的 AI 开发工作流共享能力层说明文档，涵盖架构概览、SSOT 规则、Agent 协议摘要、开发指南、错误排查等
- **skills 介绍文档**：`docs/pg-skills.md` + 12 张 SVG 卡片（`docs/cards/`），系统介绍品构技能集的理念、痛点、支柱、SSOT、Hook 协议、上手流程等
- **pg-propose 防御越界修改**：`pg-propose-review` 在修改时校验文件路径是否在 change 目录范围内，越界操作自动拒绝，保护项目其他文件不被意外修改
- **pg-propose 移除日期前缀**：新建变更时不再自动添加日期前缀，变更目录名更简洁

### 变更

- **`build.injections` → `propose.injections` 重命名**：`project.schema.json` 及相关脚本中 `build.injections` 字段重命名为 `propose.injections`（propose 阶段注入，build 阶段消费），语义更准确
- **`test_strategy` / `coding_standards` 字段清理**：`project.schema.json` 中移除废弃的 `test_strategy` 和 `coding_standards` 字段，减少配置噪声
- **`project.schema.json` 废弃字段清理**：移除 `build.injections`（已重命名）、`test_strategy`、`coding_standards` 等废弃字段，schema 定义更精简
- **pg-build SKILL 文档对齐**：SKILL.md 文档与实际 reducer 行为保持一致，修复文档与代码的偏差
- **pg-1-define.md 更新**：`/1-pg-define` 命令文档更新，约束说明更清晰
- **Python 3.7 兼容**：`pg-parse-config.py` 等脚本兼容 Python 3.7（移除 `rst` 依赖等）
- **工具编辑器适配**：`tools/project-editor` 移除 `CodingStandardsSection.vue`、`TestStrategySection.vue` 及对应字段，与 `project.schema.json` 清理同步

### 修复

- **pg-build `git.default_branch` 配置读取**：修复 pg-build 读取 `git.default_branch` 配置时的键名解析错误
- **测试脚本修复**：修复 `test_pg_parse_config_fix_issue.py`、`test_pg_parse_config_rules.py`、`test_prompt_injection.py`、`test_bootstrap.py`、`test_config.py` 等测试用例，适配 schema 变更

### 备注

- 影响面：75 个文件变更（+3943 / -809 LOC）。核心新增：集成验证不可 SKIP、API 端点强制完整性、drift.md 文档化、scenario track 浏览器操作、workflow_failed 用户选择 reset/resume、脏分支检查、AGENTS.md、skills 介绍文档
- 破坏性：`build.injections` → `propose.injections` 重命名，既有 `.pg/project.yaml` 使用了 `build.injections` 的需更新为 `propose.injections`
- `test_strategy` / `coding_standards` 字段移除，既有 `project.yaml` 中仍写这些字段不会报错但会被忽略
- 20 commits，75 文件变更（+3943 / -809 LOC）。新增 9 个文件：`AGENTS.md`、`docs/pg-skills.md`、`docs/cards/` 下 12 张 SVG 卡片、`pg-build/prompt-templates/blocks/verify_mandatory.yaml`

## [0.8.3] - 2026-07-19

### 新增

- **pg-build 集成验证不可 SKIP**：基于 `stages[*].environment.required` 字段，集成验证环节不再允许被 SKIP，确保跨环境依赖必须满足才能继续 pipeline
- **pg-propose 强制 API 端点文档完整性**：`design.md` 中的 API 端点定义必须包含完整的 Request/Response Body 结构，由 `pg-validate-proposal.py` 在校验阶段强制检查，缺失时输出 `api_endpoint_incomplete` 错误码
- **pg-build scenario-fix drift.md 记录**：scenario-fix 在诊断后输出 `drift.md` 文档，记录 design 偏移、根因分析和修复方案，实现 scenario-fix 全流程可追溯
- **pg-build scenario track 浏览器操作支持**：scenario-execute 中新增浏览器操作步骤（点击、输入、截图等），支持前端集成场景的自动化验证
- **pg-build workflow_failed 后用户可选 reset/resume**：`workflow_failed` 状态下重新执行时，由用户选择是 reset（清空状态重来）还是 resume（从失败点继续），提升容错灵活性
- **pg-build 启动前脏分支检查**：`pg-build` 启动前先检查当前分支是否不干净，脏分支时提示用户提交或 stash，避免意外覆盖
- **AGENTS.md**：新增 AGENTS.md 文档，作为 pg-skills 的 AI 开发工作流共享能力层说明文档，涵盖架构概览、SSOT 规则、Agent 协议摘要、开发指南、错误排查等
- **skills 介绍文档**：`docs/pg-skills.md` + 12 张 SVG 卡片（`docs/cards/`），系统介绍品构技能集的理念、痛点、支柱、SSOT、Hook 协议、上手流程等
- **pg-propose 防御越界修改**：`pg-propose-review` 在修改时校验文件路径是否在 change 目录范围内，越界操作自动拒绝，保护项目其他文件不被意外修改
- **pg-propose 移除日期前缀**：新建变更时不再自动添加日期前缀，变更目录名更简洁

### 变更

- **`build.injections` → `propose.injections` 重命名**：`project.schema.json` 及相关脚本中 `build.injections` 字段重命名为 `propose.injections`（propose 阶段注入，build 阶段消费），语义更准确
- **`test_strategy` / `coding_standards` 字段清理**：`project.schema.json` 中移除废弃的 `test_strategy` 和 `coding_standards` 字段，减少配置噪声
- **`project.schema.json` 废弃字段清理**：移除 `build.injections`（已重命名）、`test_strategy`、`coding_standards` 等废弃字段，schema 定义更精简
- **pg-build SKILL 文档对齐**：SKILL.md 文档与实际 reducer 行为保持一致，修复文档与代码的偏差
- **pg-1-define.md 更新**：`/1-pg-define` 命令文档更新，约束说明更清晰
- **Python 3.7 兼容**：`pg-parse-config.py` 等脚本兼容 Python 3.7（移除 `rst` 依赖等）
- **工具编辑器适配**：`tools/project-editor` 移除 `CodingStandardsSection.vue`、`TestStrategySection.vue` 及对应字段，与 `project.schema.json` 清理同步

### 修复

- **pg-build `git.default_branch` 配置读取**：修复 pg-build 读取 `git.default_branch` 配置时的键名解析错误
- **测试脚本修复**：修复 `test_pg_parse_config_fix_issue.py`、`test_pg_parse_config_rules.py`、`test_prompt_injection.py`、`test_bootstrap.py`、`test_config.py` 等测试用例，适配 schema 变更

### 备注

- 影响面：75 个文件变更（+3943 / -809 LOC）。核心新增：集成验证不可 SKIP、API 端点强制完整性、drift.md 文档化、scenario track 浏览器操作、workflow_failed 用户选择 reset/resume、脏分支检查、AGENTS.md、skills 介绍文档
- 破坏性：`build.injections` → `propose.injections` 重命名，既有 `.pg/project.yaml` 使用了 `build.injections` 的需更新为 `propose.injections`
- `test_strategy` / `coding_standards` 字段移除，既有 `project.yaml` 中仍写这些字段不会报错但会被忽略
- 20 commits，75 文件变更（+3943 / -809 LOC）。新增 9 个文件：`AGENTS.md`、`docs/pg-skills.md`、`docs/cards/` 下 12 张 SVG 卡片、`pg-build/prompt-templates/blocks/verify_mandatory.yaml`

## [0.8.2] - 2026-07-16

### 新增

- **Scenario Track 机制（v3.5/v3.6，破坏性）**：新增 `type: scenario` 的 pipeline track 类型，用于真实集成场景测试。scenario track 走独立的 `scenario-prepare → scenario-execute → [scenario-fix → scenario-execute]*` 三阶段生命周期，绕过标准 TDVG 五阶段（test/dev/review/verify/gate）。新增三个 sub-agent：
  - **scenario-prepare**：启动目标环境的所有 service instance，运行健康检查，确保全部 PASS 后进入 execute
  - **scenario-execute**：读取 `scenario.yaml` 中的 Gherkin 风格场景定义，按 Given/When/Then/And 逐条执行 HTTP API 调用，产出结构化 JSON evidence，区分 critical/non-critical 失败
  - **scenario-fix**：读取 execute 失败报告，诊断根因（业务逻辑/API 契约/前后端不匹配/DB/配置），修改源码，通过单元测试+lint 后写 fix report
  - scenario track 不参与 gate assessment（零容忍，critical 失败直接 escalate 进 fix 循环），`max_fix_retries` 耗尽后 `workflow_failed`（无 `accept_gap` 选项）
  - 新增 `SCENARIO_FIX_CYCLE` sub-pipeline 类型（`sub_pipeline.py`），事件枚举 `EVT_SCENARIO_CYCLE_STARTED` / `EVT_SCENARIO_TRACK_COMPLETED`（`events.py`）
  - 新增 3 个 prompt 模板（`scenario-prepare.yaml` / `scenario-execute.yaml` / `scenario-fix.yaml`）+ 3 个 agent 文档
  - 新增测试文件：`test_scenario_track.py`（735 行）、`test_integration.py`（252 行）、`test_orchestrator_gate_precheck.py`（205 行）
- **manifest v3（破坏性）**：`execution-manifest.yaml` 升级到 schema `2026-06-30`（v3），新增 `enabled` 必填布尔字段（pg-build 唯一派遣依据）、`reason` 字段（解释启用/禁用原因）、`on_conditions_eval` 对象（记录机械评估结果）。新增 `type: e2e`（端到端测试+修复循环，需要 `target_module`）和 `type: scenario` 类型。`pg-gen-manifest.py` 的 `_evaluate_on_conditions` 实现双维度机械评估（glob path 匹配 + keyword 语义匹配），`_build_track_enabled_decision` 按命中结果严格派发。`manifest.schema.json` 同步更新
- **pg-gen-scenario.py（v3.6）**：新脚本，按 track 生成 `scenario-<track>.yaml` 骨架文件（含 sentinel placeholder），读取 `on-conditions-eval.md` 的 `scenario_tracks_decision` 段作为 SSOT 决定启用哪些 scenario track。导出 `check_scenario_placeholders()` / `check_scenario_file()` 供下游校验
- **pg-propose v3.7 流程优化**：
  - **流程精简**：阶段 2e/2f 的独立部分校验调用全部删除，统一收敛到阶段 2g 的 `pg-validate-proposal.py manifest` 单一校验点，错误码和错误信息统一
  - **占位符校验**：`pg-validate-proposal.py` 动态导入 `pg-gen-scenario.py`，在 `_validate_three_product_consistency` 中递归检查 `scenario-<track>.yaml` 的每个字段是否仍有未替换的 placeholder（`<...>`、`/.../`、`S-<unique-name>`、`（LLM 必填）`），`<report_seq>` 运行时占位符豁免。新增错误码 `scenario_placeholder_unfilled`
  - **全推荐自动 refine**：`pg-auto-refine-check.py` 检测 review-notes.md 是否满足三条件（通用决策全推荐、issue 无用户意图、无用户编辑标记），满足时自动应用全推荐方案（阶段 4a），跳过 `/2.1-pg-propose-refine` 人工流程
  - `references/scenario-format.md` 新增文件（placeholder 校验协议文档）
  - 新增测试文件：`test_v37_optimizations.py`（423 行）、`test_three_product_consistency.py`（363 行）、`test_manifest_v3.py`（288 行）
- **pg-build workflow_failed 自动 reset**：`pg-build` 在 `workflow_failed` 状态下再次执行时，自动 reset pipeline 状态，无需用户手动干预即可重新开始
- **pg-build 多 scenario.yaml 适配**：orchestrator / dispatch / reducer 等模块适配多个 `scenario.yaml` 文件同时存在的场景
- **pg-propose 多 scenario.yaml 适配**：`pg-gen-manifest.py` / `pg-gen-tasks-skeleton.py` 等模块适配多 scenario 文件
- **pg-propose auto-record scenario tests**：`c04c37e` 在 pipeline events 中自动记录 scenario 测试的执行结果
- **scenario-execute evidence 去重**：evidence 文件名增加 `report_seq` 前缀，避免多次派遣覆盖

### 变更

- **pg-build seq 优化**：pipeline 序列号（seq）生成逻辑优化，提升并行派遣的可靠性
- **scenario.prepare 提示词优化**：按正确顺序启动所有 instance 的提示词组装逻辑
- **pg-propose 流程优化**：`a8ff254` 多处流程细节优化
- **`stages` 中 `test_key` 死字段移除**：`test_key` 字段已不再使用，从 manifest 及相关逻辑中清理

### 修复

- **pg-build 执行完成后跳过 archive 的问题**：修复 pipeline 成功完成后未自动触发 archive 的 bug
- **pg-build 其他 BUG 修复**：`23b2d26` 修复多项 pg-build 运行中的边缘 case 问题

### 备注

- 影响面：60 个文件变更（+8758 / -196 LOC）。核心新增：scenario track 机制（3 个 agent + 3 个 prompt 模板 + 1 个 sub-pipeline 类型 + 735 行测试）、manifest v3（`enabled`/`on_conditions_eval`/e2e+scenario 类型）、pg-propose v3.7（流程精简 + placeholder 校验 + 自动 refine）
- 破坏性：manifest v3 要求 `enabled` 字段必填，旧 manifest 升级后需补充该字段；`scenario` 类型 track 不参与 gate assessment，gate 相关配置对 scenario track 无效
- 96 commits，60 文件变更（+8758 / -196 LOC）。新增 11 个测试文件：`test_scenario_track.py`、`test_integration.py`、`test_orchestrator_gate_precheck.py`、`test_bootstrap.py`、`test_bootstrap_v3.py`、`test_config.py`、`test_orchestrator.py`、`test_manifest_v3.py`、`test_v37_optimizations.py`、`test_three_product_consistency.py`、`test_scenario_track.py`

## [0.8.1] - 2026-07-14

### 新增

- **Verify / Gate 按 track 关闭（破坏性）**：`project.yaml` 新增 `tracks.<id>.verify_enabled` / `gate_enabled` 字段（默认 `true`，向后兼容），关闭后的 phase 沿用 review 的 silent-skip 模式（reducer 通用 `_phase_enabled` + detect `next_pending` 跳过）。关闭逻辑与 v3.x `code_review_enabled` 对齐：由 `execution-manifest.yaml` 的 `phase_prompts` 是否含该 phase 作为 SSOT 派生。simple track 自动关闭
  - **propose 侧**（pg-propose v3.4）：`pg-gen-tasks-skeleton.py` 的 `build_sections` 按 `verify_enabled` / `gate_enabled` / `code_review_enabled` 联合过滤 STANDARD_SUBS；`manifest.schema.json`：`minProperties=2`、`required=["test","dev"]`；`pg-validate-proposal.py`：必填逻辑改为 test+dev 强必填 + verify/gate 至少一项（`_no_quality_gate` 错误码）；`references/tasks-templates.md` track:verify / track:gate 末尾补"何时本章节不出现"小节；`references/review-checklist.md` 新增 §3.5.8 Verify / Gate 一致性
  - **pg-build 侧**：`TrackState` 增加 `verify_enabled` / `gate_enabled` 字段；`reducer.py` 通用 `_phase_enabled` 函数 + `_handle_linear_phase` 通用 silent-skip 循环；`detect.py` `next_pending` 同步识别跳过；`orchestrator.py` bootstrap 时从 manifest 派生
- **design.md 缺陷协议（v2.7）**：fix-review agent 检测到 R-* 根因位于设计层（design.md / tasks.md 文档错误）时，写 `design_md_fault: true` + `design_md_fault_location: "<file>:<line>"`，reducer 检测后立即触发 `workflow_failed`（跳过 review 重审与 `max_review_fix_retries` 计数），提示用户运行 `pg-propose-refine` 修复
- **SubPipeline P0-A 字段增强**：`create_fix_cycle` / `create_gate_fix_cycle` / `create_review_cycle` 新增 `parent_report_path` / `escalation_reason` / `failed_v_tasks` / `created_at` 参数（v2.7），从 reducer 将父 phase 的 report 路径、escalation 原因、失败 task 列表、时间戳注入到 fix dispatch 的 `{verify_report_path}` / `{reason}` / `{failed_at}` / `{source}` 占位符
- **P0 硬约束机制**：`profile_loader.py` CheckConfig 新增 `p0` 字段 + Profile `p0_check_names()`；`sub_agent_contract.py` 新增 `parse_p0_failures()`；reducer `_handle_review` 检测 `implementation_completeness` P0 FAIL 时强制 escalate（绕过 score 阈值）
- **Review rule docs 注入**：dispatch.py `build_ctx(phase='review')` 修复死代码，实际调用 `load_markdown_rule()` 将 `.pg/code-review/<profile>/*.md` 注入 ctx；renderer.py 替换 `__RULE_DOCS_PLACEHOLDER__` / `__P0_CHECKS_PLACEHOLDER__` 占位符
- **pg-run 停止+清理统计算法**：`_run_env_stop_all` 返回结果列表，`停止所有实例并清理环境` 菜单项统一展示停止+清理的统计表

### 移除（破坏性）

- **`review_level` 字段全量移除**：v2.6 时期的 `modules.<m>.review_level` 与 `tracks.<id>.review_level` 字段已删除（schema.json、prompt-templates、agent docs、pg-init-project 推断逻辑、`pg-propose/references/config-fields.md` 全部清理）。v3.x 的 review phase 开关已统一到 `tracks.<id>.code_review_enabled` / `code_review_profile(s)` / `code_review_languages`，profile 集合由 `.pg/code-review/<profile>/*.md` 定义。**迁移指南**：
  - `review_level: security` → `code_review_profiles: [security]` + 把 `.pg/skills/examples/code-review/security/*.md` 拷到 `.pg/code-review/security/`
  - `review_level: standard` → 删除字段（默认行为）
  - `review_level: none` → `code_review_enabled: false`
- **gate agent "步骤 6 安全敏感变更检查"删除**：auth/secret/permission/concurrency 类检查已由 review phase 的 security profile（`auth_bypass` / `secret_leak` / `error_silence`）自动执行（`dispatch.py` 把 `.pg/code-review/security/*.md` 注入 review agent prompt），gate 不再重复。Gate Assessment 检查项从 9 项减为 8 项
- **`pg-init-project` Phase 2 review_level 推断逻辑删除**：不再按 language 自动推断 `modules.<m>.review_level`（该字段已无意义）。review profile 仍按 language 自动派发（`LANGUAGE_PROFILE_MAP`），security profile 保持 opt-in

### 备注

- 影响面：8 个 pg-build 脚本（state / orchestrator / dispatch / config / bootstrap / reducer / detect / events）+ schema.json + 3 个 prompt 模板（base / gate / final-gate）+ 6 个 agent doc（dev / test / fix / verify / fix-gate / gate）+ 11 个测试文件（新增 5 个：`test_dispatch_review_rule_docs.py` / `test_state_verify_gate.py` / `test_detect_skip_disabled.py` / `test_reducer_silent_skip.py` / `test_phase_gate_section.py`）+ 2 个 SKILL 文档（pg-init-project / pg-propose）+ 1 个项目侧 `.pg/project.yaml`
- 不向后兼容：旧 `.pg/project.yaml` 仍写 `review_level` 会触发 schema validation 警告（保持严格不兼容原则）；verify/gate 关闭后 manifesto 的 phase_prompts 不会自动补充（需重跑 pg-propose）
- 安全审查职责已转移到 review phase（dispatch.py 自动注入 security profile markdown 到 review agent），无审查盲区
- 9 commits，55 文件变更（+3862 / -314 LOC）。核心新增：verify/gate 按 track 关闭（4 个新测试文件）、design.md 缺陷协议（events / reducer / CLI 三端联动）、P0 硬约束（profile_loader + reducer + contract）

## [0.8.0] - 2026-07-09

### 新增

- **pg-build v2.6 code-review 阶段（破坏性）**：`test → dev → review → verify → gate` 五阶段模型中新增 `review` 子阶段（位于 dev 与 verify 之间），由独立 `pg-build/review` sub-agent 执行静态代码审查（R-* 检查项：design 对齐 / scope creep / 模式一致 / 文件位置 / 测试契约弱）。`pg-build/fix-review` sub-agent 处理 review 阶段的修复（独立计数 `review_fix_cycles`，默认 3 次），与 `verify→fix` 循环解耦。代码命名从 `code-view` 全量迁移为 `code-review`（agent 文件、prompt 模板、状态字段、事件枚举、测试文件）
- **pg-build review phase Profile 引擎**：`scripts/pipeline/profile_loader.py` 支持 `.pg/code-review.yaml` 多 profile 索引（default / java-spring / go / vue3 / security），Language 自动派发（java/kotlin/scala → `java-spring`，go → `go`，ts/js/vue → `vue3`，其他 → `default`），Union 合并语义（`checks` 并集、`weight` max、`enabled` OR、`pass_threshold` min）。`review` 自动 skip 场景：`code_review_enabled=false`，track type `simple`，`execution-manifest.yaml` 无 `phases.review` 字段
- **pg-build review 子 pipeline 机制**：review escalate 创建 `REVIEW_CYCLE` 子 pipeline（`fix-review → review`），独立于 `FIX_CYCLE`（`fix → verify`）和 `GATE_FIX_CYCLE`（`fix-gate → verify → gate`）。`max_review_fix_retries` 默认为 3，耗尽后强制进 verify
- **pg-build v3.x SSOT 迁移**：`TrackState.code_review_*` 字段（`code_review_enabled` / `code_review_profiles` / `code_review_profile` / `code_review_languages`）从 pg-build 内部状态中删除，改由 `execution-manifest.yaml` 的 `phases.review` 是否存在作为唯一 SSOT。orchestrator bootstrap 时从 manifest 派生 `code_review_enabled` 字段
- **pg-propose v3.3 code-review 适配**：`pg-gen-tasks-skeleton.py` 的 `STANDARD_SUBS` 增加 `review`，按 `tracks.<id>.code_review_enabled` 决定 tasks.md 含 4 或 5 sub。`pg-gen-manifest.py` / `manifest.schema.json` / `pg-validate-proposal.py` 适配：`phase_prompts` 4 必填 + review optional，`minProperties=4` / `maxProperties=5`。`references/tasks-templates.md` 新增 `track:review` 章节模板。章节号 N 跨 change 不再一致，下游消费方基于 sections JSON 的 N 值填
- **pg-init-project v0.3 code-review 适配**：新增 Phase 2.5，根据 Phase 1 扫到的 module languages 自动派发 `.pg/code-review/` 目录（profile 索引 + 各 profile 检查项细则）。`modules.<m>.review_level` 字段新增（按 language 推断：`java/go/proto` → `security`，`ts/py` → `standard`，`shell` → `none`）。Security profile 保持 opt-in 不自动拷贝。Phase 2.5 幂等：已存在 `.pg/code-review/` 时不覆盖。模板缺失时 WARN + 仅生成 default profile
- **code-review 示例模板**：`examples/code-review/` 目录新增 15 个文件，覆盖 5 个 profile：
  - `default/`5 文件：`design_alignment.md`、`file_location.md`、`pattern_consistency.md`、`scope_creep.md`、`test_contract.md`
  - `java-spring/`2 文件：`null_safety.md`、`pattern_consistency.md`
  - `go/`2 文件：`error_wrapping.md`、`pattern_consistency.md`
  - `vue3/`2 文件：`component_props.md`、`pattern_consistency.md`
  - `security/`3 文件：`auth_bypass.md`、`error_silence.md`、`secret_leak.md`
  - `code-review.yaml`：profile 索引（weight / enabled / pass_threshold / escalate_threshold / checks 及 Union 继承规则）
- **pg-fix-issue v3.1 → v3.2 重构**：重写 SKILL.md（删除 ~1600 行过时代码），采用 6 阶段流程（Phase 0 Load Config → Phase 1 Call Chain → Phase 2 Plan → Phase 3 用户确认 → Phase 4 自动修复 → Phase 5 验证）。支持 `pg-fix-issue-v2` 目录删除。Phase 1.5 新增复现判定门
- **pg-fix-issue 目录清理**：删除 `pg-fix-issue-v2` 目录（`src/opencode/skills/pg-fix-issue-v2/` 230 行 SKILL.md + 19 行命令文件），`/5-pg-fix-issue-v2` 命令同步删除。`pg-fix-issue-v2` 命名空间彻底退役
- **pg-propose tasks.md 骨架脚本外化**：`pg-gen-tasks-skeleton.py` 替代 LLM 手工生成章节标题骨架。脚本输出 `tasks.md` + `on-conditions-eval.md` + sections JSON，LLM 只填充 body。`--selected-stages` 参数支持按 on_conditions 过滤 stage。`pg-gen-tasks-skeleton.py` 新增 620 行代码 + 722 行测试用例
- **pg-propose 两阶段填充法**：LLM 先机械生成所有章节标题骨架（`--selected-stages` 过滤后的 stage + `--affected-tracks` 内的 track），heading 骨架确认无误后再逐个填充 body。禁止在填充阶段调整 heading 顺序或跳过章节
- **pg-build 配置优化**：`tracks.<id>.max_fix_retries` 统一为 5（默认值），`max_gate_fix_retries` 移除。`tracks.<id>.code_review_enabled` 新增字段。`manifest.schema.json` tracks 对象新增 `code_review_enabled` 属性
- **pg-run health_check 菜单**：`pg-run` 交互菜单增加 `Instance.health_check` 选项，配合 `actions.health_check` 声明使用
- **pg-define 约束收紧**：禁止 `pg-define` 直接生成 `design.md` / `proposal.md` / `tasks.md`（这些产物由 `pg-propose` 独占产出）
- **Python 3.7 兼容**：`pg-gen-tasks-skeleton.py` 等脚本兼容 Python 3.7（`rst` 回退 `removeprefix` / `removesuffix` 等）
- **品构品牌命名**：Workshop 文档标题从 `pg-skills Workshop` 改为 `品构 Workshop`，slogan `让 AI 写出可托付的代码` 贯穿全文。sec1 重构为 5 段叙事（§1.1 可托付 / §1.2 品构命名释义 / §1.3 4 支柱 / §1.4 AI 角色表 / §1.5 速查对照表），新增 SVG 品牌视觉

### 变更

- **code_view → code_review 全量重命名（破坏性）**：agent 文件 `code-view.md` → `review.md`，`fix-code-view.md` → `fix-review.md`；prompt 模板 `code-view.yaml` → `review.yaml`，`fix-code-view.yaml` → `fix-review.yaml`；状态字段 `code_view_enabled` → `code_review_enabled`；事件枚举 `EVT_CODE_VIEW_*` → `EVT_REVIEW_*`，`EVT_FIX_CODE_VIEW_*` → `EVT_FIX_REVIEW_*`；测试文件 `test_state_code_view.py` → `test_state_review.py`，`test_code_view_section.py` → `test_review_section.py`。pg-build 内部 `TrackState.code_view_*` 字段：`code_view_enabled` / `code_view_profile` → `code_review_enabled` / `code_review_profiles` / `code_review_profile` / `code_review_languages`
- **pg-propose SKILL.md 重构**：SKILL.md 从 810 行压缩到 303 行（-507 行），流程编排与模板字符串分离：SKILL.md 仅保留流程编排 + 阶段契约 + 黑/白名单；模板字符串、字段定义、规则清单全部下放到 `references/` 单一 SSOT。顶部新增「文档导航」routing table
- **pg-build SKILL.md 更新**：新增 v2.6 review 阶段完整文档（profile 配置 / Score 协议 / fix-review 循环 / 关闭方式 / v3.x SSOT 迁移），新增 v2.5 `--result-json` 记录，新增 v2.4 result.json 强制落盘协议
- **pg-propose references 解耦**：`references/orchestration-model.md` 重写（161 行增量），`references/tasks-templates.md` 重写（139 行增量），新增 `references/review-checklist.md`（6 类自审清单 + 3.5.7 on_conditions 复核），新增 `references/config-fields.md`

### 修复

- **pg-gen-tasks-skeleton.py `--selected-stages` 参数缺失**：v3.2 生成的 skeleton 错误地包含所有 stage（包括 on_conditions 未命中的），追加 `--selected-stages` 参数后仅生成选中 stage 的章节，不占章节号 N
- **pg-gen-manifest.py phase_prompts 校验**：phase_prompts 的 minProperties 从 4 改为 4（必填）+ 1 optional（review），maxProperties 从 4 改为 5，适配 code-review 阶段

### 备注

- 19 个 commits，78 文件变更（+8220 / -2205 LOC）。核心新增：code-review 引擎（4 个 sub-agent + 15 个 profile 模板 + 3 个 SKILL 适配）、pg-propose tasks.md 骨架外化（620 行脚本 + 722 行测试）、pg-init-project Phase 2.5（97 行增量）。品牌命名同步更新：Workshop 文档标题改为「品构」
- 破坏性变更：`code_view` → `code_review` 全量重命名（影响所有 state 字段、事件枚举、agent 文件、prompt 模板、测试文件），既有 snapshot 需要迁移。pg-propose tasks.md 章节号 N 跨 change 不再一致（同一 track 在不同 change 的 N 可能不同）
- 依赖：`.pg/skills/examples/code-review/` 模板目录必须存在（subtree 拉取的 SSOT 模板），含 default / java-spring / go / vue3 / security 5 个 profile 的检查项细则。该目录缺失时 pg-init-project 仅生成 default profile 并 WARN
- 新增 5 个测试文件：`test_state_review.py`（222 行）、`test_review_section.py`（335 行）、`test_profile_loader.py`（409 行）、`test_state_code_view.py` 替换为 `test_state_review.py`，`test_code_view_section.py` 替换为 `test_review_section.py`
- `pg-fix-issue-v2` 目录删除，命名空间彻底退役
- 品构品牌命名：`pg-skills` 保持为 git 路径名和 CLI 工具名不变，用户面称谓从本版本起统一为「品构」

### 新增

- **pg-build v2 取代 v1（破坏性合并）**：原 `pg-build-v2` 重命名为 `pg-build`，并吸收原 v1 的 `pg-pipeline-runner.py` 行为（v1 过程式状态机 + 51 个 `save_state` 调用被彻底替换）。`pg-build-v2` 目录删除、`/3-pg-build-v2` 命令删除、`_deprecated_README.md` 删除、`src/opencode/skills/pg-build-v2/scripts/pg-pipeline-runner.py` 删除。SKILL.md 重写（871 → 265 行），直接暴露 5 个 CLI 子命令（`bootstrap` / `next` / `record` / `progress` / `env-action` / `env-action-result`）。`/3-pg-build` 命令重写为直接调用 runner 编排
- **路径简化（破坏性）**：caller 维度日志目录从 `<env>/logs` 改为 `<env>-logs`，影响所有 5 个 caller 命名空间（pg-build / pg-regression / pg-fix-issue / pg-agent / ad-hoc）。`pg-invoke-hook.py` 的 `pg_log_dir_for_skill` 同步更新；`pg-run-hook.py` 示例、SKILL.md 路径示意、init-project Phase 5 drift 清单示例同步更新。**既有项目**：pg-build 自动迁移 `2-build/<env>/logs` → `2-build/<env>-logs`；其它 caller 仍需手工迁移或重建
- **execution-manifest.yaml 成为环境 SSOT（破坏性）**：v1 遗留的 `environment.yaml` 弃用，per-change 的环境选择写入 `execution-manifest.yaml.stages[i].environment`，由 `pg-build` 直接读取。`pg-propose` 阶段 2d 产物清单硬约束 4 个文件（proposal.md / design.md / execution-manifest.yaml / tasks.md），严禁生成 `environment.yaml`
- **`pg-verify-and-merge` AffectedTracks 推断 5 层 fallback**：新增 `execution-manifest.yaml` 优先级（`__meta.affected_tracks_source = "manifest"`），pg-gen-manifest.py 已自动过滤全部 `- 无` 的 track，比 tasks.md 更精确。`pg-parse-config.py --json-only` flag 抑制 banner，stdout 纯净
- **pg-verify-and-merge lint 日志独立空间**：lint 输出落到 `<change>/3-merge/lint-logs/lint-<track>-<ts>.log`（与 `2-build/` 解耦），成功时静默，失败时 `tail -50`。archive 路径自动推断：编排器优先传 archive 路径；`pg-parse-config.py` 输出 `__meta.change_dir` 不存在时回退到 `glob archive/*-<change>` 查找
- **pg-regression 自动修复边界（A/B/C 三分类）**：fix-test agent 必须按下表判定每条失败属于哪一类，决定自动修或上报：
  - 🟢 **A 类**（必须自动修，不上报）：A1 断言期望值漂移、A2 选择器过期、A3 等待逻辑、A4 框架 API 误用、A5 fixture 写错、A6 测试隔离、A7 env 硬编码、A8 断言精度
  - 🟡 **B 类**（条件性自动修，必须附 `rationale`）：B1 断言放宽、B2 新增 helper、B3 mock 匹配新接口、B4 重命名局部变量、B5 调整 cleanup、B6 加 retry 限制
  - 🔴 **C 类**（禁止自动修，必须上报）：C1 生产 bug、C2 接口语义变更、C3 schema、C4 环境配置、C5 测试数据缺失、C6 新增 skip/fixme、C7 弱化断言、C8 跨服务契约、C9 第三方依赖、C10 并发偶发、C11 删/合并用例
- **`pg-check-fix-test-boundary.py`（边界守护）**：编排器在 fix-test agent 返回后、Phase 2a 提交前必须扫描 git diff，命中 C6（新增 skip/only/todo/@Disabled/@Ignore/xit/xdescribe）、C7（断言数量减少）、C11（删 it/test/@Test）、C5（改 fixtures/seeds/sql/test-data）任一硬规则 → 立即 `git checkout -- <test_files>` 回滚，把这些用例转写为 `unfixableIssues` 上报
- **pg-regression `skippedUnits` 分析（Phase 2b）**：Phase 1.2 输出的 `phase1-failures.json` 现包含 `skippedUnits` 字段（按文件分组的跳过测试清单）。编排器读 `skippedUnits` 按 skip 原因分类（C5 测试数据缺失 / C2 生产代码未实现 / C10 环境不足 / 其他），C2 类追加到 `unfixableIssues` 走生产代码修复流程；C5 写入 `skipped_targets` 记录已知跳过
- **`.pg/regression/<suite>.json` schema 扩展**：每条 issue 新增 `auto_fixed`（bool，缺省 `false`）、`rationale`（仅 B 类有值）、`category`（`A<id>`/`B<id>`/`C<id>`）字段。Phase 4 runner 只处理 `auto_fixed=false` 的 issue（已自动修的不再重复处理）
- **`pg-build-result` 工具**：独立的 result 落盘 CLI，`--output-path` 强制落盘到指定路径，编排器校验 `result.json` 落盘后派生路径工具 `derive_result_path`，避免 LLM 自作主张。`pipeline.events` 写盘前必须先有 `result.json`
- **env-action 钩子架构拆分（v2.1.1）**：`pg-pipeline-runner.py` 的 env hook 执行从主循环拆出为独立的 `env-action` / `env-action-result` 子命令，配合 `--phase prepare_env|clean_env --stage <stage> --env <env>` 三元组定位。`--success` 布尔语义（`ok` → `success` 重命名）明确"hook 是否成功执行"，与 record 的 `--status` 字段语义解耦
- **env-action 日志记录**：每次 env hook 执行落 `<change>/2-build/<env>-logs/role.*.<action>@<ts>.log`，`prepare_env` / `clean_env` 日志同样路由
- **pg-init-project pg-skip-agents-md-migration**：新增 `PG_SKIP_AGENTS_MD_MIGRATION` 兜底开关，用户拒绝时仍强行生成 patch 清单的场景被禁止；CI 跑 pg-init-project 时 `.pg/context/` 下不再出现污染 artifacts
- **`pg-parse-test-results.py` skipped 解析**：playwright + junit 输出解析新增 skipped 单元聚合（按文件/类分组），与 failedUnits 同构

### 修复

- **pg-build runner `--tasks-updated` 参数位置错误**：v1 风格的 positional arg 改为 `--flag` 形式（`--tasks-updated t1 --tasks-updated t2`），避免输入参数位置错误
- **pg-build runner record 传参错误**：`record` 子命令新增 `--result-json` 参数，强制指定 result.json 路径，杜绝 LLM 把 result.json 写到错误位置的问题
- **pg-build runner `pg-invoke-hook` 漏传 `--skill`**：env hook 调度时 `--skill` 参数硬编码传入（不再依赖环境变量），避免日志落到错误的 caller 命名空间
- **pg-build verify → fix 派遣路由**：修复 verify ESCALATE 后无法正确派遣到 fix agent 的问题（dispatch_file 注入 `context.verify_report_path`，fix agent 自行读源 verify 报告，runner 不解析 V-N 章节、不提取结构化字段）
- **pg-build prepare_env 阶段错选**：修复 state persist bug 导致 prepare_env 阶段选择错误的根因
- **pg-build archive 后 `pipeline.events` 写入错误路径**：修复 `pg-build` 完成 archive 后仍向旧 change 目录写入 `pipeline.events` 的问题
- **pg-fix-issue `max_per_iteration_subcalls` 移除**：`max_per_iteration_subcalls`（单次 iteration 内 executor 重派上限）从 schema 与默认配置中删除，统一收敛到 `max_iteration_count` 一个计数器（避免双计数器混淆与回归 ESCALATE 判定）
- **pg-fix-issue `tracks.<id>.max_fix_retries` 移除**：`tracks.<id>.max_fix_retries`（subagent 重试上限）从 SKILL.md、配置示例、`fix_issue_context` 表中删除，与 `max_iteration_count`（主 agent 整体迭代上限）不再有"双计数器"语义混淆
- **`pg-run-hook.py` 路径示例更新**：示例 log_path 与新 `<env>-logs` 路由一致
- **`pg-parse-config.py` banner 截断问题**：新增 `--json-only` flag，抑制 stderr banner，让 stdout 纯净（LLM 直接 `json.load()` 无需 python 管道截断）
- **`pg-parse-config.py` banner 边界对齐**：banner 分隔符从 64 个 `=` 改为 60 个（视觉对齐）

### 变更

- **pg-fix-issue actions 列表加 `restart`/`health_check`**：`environments.<env>.roles.<role>.actions.{start,stop,restart,logs,tail,health_check}` 一致化，pg-invoke-hook.py 同步支持
- **`pg-invoke-hook.py` 新增 flag**：`--log-dir`（agent 调试用，显式覆盖日志目录）、`--timeout-override`（ad-hoc 调试用，CLI 显式传时输出 WARN）、`--no-wait-for-bg`（start action 的 fire-and-forget 开关，hook `pg_start_bg` setsid detach 后立即返回）、`--wait-for-completion`（强制等 hook 跑完，覆盖 start 默认）
- **编排器职责收紧**：禁止编排器读取 `dispatch.md` 文件（避免它自作主张调整 prompt），禁止编排器修改 prompt 模板
- **`build_rules` 字段注入扩展**：`build_rules` 同时注入 `pg-build/dev` 与 `pg-build/verify` prompt（之前只在 verify 阶段）
- **pg-parse-config.py Maven Surefire 解析清理**：删除冗余的"Find the full class name by looking at context"逻辑；正则调整（保留兼容的 part 顺序）
- **pg-propose tasks.md 两阶段骨架填充法**：LLM 遵行两阶段法——先按 `stage.tracks` 数组顺序机械生成所有章节标题骨架（simple=1 个 heading，standard=4 个 heading，N 连续递增），heading 骨架确认无误后再逐个填充 body 内容。禁止在填充阶段调整 heading 顺序、跳过非 affected track 或调换 simple/standard 先后顺序
- **pg-propose track 级 `on_conditions`**：若 track 定义了 `on_conditions`，所有条件未命中时该 track 不生成任何章节（完全跳过，不占章节号）

### 备注

- `pg-build` 与 `pg-build-v2` 命名空间合一（v2 内容吸收到 `pg-build`，原 `pg-build-v2` 目录物理删除）；`/3-pg-build-v2` 命令同步删除
- 路径简化（`<env>/logs` → `<env>-logs`）对**既有项目**有迁移成本：pg-build 自动迁移 `2-build/<env>/logs` → `2-build/<env>-logs`，但 pg-regression / pg-fix-issue / pg-agent / ad-hoc 命名空间仍需手工迁移或重建
- `execution-manifest.yaml` 取代 `environment.yaml` 是**破坏性**变更：旧 change 的 `environment.yaml` pg-build 不再读取，必须重新跑 `pg-propose` 生成 `execution-manifest.yaml`
- pg-regression A/B/C 分类边界是 fix-test agent 行为的**强约束**：A/B 类自动修但必须落 rationale 到 `auto_fixed=true`；C 类禁止自动修（包括 C6 新增 skip、C7 弱化断言、C11 删用例）；`pg-check-fix-test-boundary.py` 二次守护
- 39 commits，144 文件变更（+9191 / -25260 LOC）。旧 v1 过程式状态机代码全部删除（`pg_pipeline_state_v2.py` 1163 行、`pg_context_chain.py` 229 行、`pg_pipeline_common.py` 1235 行等）
- 新增 9 个测试文件：`test_derive_result_path.py`、`test_error_path.py`、`test_fix_routing.py`、`test_pg_build_result.py`、`test_record_flags.py`、`test_record_result_json.py`、`test_event_log.py`、`pg-check-fix-test-boundary.py` 等
- `pg-skip-agents-md-migration` 兜底开关：CI 中可设置 `PG_SKIP_AGENTS_MD_MIGRATION=1` 跳过 AGENTS.md drift 清单生成

## [0.7.0] - 2026-07-05

### 新增

- **pg-build v2 取代 v1（破坏性合并）**：原 `pg-build-v2` 重命名为 `pg-build`，并吸收原 v1 的 `pg-pipeline-runner.py` 行为（v1 过程式状态机 + 51 个 `save_state` 调用被彻底替换）。`pg-build-v2` 目录删除、`/3-pg-build-v2` 命令删除、`_deprecated_README.md` 删除、`src/opencode/skills/pg-build-v2/scripts/pg-pipeline-runner.py` 删除。SKILL.md 重写（871 → 265 行），直接暴露 5 个 CLI 子命令（`bootstrap` / `next` / `record` / `progress` / `env-action` / `env-action-result`）。`/3-pg-build` 命令重写为直接调用 runner 编排
- **路径简化（破坏性）**：caller 维度日志目录从 `<env>/logs` 改为 `<env>-logs`，影响所有 5 个 caller 命名空间（pg-build / pg-regression / pg-fix-issue / pg-agent / ad-hoc）。`pg-invoke-hook.py` 的 `pg_log_dir_for_skill` 同步更新；`pg-run-hook.py` 示例、SKILL.md 路径示意、init-project Phase 5 drift 清单示例同步更新。**既有项目**：pg-build 自动迁移 `2-build/<env>/logs` → `2-build/<env>-logs`；其它 caller 仍需手工迁移或重建
- **execution-manifest.yaml 成为环境 SSOT（破坏性）**：v1 遗留的 `environment.yaml` 弃用，per-change 的环境选择写入 `execution-manifest.yaml.stages[i].environment`，由 `pg-build` 直接读取。`pg-propose` 阶段 2d 产物清单硬约束 4 个文件（proposal.md / design.md / execution-manifest.yaml / tasks.md），严禁生成 `environment.yaml`
- **`pg-verify-and-merge` AffectedTracks 推断 5 层 fallback**：新增 `execution-manifest.yaml` 优先级（`__meta.affected_tracks_source = "manifest"`），pg-gen-manifest.py 已自动过滤全部 `- 无` 的 track，比 tasks.md 更精确。`pg-parse-config.py --json-only` flag 抑制 banner，stdout 纯净
- **pg-verify-and-merge lint 日志独立空间**：lint 输出落到 `<change>/3-merge/lint-logs/lint-<track>-<ts>.log`（与 `2-build/` 解耦），成功时静默，失败时 `tail -50`。archive 路径自动推断：编排器优先传 archive 路径；`pg-parse-config.py` 输出 `__meta.change_dir` 不存在时回退到 `glob archive/*-<change>` 查找
- **pg-regression 自动修复边界（A/B/C 三分类）**：fix-test agent 必须按下表判定每条失败属于哪一类，决定自动修或上报：
  - 🟢 **A 类**（必须自动修，不上报）：A1 断言期望值漂移、A2 选择器过期、A3 等待逻辑、A4 框架 API 误用、A5 fixture 写错、A6 测试隔离、A7 env 硬编码、A8 断言精度
  - 🟡 **B 类**（条件性自动修，必须附 `rationale`）：B1 断言放宽、B2 新增 helper、B3 mock 匹配新接口、B4 重命名局部变量、B5 调整 cleanup、B6 加 retry 限制
  - 🔴 **C 类**（禁止自动修，必须上报）：C1 生产 bug、C2 接口语义变更、C3 schema、C4 环境配置、C5 测试数据缺失、C6 新增 skip/fixme、C7 弱化断言、C8 跨服务契约、C9 第三方依赖、C10 并发偶发、C11 删/合并用例
- **`pg-check-fix-test-boundary.py`（边界守护）**：编排器在 fix-test agent 返回后、Phase 2a 提交前必须扫描 git diff，命中 C6（新增 skip/only/todo/@Disabled/@Ignore/xit/xdescribe）、C7（断言数量减少）、C11（删 it/test/@Test）、C5（改 fixtures/seeds/sql/test-data）任一硬规则 → 立即 `git checkout -- <test_files>` 回滚，把这些用例转写为 `unfixableIssues` 上报
- **pg-regression `skippedUnits` 分析（Phase 2b）**：Phase 1.2 输出的 `phase1-failures.json` 现包含 `skippedUnits` 字段（按文件分组的跳过测试清单）。编排器读 `skippedUnits` 按 skip 原因分类（C5 测试数据缺失 / C2 生产代码未实现 / C10 环境不足 / 其他），C2 类追加到 `unfixableIssues` 走生产代码修复流程；C5 写入 `skipped_targets` 记录已知跳过
- **`.pg/regression/<suite>.json` schema 扩展**：每条 issue 新增 `auto_fixed`（bool，缺省 `false`）、`rationale`（仅 B 类有值）、`category`（`A<id>`/`B<id>`/`C<id>`）字段。Phase 4 runner 只处理 `auto_fixed=false` 的 issue（已自动修的不再重复处理）
- **`pg-build-result` 工具**：独立的 result 落盘 CLI，`--output-path` 强制落盘到指定路径，编排器校验 `result.json` 落盘后派生路径工具 `derive_result_path`，避免 LLM 自作主张。`pipeline.events` 写盘前必须先有 `result.json`
- **env-action 钩子架构拆分（v2.1.1）**：`pg-pipeline-runner.py` 的 env hook 执行从主循环拆出为独立的 `env-action` / `env-action-result` 子命令，配合 `--phase prepare_env|clean_env --stage <stage> --env <env>` 三元组定位。`--success` 布尔语义（`ok` → `success` 重命名）明确"hook 是否成功执行"，与 record 的 `--status` 字段语义解耦
- **env-action 日志记录**：每次 env hook 执行落 `<change>/2-build/<env>-logs/role.*.<action>@<ts>.log`，`prepare_env` / `clean_env` 日志同样路由
- **pg-init-project pg-skip-agents-md-migration**：新增 `PG_SKIP_AGENTS_MD_MIGRATION` 兜底开关，用户拒绝时仍强行生成 patch 清单的场景被禁止；CI 跑 pg-init-project 时 `.pg/context/` 下不再出现污染 artifacts
- **`pg-parse-test-results.py` skipped 解析**：playwright + junit 输出解析新增 skipped 单元聚合（按文件/类分组），与 failedUnits 同构

### 修复

- **pg-build runner `--tasks-updated` 参数位置错误**：v1 风格的 positional arg 改为 `--flag` 形式（`--tasks-updated t1 --tasks-updated t2`），避免输入参数位置错误
- **pg-build runner record 传参错误**：`record` 子命令新增 `--result-json` 参数，强制指定 result.json 路径，杜绝 LLM 把 result.json 写到错误位置的问题
- **pg-build runner `pg-invoke-hook` 漏传 `--skill`**：env hook 调度时 `--skill` 参数硬编码传入（不再依赖环境变量），避免日志落到错误的 caller 命名空间
- **pg-build verify → fix 派遣路由**：修复 verify ESCALATE 后无法正确派遣到 fix agent 的问题（dispatch_file 注入 `context.verify_report_path`，fix agent 自行读源 verify 报告，runner 不解析 V-N 章节、不提取结构化字段）
- **pg-build prepare_env 阶段错选**：修复 state persist bug 导致 prepare_env 阶段选择错误的根因
- **pg-build archive 后 `pipeline.events` 写入错误路径**：修复 `pg-build` 完成 archive 后仍向旧 change 目录写入 `pipeline.events` 的问题
- **pg-fix-issue `max_per_iteration_subcalls` 移除**：`max_per_iteration_subcalls`（单次 iteration 内 executor 重派上限）从 schema 与默认配置中删除，统一收敛到 `max_iteration_count` 一个计数器（避免双计数器混淆与回归 ESCALATE 判定）
- **pg-fix-issue `tracks.<id>.max_fix_retries` 移除**：`tracks.<id>.max_fix_retries`（subagent 重试上限）从 SKILL.md、配置示例、`fix_issue_context` 表中删除，与 `max_iteration_count`（主 agent 整体迭代上限）不再有"双计数器"语义混淆
- **`pg-run-hook.py` 路径示例更新**：示例 log_path 与新 `<env>-logs` 路由一致
- **`pg-parse-config.py` banner 截断问题**：新增 `--json-only` flag，抑制 stderr banner，让 stdout 纯净（LLM 直接 `json.load()` 无需 python 管道截断）
- **`pg-parse-config.py` banner 边界对齐**：banner 分隔符从 64 个 `=` 改为 60 个（视觉对齐）

### 变更

- **pg-fix-issue actions 列表加 `restart`/`health_check`**：`environments.<env>.roles.<role>.actions.{start,stop,restart,logs,tail,health_check}` 一致化，pg-invoke-hook.py 同步支持
- **`pg-invoke-hook.py` 新增 flag**：`--log-dir`（agent 调试用，显式覆盖日志目录）、`--timeout-override`（ad-hoc 调试用，CLI 显式传时输出 WARN）、`--no-wait-for-bg`（start action 的 fire-and-forget 开关，hook `pg_start_bg` setsid detach 后立即返回）、`--wait-for-completion`（强制等 hook 跑完，覆盖 start 默认）
- **编排器职责收紧**：禁止编排器读取 `dispatch.md` 文件（避免它自作主张调整 prompt），禁止编排器修改 prompt 模板
- **`build_rules` 字段注入扩展**：`build_rules` 同时注入 `pg-build/dev` 与 `pg-build/verify` prompt（之前只在 verify 阶段）
- **pg-parse-config.py Maven Surefire 解析清理**：删除冗余的"Find the full class name by looking at context"逻辑；正则调整（保留兼容的 part 顺序）
- **pg-propose tasks.md 两阶段骨架填充法**：LLM 遵行两阶段法——先按 `stage.tracks` 数组顺序机械生成所有章节标题骨架（simple=1 个 heading，standard=4 个 heading，N 连续递增），heading 骨架确认无误后再逐个填充 body 内容。禁止在填充阶段调整 heading 顺序、跳过非 affected track 或调换 simple/standard 先后顺序
- **pg-propose track 级 `on_conditions`**：若 track 定义了 `on_conditions`，所有条件未命中时该 track 不生成任何章节（完全跳过，不占章节号）

### 备注

- `pg-build` 与 `pg-build-v2` 命名空间合一（v2 内容吸收到 `pg-build`，原 `pg-build-v2` 目录物理删除）；`/3-pg-build-v2` 命令同步删除
- 路径简化（`<env>/logs` → `<env>-logs`）对**既有项目**有迁移成本：pg-build 自动迁移 `2-build/<env>/logs` → `2-build/<env>-logs`，但 pg-regression / pg-fix-issue / pg-agent / ad-hoc 命名空间仍需手工迁移或重建
- `execution-manifest.yaml` 取代 `environment.yaml` 是**破坏性**变更：旧 change 的 `environment.yaml` pg-build 不再读取，必须重新跑 `pg-propose` 生成 `execution-manifest.yaml`
- pg-regression A/B/C 分类边界是 fix-test agent 行为的**强约束**：A/B 类自动修但必须落 rationale 到 `auto_fixed=true`；C 类禁止自动修（包括 C6 新增 skip、C7 弱化断言、C11 删用例）；`pg-check-fix-test-boundary.py` 二次守护
- 39 commits，144 文件变更（+9191 / -25260 LOC）。旧 v1 过程式状态机代码全部删除（`pg_pipeline_state_v2.py` 1163 行、`pg_context_chain.py` 229 行、`pg_pipeline_common.py` 1235 行等）
- 新增 9 个测试文件：`test_derive_result_path.py`、`test_error_path.py`、`test_fix_routing.py`、`test_pg_build_result.py`、`test_record_flags.py`、`test_record_result_json.py`、`test_event_log.py`、`pg-check-fix-test-boundary.py` 等
- `pg-skip-agents-md-migration` 兜底开关：CI 中可设置 `PG_SKIP_AGENTS_MD_MIGRATION=1` 跳过 AGENTS.md drift 清单生成

## [0.6.0] - 2026-07-02

### 新增

- **pg-build-v2 事件溯源引擎**：Event Sourcing + Reducer 纯函数取代过程式状态机（`pipeline.events` append-only JSONL 作为唯一持久化入口）。核心指标：7800 LOC → 3000 LOC（-62%），51 `save_state` 调用收敛为 1 `event_log.append`。详见 `src/opencode/skills/pg-build-v2/`
  - YAML 模板与代码解耦（11 个模板文件）
  - SubPipeline 递归复用 reducer（替代 `in_fix_cycle` 状态 flag）
  - v1 迁移脚本（旧 `.pipeline-state.json` → `pipeline.events`）
  - 125 单元+集成测试全绿
- **pg-build-v2 v2.1 pipeline reliability improvements**：
  - 保留每 record 原子化 commit（审计痕迹，最终 squash 压平）
  - Sub-agent 返回 JSON schema 校验 + `evidence_missing` hard fail
  - 5 维加权评分 gate-score（≥ 80 通过）
  - final-gate 前置门控 gate assessment 缺失阻断 + 加权评分
  - checkpoint/resume 机制 + verify-replay 对比命令
- **pg-build-v2 v2.2 dispatch 提示词结构优化**：
  - env.instances/hooks + 运行时环境操作指令按 phase 条件注入（test/dev/verify/fix/fix-gate 注入完整 env 配置；gate/simple/final-gate 跳过）
  - 删除末尾旧"返回格式"段（被 `sub_agent_contract.yaml` 6 字段段取代）
  - 标题简化：`## 任务：{id} - {label}` → `## 任务：{id}`
  - 测试：新增 `test_dispatch_renderer.py`（13 个 case 覆盖 5 phase × 2 维度）
- **v1/v2 行为对齐**：3 个共享 helper（`pg_build_bootstrap` / `pg_build_dispatch_context` / `pg_build_record_log`）统一 v1/v2 的 分支创建 / init commit / context-chain 记账 / manifest 校验逻辑，避免回归。v1 `cmd_next`/`cmd_record` 行为不变
- **mark-task CLI**：`pg_pipeline_state_v2.py mark-task` 子命令，tasks.md 转为派生视图（state.json 是 SSOT），支持幂等标记。配套 CI lint `lint_tasks_md.py` 检测直接 Edit tasks.md 的违规变更，与 state.json 交叉验证
- **`actions.health_check`（声明才生成）**：instance 级 health check action，支持 HTTP 探针（backend/frontend）与 TCP 探针（agent）。`pg-init-project` 仅当 `environments.<env>.roles.<r>.actions.health_check` 存在时生成 hook
- **pg-agent workflow + Phase 5**：新增 `CALLER_PG_AGENT` 路由（`.pg/agent/<session>/<env>/logs/`），治理 AGENTS.md drift。`pg-init-project` Phase 5 扫描 **/AGENTS.md 分类 drift（a/b/c 三类），生成 SSOT 速查 + review 清单。`pg doctor` 新增 2 项 warn 检查
- **pg-run 菜单增强**：新增"停止所有实例并清理环境"菜单项，高亮选中菜单，修复菜单切换界面漂移，优化"准备环境并启动所有实例"过程中的输出提示
- **symlink 管理**：`pg init` 重建已存在的 symlink，删除冗余 symlink

### 修复

- **Simple track 路由**：`type=simple` 的 track（如 openapi-gen）不再被错误走成 test/dev/verify/gate 4 个空 sub。两处 bug：`_is_phase_item()` 委托给 `get_track_type()` helper；`_next_phase_in_track()` 加 `is_simple_track()` 短路
- **Final-gate 单次派遣**：`record_completed()` 顶部加 final-gate special handling，委托给 `record_pass()`，避免 final-gate 被拆成 4 个 sub 派遣
- **pg-build bootstrap `prepare_env` 阶段错选**：修复 state persist bug 导致 prepare_env 阶段选择错误
- **pg-verify-and-merge Phase 4 防御性切回 master**：避免 workspace 滞留在 feature branch，确保即使编排者走 feature branch fallback 后最终也回到 master
- **`renumber-flyway-migration.sh` 路径 bug**：修复脚本中飞路迁移文件重编号时的路径解析错误
- **Simple track 上下文缺失**：`cmd_record_v2` 补回 context-chain 记账 + commit 元数据挂载；`cmd_next_v2` 补回分支创建 / init commit / ctx enrich / manifest 校验
- **Hook `wait_for_completion` 默认行为修复**：start hook 在 background 运行，默认 `wait_for_completion=false`

### 变更

- **非破坏性**：`pg-pipeline-runner.py` 删除 v1 漂移检测（`_validate_state_consistency` / `_any_open_section` / `_duplicate_warning` 等 ~212 行），默认启用 `state_v2.enabled=true`
- **非破坏性**：`pg-build` SKILL.md 新增 v1/v2 行为对齐章节（共享 helper 设计文档）
- **非破坏性**：优化 pg-build 编排器提示词，禁止过多准备 prompt
- **非破坏性**：`pg upgrade` 从 tag 拉取新版本

### 备注

- pg-build-v2 与 pg-build 并行存在，通过 `/3-pg-build-v2` 命令访问；旧 `pg-build` 标记 deprecated（`_deprecated_README.md`）
- 新增 6 个测试文件：`test_state_v2.py`(25)、`test_runner_v2_shadow.py`(3)、`test_replay_archive.py`(6)、`test_mark_task_cli.py`(15)、`test_lint_tasks_md.py`(13)、`test_dispatch_renderer.py`(13)，合计 ~75 新增测试
- 旧 `pg-build` 保留 v1 17 处 `pg_context_chain.*` 散落调用，不动
- `actions.health_check` 是 opt-in，不会给已有项目强加

## [0.5.0] - 2026-06-28

### 变更

- **破坏性**：`.pg/project.yaml` 顶层字段名统一为 `snake_case` 风格，与 `review_level` 等已有字段保持一致。原 PascalCase / camelCase / kebab-case 字段硬切换，不保留旧名：
  | 旧名 | 新名 |
  |---|---|
  | `verifyMerge` | `verify_merge` |
  | `verifyMerge.skipTestsIfNoConflict` | `verify_merge.skip_tests_if_no_conflict` |
  | `flyway.migration-path` | `flyway.migration_path` |
  | `git.default-branch` | `git.default_branch` |
  | `apply_change_rules` | `build_rules` |
- **破坏性**：JSON Schema (`project.schema.json`) 同步更新为新字段名，YAML 不再接受旧名
- **破坏性**：`pg-parse-config.py` 输出 JSON 段同步重命名（`verify_merge` / `flyway.migration_path` / `git.default_branch` / `build_rules`）
- **破坏性**：`apply_change_rules` → `build_rules` 重命名（语义更准确：所有规则均注入 `pg-build/dev` 与 `pg-build/verify` prompt）。影响面：
  - `pg-parse-config.py` 的 `WORKFLOW_KEYS["pg-build"]` 列表项
  - `pg-pipeline-runner.py` 的 `config.get("build_rules")`、`_enrich_context_with_prompt_injection` 读取与 `_merge_prompt_injection` 拼接逻辑（行为不变）
  - 测试套件 `test_pg_parse_config_rules.py` / `test_prompt_injection.py` 中的 SAMPLE_CONFIG、断言与方法名同步更新
  - 文档：`pg-build/SKILL.md`、`pg-propose/references/config-fields.md`、`pg-fix-issue/SKILL.md` 全文字段引用同步
- `pg-verify-and-merge/SKILL.md` 全文字段引用同步更新；CLI flag `--default-branch` 保持不变（与 YAML 字段是两套命名体系）

## [0.4.0] - 2026-06-27

### 新增

- `pg-run`：菜单式运行时命令（位于 `src/runtime/bin/`），从 `.pg/project.yaml` 读取配置逐级菜单引导用户选择并执行模块/环境/角色操作。支持 `--module/--env/--role/--action/--cmd` 直达模式跳过多层菜单
- `pg-parse-config.py --resolve-env <name>`：新 CLI flag，按需解析 environment 的 `resolved_actions`（含 `{env}.{role}.{instance}.{action}` 展开的 `cmd` + `timeout_seconds`），供 pg-quick-build worker 等在运行时按需取用，不再提前注入所有 env 详情
- 示例模板 `lib/common.sh`：hooks 协议 SSOT 公共库（`examples/shell/hooks/lib/`），包含 `pg_resolve_paths`（caller × session 双维度日志目录路由）、`kill_port`、`wait_for_port`、`wait_for_port_with_monitor`、`kill_pid_file` 等工具函数
- 示例模板正则测试：`examples/shell/hooks/tests/test_template_hooks.py`（143 个断言），验证 5 个模板与 `lib/common.sh` 的一致性、bash 语法正确性、条件 source 守护完整性
- `pg doctor` 新增检查项：`.pg/hooks/lib/common.sh` 存在性校验，不含 `pg_resolve_paths` 或文件缺失时输出 WARNING
- pg-regression run 目录系统：单次 run 自动创建 `<suite>-<YYYYMMDD>-<NN>/` 目录，包含 `temp/`、`<env>/logs/`、`fix-issues/<idx>-<slug>/`（含 1-prompt.md / 2-agent.log / 3-result.json）、`fix-test/<idx>-<target-slug>/`、`fix-issue-runner-summary.md`、`report.md`
- pg-regression fix-test 历史留痕：`pg-record-fix-test.sh` 记录每次 fix-test 调用的 prompt、response 和结构化结果
- pg-regression `--run-dir` CLI 参数：复现时可指定 run 目录，不指定则自动按 mtime 选取最新目录
- pg-regression runner 工作目录脏检查：进入 fix-issue 循环前检查 working tree 是否干净（Phase 2a 应已 commit test fix），脏则跳过该 issue

### 变更

- **破坏性**（v4 hooks 协议）：`--change` 改为 `--session`（canonical CLI flag）。`--change` 保留 1 版本作为 deprecated alias（输出 WARN）
- **破坏性**（v4 hooks 协议）：`--skill` 语义拆分，引入 `--caller` 别名（互为 alias），硬缺省从 `pg-build` 改为 `ad-hoc`。SKILL 调用必须显式传 `--skill pg-build|pg-regression|pg-fix-issue`，否则落入 `.pg/ad-hoc/` 目录
- **破坏性**（v4 hooks 协议）：`pg-invoke-hook.py` 新增 `--log-dir`（调试覆盖）和 `--timeout-override`（ad-hoc 调试，输出 WARN）标志
- **破坏性**（v4 hooks 协议）：日志目录路由重构为 caller × session 双维度：
  - `pg-build` → `.pg/changes/<session>/2-build/<env>/logs/`
  - `pg-regression` → `.pg/regression/<session>/<env>/logs/`
  - `pg-fix-issue` → `.pg/fix-issue/<session>/<env>/logs/`
  - `ad-hoc` → `.pg/ad-hoc/<session>/<env>/logs/`（新顶级目录）
- **破坏性**（v4 hooks 协议）：`pg-run-hook.py` 注入的 env var 变更：
  - 新增 `PG_RUN_CALLER` / `PG_RUN_SESSION` / `PG_HOOK_LOG_DIR` / `PG_LOG_FILE` / `PG_RESULT_FILE`
  - `PG_SKILL_NAME` / `PG_CHANGE_NAME` 降级为 deprecated alias（1 版本兼容）
  - `PG_RUN_CALLER` 硬缺省 `ad-hoc`（替代旧 `PG_SKILL_NAME` 的 `pg-skills`）
- **破坏性**（v4 hooks 协议）：所有 SKILL.md（pg-build / pg-fix-issue / pg-regression）文档中 `--change` 统一改为 `--session`，`--skill` 显式标注说明
- **破坏性**：`pg-pipeline-runner.py` 删除 `_build_fix_issue_context` / `_build_fix_issue_context_gate`——runner 不再解析 verify/gate 报告的结构化字段（issue_title / expected / actual / root_cause_phase / gate_gap_id / file_pos / fix_hint 等），改为直接注入 `verify_report_path` / `gate_report_path` 让 fix agent 读取源报告
- **破坏性**：`_SUB_TRACK_FIELDS["fix"]` 删除结构化字段（`issue_title`、`verification_step`、`expected`、`actual`、`root_cause_phase`、`affected_tasks`）；新增 `verify_report_path`、`design_doc_path`、`tasks_path`
- **破坏性**：`_SUB_TRACK_FIELDS["fix-gate"]` 删除结构化字段（`gate_gap_id`、`audit_step`、`file_pos`、`fix_hint`、`affected_tasks`）；保留路径字段
- **破坏性**：`pg-quick-build` 不再切分支——直接在当前分支修改代码。删除 Phase 0.5 和 Phase 1.2 的 `git checkout -b` 步骤，worker 不再接收 `branch` 字段，return schema 删除 `branch` 字段，self_check 从 5 项减为 3 项（删除 scope_creep和 commits_count 检查）
- **破坏性**：`pg-quick-build` worker prompt 不再注入完整 env 详情（instances / actions），改为注入 `--resolve-env` 按需获取
- **破坏性**：pg-regression SKILL.md 的 `--change` 改为 `--session`，`regression-<suite>` 的命名约定保留但不再用于 change 前缀
- **破坏性**：pg-fix-issue 的 `change_name` 约定由复用 pg-build 的 change 名改为独立生成 `fix-<YYYY-MM-DD>-<bug-slug>`，日志目录走 `.pg/fix-issue/` 而非 `.pg/changes/`
- `pg-init-project` Phase 3 新增步骤：复制 SSOT 公共库 `.pg/skills/examples/shell/hooks/lib/common.sh` 到 `.pg/hooks/lib/common.sh`
- `pg-pipeline-runner.py` help text `--change` 改为 `--session`，thin wrapper 转发同步 v4 协议
- 所有示例 hook 模板（role-start/stop/logs, env-prepare/clean）头部新增 `lib/common.sh` 条件 source + `pg_resolve_paths` 调用，新增 v4 env var 注释文档
- README.md §Hook 协议大幅扩展：新增 §7.1.2 "v4 协议 — caller × session 双维度路由" 章节，新增 §7.1.3 "三种使用场景的调用范式" 章节

### 修复
- `pg-pipeline-runner.py:dispatch_fix_action` / `dispatch_fix_gate_action` 在 resume/record 路径上缺少 `_change` 上下文注入，导致 fix agent 拿到空的 change 名（`_change` 键的 setdefault 在 filter_track_context 之前未被填充）

### 备注
- v4 hook 协议路由表三处同步（runtime `pg-invoke-hook.py:pg_log_dir_for_skill`、`pg-pipeline-runner.py:_pg_log_dir_for_skill`、`lib/common.sh:pg_resolve_paths`），改动任一处前需同步另外两处
- `pg-regression` run 目录重构后，旧 `results/` 和 `summary.*.md` 不再写入，统一改为 `<suite>-<date>-<NN>/` 子目录结构
- 测试覆盖更新：`test_invoke_hook.py` 新增 `TestV4Protocol`（10+ 测试覆盖 caller×session 路由、auto-session、deprecated alias）、`test_prompt_template.py` 用例全面适配"必读源报告"新范式、`test_template_hooks.py` 覆盖 SSOT 同步

## [0.3.0] - 2026-06-26

### 新增
- `pg-invoke-hook.py`：runtime 层独立 CLI（位于 `src/runtime/bin/`），承担 env-level (prepare_env/clean_env) + per-role (start/stop/logs/tail) hook 的 spec 渲染与 `pg-run-hook.py` 调度。供 `pg-build` / `pg-fix-issue` / `pg-regression` 三个 SKILL 共享调用，**统一 hooks 协议入口**
- `pg-invoke-hook.py` 支持 env-level actions (`prepare_env` / `clean_env`)，无需 `--role` / `--instance`；spec.role/instance_host 留空，log_path 走 `env.<action>.log`
- `pg-invoke-hook.py` 错误路径：missing --role / missing --instance / unknown env / role / instance / action, 全部 exit 1, stderr 输出明确错误信息

### 变更
- **破坏性**（隐式）：`pg-pipeline-runner.py:cmd_invoke_hook` 不再内联实现 spec 渲染与 pg-run-hook.py 调度, 改为 thin wrapper 转发到 `pg-invoke-hook.py`. CLI 形式 (`pg-pipeline-runner.py invoke-hook ...`) 100% 向后兼容, 但 LLM 面向的新代码统一写 `pg-invoke-hook.py invoke-hook ...`
- **破坏性**：pg-build runner 的 `_build_stage_context.environment.hooks.invocation.command_template` 由 `pg-pipeline-runner.py invoke-hook` 改为 `pg-invoke-hook.py invoke-hook`
- **破坏性**：pg-regression SKILL.md Phase 0.2 (prepare_env) 改为调 `pg-invoke-hook.py --action prepare_env`, Phase 0.3 (启 services) 改为编排器循环调 `pg-invoke-hook.py --action start`, 不再走 `start-services.sh`
- **破坏性**：`pg-regression/scripts/start-services.sh` 已删除（手写 yaml 解析 + spec 渲染, 绕过 hooks 协议; 由编排器循环调 `pg-invoke-hook.py` 替代, 与 pg-fix-issue 风格一致）
- README.md §Hook 协议: LLM ↔ Runner 通信约定 改为以 `pg-invoke-hook.py` 为唯一入口; 添加向后兼容说明

### 修复
- N/A (本次以重构为主, 无 bug 修复)

### 备注
- pg-build / pg-fix-issue / pg-regression 三个 SKILL 不再互相依赖 runner 路径, hooks 协议入口在 runtime 层单一实现
- 升级路径: 旧 prompt 含 `pg-pipeline-runner.py invoke-hook` 的 sub-agent / 编排器仍可工作 (thin wrapper 透传), 推荐新 prompt 改写为 `pg-invoke-hook.py invoke-hook`
- 新增 2 个测试文件: `test_invoke_hook.py` (21 个测试覆盖 canonical + thin wrapper) + `test_invoke_hook_env_level_actions.py` (6 个测试覆盖 env-level actions)

## [0.2.0] - 2026-06-24

### 新增
- `pg upgrade [version]` 命令：替代 `pg sync`，支持指定版本号（如 `pg upgrade 0.2.0`），自动补 `v` 前缀作为 git tag 拉取
- `pg upgrade --list`：fetch 远程 tags，列出所有可用版本并标记当前版本
- `pg upgrade --interactive`：fetch 目标 ref，列出差异文件，检测本地冲突

### 变更
- **破坏性**：`pg sync` 命令重命名为 `pg upgrade`
- **破坏性**：`--check` 标志重命名为 `--list`
- **破坏性**：移除 `.pg-version` 文件。改用 `.pg/skills/VERSION` 作为版本唯一来源
- `pg doctor` 改为检查 `.pg/skills/VERSION` 而非 `.pg-version`
- `pg init` 不再写入 `.pg-version` 文件

### 修复
- `_normalize_ref` 逻辑：纯数字版本号（如 `0.2.0`）自动补 v 前缀，分支名（`master`、`feature/x`）保持原样

## [0.1.0] - 2026-06-22

### 新增
- 从 webvirt 项目提取 pg-* skills、commands 和 agents
- 13 个技能：pg-propose, pg-build, pg-quick-build, pg-fix-issue, pg-regression, pg-archive, pg-verify-and-merge, pg-propose-refine, pg-browser-testing-with-devtools, pg-systematic-diagnosing, git-workflow-and-versioning, security-and-hardening, using-agent-skills
- 8 个斜杠命令：/1-pg-define, /2-pg-propose, /2b-pg-quick-build, /2.1-pg-propose-refine, /3-pg-build, /4-pg-regression, /5-pg-fix-issue, /6-pg-archive
- 5 个子代理：explore, pg-manager, pg-build/{dev,test,verify,fix,fix-gate,gate}, pg-fix-issue/{executor,fix-and-pr}, pg-regression/fix-test, pg-quick-build/worker
- L1 runtime 骨架：src/runtime/{bin,lib,spec}
- 3 种语言示例模板：java-maven, go, typescript

### 备注
- 初始"骨架 + 去 webvirt"版本
- Python 测试夹具已泛化，使用 `<module-name>` 占位符
- 完整 hook 协议在 0.2.0 实现
- 完整 `pg` CLI 在 0.2.0 实现
