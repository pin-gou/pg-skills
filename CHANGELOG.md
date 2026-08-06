# 变更日志

所有对 pg-skills 的重要变更均记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.9.1] - 2026-08-06

### 新增

- **define-summary.yaml schema + 产物协议**：pg-1-define 新增"定界后环境验证"可选环节（用户明确授权后调 describe_env 探测真实环境，逐 V-* 讨论验证方法，落盘 `define-summary.yaml`）；新增 `src/runtime/spec/define-summary.schema.json`（schema v1）、`examples/define-summary.example.yaml`、`src/core/workflows/skills/pg-propose/references/define-summary-templates.md`
- **pg-propose 阶段 1.8 — 加载 define-summary.yaml（条件性）**：当 `.pg/changes/<change-id>/0-define/define-summary.yaml` 存在时自动加载并校验，V-* 状态（`verifiable` / `degraded` / `skipped`）作为阶段 2 全产物写作的强制 context；向后兼容（不存在时跳过）
- **env_resource_refs 强引用机制**：design.md / scenario-\*.yaml 必须引用 define-summary 中已声明的 `{env.<段>[name=<资源名>]}` 占位引用，由 `pg-validate-proposal.py` 交叉校验；verifiable 的 V-* 必填引用，non-verifiable 必须为空
- **pg-validate-proposal.py 新增 `define-summary` 校验子命令**：5 项校验（文件存在性 / 结构校验 / change_id 一致性 / target_environment 与 env-description 一致性 / env_resource_refs 交叉校验）
- **pg-gen-tasks-skeleton.py v1.3**：新增 `--define-summary` 参数，verify 章节自动注入 V-* 状态声明（`define-summary 对账` 子段：verifiable / degraded / skipped 分组）
- **migrate-define-summary.py 兼容性迁移工具**：旧格式 id (`V-NNN`) 改写为 `V-{track_id}-{seq}` 新格式，持 `--dry-run` 模式
- **progress-monitor 服务端重构**：新增 `server/change-info.ts`（变更信息解析）、`server/events.ts`（事件追踪）、`server/path-utils.ts`（路径工具）、`server/phase-telemetry.ts`（阶段遥测）；新增 `scripts/start.mjs` 启动脚本；新增 `tests/monitor-utils.test.ts`（291 行测试）
- **progress-monitor 前端优化**：EventLog / ManifestViewer / PipelineProgress 组件重构；新增 `status.ts` composable、`pipelineStatus.ts` 类型定义；`pipelineStore.ts` 状态管理优化；`vite.config.ts` 重构（405 行→更精简）
- **pg-propose SKILL.md 升级到 v1.2.0**：todo 清单 12→13 项；新增阶段 1.8 完整流程与 context 注入契约；附录项编号同步更新
- **pg-run 更新**：`describe_env` 调用入口对齐

### 变更

- **describe_env 提前到 pg-define**：原在 pg-propose 阶段 1.6 的 describe_env 调用，现在 pg-define 的"定界后环境验证"环节即可调用（产物位置不变：`.pg/changes/<change-id>/env-description.yaml`）；pg-propose 阶段 1.6 保持向后兼容
- **pg-1-define.md 约束更新**：明文例外——「定界后环境验证」环节经用户明确确认后可落盘 `define-summary.yaml` 及 `env-description.yaml`（其他环节仍遵守不自动记录硬约束）
- **progress-monitor Vite 配置重构**：`vite.config.ts` 从 405 行精简为更专注的配置

### 修复

- **pg-propose 多项问题修复**：`pg-gen-tasks-skeleton.py` 产物生成、校验逻辑、阶段路由等问题
- **pg-build 执行时 bug 修复**：runner 执行过程中的 bug 修复
- **pg-build bootstrap 重复执行问题**：修复 bootstrap 阶段重复执行的问题
- **pg-init 集成问题**：multi-tool 集成场景下的初始化问题修复

### 备注

- 6 commits（含 2 个 merge），38 文件变更（+3520 / -753 LOC）
- 新增 9 个文件：`src/runtime/spec/define-summary.schema.json`、`examples/define-summary.example.yaml`、`src/core/workflows/skills/pg-propose/references/define-summary-templates.md`、`src/runtime/bin/migrate-define-summary.py`、`src/core/workflows/scripts/tests/test_define_summary.py`、`src/core/workflows/scripts/tests/test_env_resource_refs.py`、`src/core/workflows/scripts/tests/test_tasks_skeleton_define_summary.py`、`src/runtime/tests/test_migrate_define_summary.py`、`tools/progress-monitor/tests/monitor-utils.test.ts`
- 新增 4 个 server 端文件：`tools/progress-monitor/server/change-info.ts`、`events.ts`、`path-utils.ts`、`phase-telemetry.ts`
- `define-summary.yaml` 向后兼容：pg-propose 阶段 1.8 仅在文件存在时执行，旧项目无此文件则跳过，不影响现有流程
- pg-propose SKILL.md 版本从 1.0.1 升级到 1.2.0（跳过了 1.1.0 的版本号）

## [0.9.0] - 2026-08-03

### 移除（破坏性）

- **pg-propose-refine 流程删除**：SKILL 目录、`/2.1-pg-propose-refine` 命令、`review-notes.md` 产物、`pg-auto-refine-check.py` 一并清理；6 类 LLM 自审清单改由 `pg-validate-proposal.py` 机械校验替代
- **pg-fix-issue 大幅精简**：SKILL.md 从 6 阶段瀑布流重构为扁平流程，删除 ~2900 行；`fix-issue-v2` 命名空间清出
- **`escalate_threshold` 字段删除**：review ESCALATE 改由 `pass_threshold` + reducer P0 硬约束统一处理
- **`env-capability.yaml` 机制废弃**：改用 per-change `env-description.yaml` + describe_env 脚本
- **`scenario-prepare` sub-agent / `summary.yaml` / `${VAR}` 占位符一并清理**：slash 命令 8→7、SKILL 11→10

### 新增

- **v6 hook 协议 — describe_env**：env-description schema + describe-env.sh 模板 + `pg-invoke-hook.py --action describe_env` + `PG_CHANGE_ID` / `PG_OUTPUT_PATH` 注入；pg-propose 阶段 1d.5 改为调 describe_env 读 per-change env-description.yaml
- **`explore` sub-agent**：新增 `.opencode/agents/explore.md`，代码探索代理优先使用 CodeGraph，不可用时降级到 glob/grep/read
- **pg-validate-proposal.py 3 条新校验规则**：V-* 映射、scenario 引用防护、章节编号连续性（COMMON_DECISIONS 常量块固化 5 项 common decisions）
- **pg-build bootstrap 防御性加固**：dirty branch / 重复 bootstrap / idle_next 检测；新增 `test_orchestrator_idle_next.py` 与 `test_describe_env_protocol.py`（26 tests）
- **pg-quick-build v2.1 — env-description 真实探测 + V-* 可达性过滤**：
  - SKILL.md 新增 Phase 0.5（白名单触发：用户需求涉及多环境 / K8s / DB / 外部服务 / 用户明确要求时执行）
  - 调用 `pg-invoke-hook.py --caller pg-quick-build --action describe_env`（caller `pg-quick-build` 为 v2.1 新注册合法 caller）
  - 6 段资源拓扑注入 `design.context.environment`，**不**写 `.pg/changes/`（pg-quick-build 零产物承诺保留）
  - 新增 `pg-quick-build-env-capability.py`（纯函数）做 V-* 可达性判定 + `covers_v` 自动过滤
  - 不可达 V-* 进入 `design.context.env_capability.unverifiable_v` 留痕
  - 强停条件新增 2 项：`unverifiable_v > verifiable_v` 时建议走 pg-propose；describe_env 调用失败 abort
- **runtime caller 白名单扩展**：注册 `pg-quick-build` 为合法 caller 标识
  - `src/runtime/spec/hook-env-vars.yaml` enum 同步加入
  - `pg-invoke-hook.py:KNOWN_CALLERS` + `DESCRIBE_ENV_CALLERS` 加入
  - 日志路由：`.pg/quick-build/<session>/<env>-logs/`（独立命名空间，不与 `.pg/changes/` 混）
  - env-description 输出路径：`.pg/quick-build/<session>/env-description.yaml`
  - `pg-invoke-hook.py:pg_log_dir_for_skill` + `build_env_level_hook_spec` 路由表同步
  - `examples/shell/hooks/lib/common.sh:pg_resolve_paths` 同步（pg-init-project 复制路径）

### 变更

- **pg-quick-build worker prompt 模板**：新增 §3.5 "Env 上下文" 段（仅当 `design.context.environment` 非空时存在），worker 在 dev/verify 阶段引用具体资源 ID
- **pg-quick-build 收尾摘要**：SUCCESS / FAILED 摘要新增 "Env 探测" 字段（source + verifiable_v + unverifiable_v）
- **pg-quick-build 强停条件表**：新增 2 项（unverifiable 占比 + describe_env 失败）
- **pg-quick-build ⛔ 禁令**：新增 1 条（禁止把 env-description 复制到 worker prompt 之外的产物）

### 修复

- **pg-build resume 失败修复**：workflow_failed 状态下 resume 上下文丢失问题
- **pg-build 执行过程多项问题**：verify.yaml 渲染、prepare_env 阶段错选、report_path 注入、review ESCALATE 计数等
- **错误分类 SSOT 对齐**：restart env 错误码从 `unknown` 修订为 `health_check_fail`；pg-verify-and-merge Phase 4 拆分为成功/失败两条路径

### 备注

- 26 commits，100 文件变更（+6468 / -5361 LOC）
- 新增：`src/runtime/spec/env-description.schema.json`、`examples/env-description.example.yaml`、`examples/shell/hooks/describe-env.sh`、`.opencode/agents/explore.md`、`src/opencode/skills/pg-quick-build/scripts/pg-quick-build-env-capability.py` + `tests/test_env_capability.py`（25 tests）
- 删除：`pg-propose-refine/` SKILL 目录、`pg-gen-env-fingerprint.py`、`check-env-capability.sh`、`check-review-cache.sh`、`scenario-prepare.md` / `scenario-prepare.yaml`
- pg-quick-build v2.1 向后兼容：`source = "skip"` 时走 v2.0 行为（仅靠 `--resolve-env` 拿 actions），现有项目无破坏；worker.md（pg-quick-build/worker.md）0 行改动——保持 v2.0 自包含协议
- 不动 pg-propose / pg-build / pg-fix-issue / pg-regression / pg-verify-and-merge / pg-archive 任何代码

## [0.8.3] - 2026-07-19

### 新增

- **pg-build 集成验证不可 SKIP**：基于 `stages[*].environment.required`，跨环境依赖必须满足才能继续 pipeline
- **pg-propose API 端点强制完整性**：`design.md` API 端点必须含完整 Request/Response Body，缺失时 `pg-validate-proposal.py` 输出 `api_endpoint_incomplete`
- **pg-build scenario-fix drift.md 记录**：scenario-fix 诊断后输出 drift.md，记录 design 偏移、根因和修复方案
- **pg-build scenario track 浏览器操作**：scenario-execute 支持点击、输入、截图等浏览器操作步骤
- **pg-build workflow_failed reset/resume 用户可选**：workflow_failed 状态下重执行时由用户选择 reset 还是 resume
- **pg-build 启动前脏分支检查**：脏分支时提示提交或 stash，避免意外覆盖
- **AGENTS.md + skills 介绍文档**：AGENTS.md 涵盖架构、SSOT、Agent 协议；`docs/pg-skills.md` + 12 张 SVG 卡片介绍品构技能集
- **pg-propose 防御越界修改 + 移除日期前缀**：propose-review 校验文件路径在 change 目录范围内；新建变更不再加日期前缀

### 变更

- **`build.injections` → `propose.injections` 重命名（破坏性）**：`project.yaml` 既有 `build.injections` 需更新为 `propose.injections`
- **project.schema.json 废弃字段清理**：移除 `test_strategy`、`coding_standards` 等废弃字段；`tools/project-editor` 同步适配
- **pg-build SKILL 文档对齐 + Python 3.7 兼容**：SKILL.md 与 reducer 行为一致；`pg-parse-config.py` 等脚本兼容 Python 3.7

### 修复

- **pg-build `git.default_branch` 配置读取**：修复键名解析错误
- **测试脚本适配 schema 变更**：`test_pg_parse_config_*` / `test_bootstrap.py` / `test_config.py` 等同步更新

### 备注

- 20 commits，75 文件变更（+3943 / -809 LOC）
- 新增：`AGENTS.md`、`docs/pg-skills.md`、`docs/cards/` 下 12 张 SVG 卡片

## [0.8.2] - 2026-07-16

### 新增

- **Scenario Track 机制（破坏性）**：新增 `type: scenario` pipeline track 类型，走独立的 `scenario-prepare → scenario-execute → [scenario-fix → scenario-execute]*` 生命周期，绕过标准 TDVG 五阶段。新增 scenario-prepare / scenario-execute / scenario-fix 三个 sub-agent，`SCENARIO_FIX_CYCLE` sub-pipeline 类型，不参与 gate assessment，`max_fix_retries` 耗尽后 `workflow_failed`
- **manifest v3（破坏性）**：`execution-manifest.yaml` 升级到 schema `2026-06-30`，新增 `enabled` 必填字段、`reason` 字段、`on_conditions_eval` 对象；新增 `type: e2e` 和 `type: scenario` 类型
- **pg-gen-scenario.py**：按 track 生成 `scenario-<track>.yaml` 骨架（含 sentinel placeholder），导出 `check_scenario_placeholders()` / `check_scenario_file()` 供下游校验
- **pg-propose v3.7**：流程精简（阶段 2e/2f 收敛到阶段 2g）；占位符校验（递归检查 `<...>` / `/.../` / `S-<unique-name>` 等）；全推荐自动 refine（满足三条件时跳过人工 refine）
- **pg-build workflow_failed 自动 reset** + **多 scenario.yaml 适配** + **scenario-execute evidence 去重**

### 变更

- **`test_key` 死字段移除**：从 manifest 及相关逻辑中清理
- **pg-build seq / scenario.prepare 提示词优化**

### 修复

- **pg-build 执行完成后跳过 archive**：修复 pipeline 成功完成后未自动触发 archive 的 bug
- **pg-build 边缘 case 修复**：多项运行中的边缘 case 问题

### 备注

- 96 commits，60 文件变更（+8758 / -196 LOC）
- 新增 11 个测试文件：`test_scenario_track.py`、`test_integration.py`、`test_orchestrator_gate_precheck.py`、`test_bootstrap.py` / `test_bootstrap_v3.py`、`test_config.py`、`test_orchestrator.py`、`test_manifest_v3.py`、`test_v37_optimizations.py`、`test_three_product_consistency.py`

## [0.8.1] - 2026-07-14

### 新增

- **Verify / Gate 按 track 关闭（破坏性）**：`project.yaml` 新增 `tracks.<id>.verify_enabled` / `gate_enabled`，关闭后沿用 review 的 silent-skip 模式；simple track 自动关闭；manifest 的 `phase_prompts` 是否含该 phase 作为 SSOT 派生
- **design.md 缺陷协议（v2.7）**：fix-review agent 检测到 design/tasks 文档错误时写 `design_md_fault`，reducer 立即触发 `workflow_failed`
- **SubPipeline P0-A 字段增强**：fix_cycle / gate_fix_cycle / review_cycle 注入 `parent_report_path` / `escalation_reason` / `failed_v_tasks` / `created_at`
- **P0 硬约束机制**：`profile_loader.py` 新增 `p0` 字段，reducer 检测 P0 FAIL 时强制 escalate（绕过 score 阈值）
- **Review rule docs 注入**：dispatch.py 修复死代码，将 `.pg/code-review/<profile>/*.md` 注入 ctx
- **pg-run 停止+清理统计算法**：菜单统一展示停止+清理的统计表

### 移除（破坏性）

- **`review_level` 字段全量移除**：schema.json、prompt-templates、agent docs、pg-init-project 推断逻辑全量清理；review phase 开关统一到 `code_review_enabled` / `code_review_profiles` / `code_review_languages`。迁移：`security` → `code_review_profiles: [security]` + 拷 profile 模板；`standard` → 删除字段；`none` → `code_review_enabled: false`
- **gate agent "步骤 6 安全敏感变更检查"删除**：auth/secret/permission/concurrency 由 review phase 的 security profile 自动执行，Gate Assessment 从 9 项减为 8 项
- **`pg-init-project` Phase 2 review_level 推断逻辑删除**：不再按 language 自动推断；security profile 保持 opt-in

### 备注

- 9 commits，55 文件变更（+3862 / -314 LOC）
- 新增 5 个测试文件：`test_dispatch_review_rule_docs.py` / `test_state_verify_gate.py` / `test_detect_skip_disabled.py` / `test_reducer_silent_skip.py` / `test_phase_gate_section.py`
- 不向后兼容：旧 `project.yaml` 仍写 `review_level` 会触发 schema validation 警告

## [0.8.0] - 2026-07-09

### 新增

- **pg-build v2.6 code-review 阶段（破坏性）**：`test → dev → review → verify → gate` 五阶段模型中新增 review 子阶段；`pg-build/review` sub-agent 执行静态代码审查（R-* 检查项），`pg-build/fix-review` 处理 review 阶段修复（独立 `review_fix_cycles`，默认 3 次）
- **pg-build review phase Profile 引擎**：`profile_loader.py` 支持 `.pg/code-review.yaml` 多 profile（default / java-spring / go / vue3 / security），按 language 自动派发，Union 合并语义
- **pg-build review 子 pipeline + v3.x SSOT 迁移**：review escalate 创建 `REVIEW_CYCLE` 子 pipeline；`code_review_*` 字段从 state 删除，改由 `execution-manifest.yaml` 的 `phases.review` 作为 SSOT
- **pg-propose v3.3 code-review 适配**：tasks.md 按 `code_review_enabled` 含 4 或 5 sub，`phase_prompts` 4 必填 + review optional
- **pg-init-project v0.3 code-review 适配**：Phase 2.5 根据 module languages 自动派发 `.pg/code-review/` 目录
- **code-review 示例模板**：`examples/code-review/` 目录新增 15 个文件，覆盖 5 个 profile
- **pg-fix-issue v3.1 → v3.2 重构**：6 阶段流程（Phase 0-5）；`pg-fix-issue-v2` 命名空间清出
- **pg-propose tasks.md 骨架脚本外化 + 两阶段填充法**：`pg-gen-tasks-skeleton.py` 替代 LLM 手工生成 heading；新增 `--selected-stages` 参数
- **品构品牌命名**：Workshop 文档标题改为「品构 Workshop」，slogan `让 AI 写出可托付的代码`；sec1 重构为 5 段叙事，新增 SVG 品牌视觉
- **pg-run health_check 菜单**：`Instance.health_check` 选项，配合 `actions.health_check` 声明使用
- **pg-define 约束收紧**：禁止生成 `design.md` / `proposal.md` / `tasks.md`（由 pg-propose 独占）
- **Python 3.7 兼容**：`pg-gen-tasks-skeleton.py` 等脚本兼容

### 变更

- **code_view → code_review 全量重命名（破坏性）**：agent 文件、prompt 模板、状态字段、事件枚举、测试文件全量迁移；既有 snapshot 需迁移
- **pg-propose SKILL.md 重构**：从 810 行压缩到 303 行，模板字符串下放到 `references/`；新增 `references/review-checklist.md` / `config-fields.md`
- **pg-build SKILL.md 更新**：新增 v2.6 review 阶段完整文档 + v2.5 `--result-json` + v2.4 result.json 强制落盘协议

### 修复

- **pg-gen-tasks-skeleton.py `--selected-stages` 参数缺失**：v3.2 skeleton 错误包含所有 stage
- **pg-gen-manifest.py phase_prompts 校验**：minProperties=4 + 1 optional（review），maxProperties=5

### 备注

- 19 commits，78 文件变更（+8220 / -2205 LOC）
- 新增 5 个测试文件：`test_state_review.py` / `test_review_section.py` / `test_profile_loader.py` 等
- 依赖：`.pg/skills/examples/code-review/` 模板目录必须存在（subtree 拉取），缺失时仅生成 default profile 并 WARN
- 不向后兼容：tasks.md 章节号 N 跨 change 不再一致

## [0.7.0] - 2026-07-05

### 新增

- **pg-build v2 取代 v1（破坏性合并）**：原 `pg-build-v2` 重命名为 `pg-build`，吸收原 v1 行为（过程式状态机 + 51 个 `save_state` 调用彻底替换）；SKILL.md 重写，直接暴露 5 个 CLI 子命令
- **路径简化（破坏性）**：caller 维度日志目录从 `<env>/logs` 改为 `<env>-logs`，影响 5 个 caller 命名空间；pg-build 自动迁移既有项目，其它 caller 手工迁移
- **execution-manifest.yaml 成为环境 SSOT（破坏性）**：`environment.yaml` 弃用，per-change 环境选择写入 manifest；pg-propose 阶段 2d 产物硬约束 4 个文件
- **pg-verify-and-merge AffectedTracks 5 层 fallback**：新增 manifest 优先级；`--json-only` flag 抑制 banner
- **pg-verify-and-merge lint 日志独立空间**：`3-merge/lint-logs/lint-<track>-<ts>.log`，与 2-build 解耦
- **pg-regression A/B/C 三分类自动修复边界**：A 类自动修不附 rationale；B 类附 rationale；C 类禁止自动修
- **`pg-check-fix-test-boundary.py`**：扫描 git diff 命中 C5/C6/C7/C11 硬规则时立即回滚，转写 unfixableIssues
- **pg-regression `skippedUnits` 分析（Phase 2b）**：phase1-failures.json 含 skippedUnits，按原因分类
- **`.pg/regression/<suite>.json` schema 扩展**：`auto_fixed` / `rationale` / `category` 字段
- **`pg-build-result` 工具**：独立 result 落盘 CLI，`--output-path` 强制落盘
- **env-action 钩子架构拆分（v2.1.1）**：从主循环拆出 `env-action` / `env-action-result` 子命令；`--success` 布尔语义明确
- **`pg-parse-test-results.py` skipped 解析**：playwright + junit 输出解析新增 skipped 单元聚合

### 修复

- **pg-build runner `--tasks-updated` 参数位置错误**：positional 改为 `--flag` 形式
- **pg-build runner record 传参错误**：新增 `--result-json` 参数
- **pg-build runner `pg-invoke-hook` 漏传 `--skill`**：`--skill` 参数硬编码传入
- **pg-build verify → fix 派遣路由**：dispatch_file 注入 `context.verify_report_path`
- **pg-build prepare_env 阶段错选 / archive 后 `pipeline.events` 路径错误**：state persist bug 修复
- **pg-fix-issue `max_per_iteration_subcalls` / `tracks.<id>.max_fix_retries` 移除**：统一收敛到 `max_iteration_count`
- **`pg-parse-config.py` banner 截断 + 边界对齐**：新增 `--json-only` flag，banner 分隔符 64→60

### 变更

- **pg-fix-issue actions 列表加 `restart` / `health_check`**：actions 一致化
- **`pg-invoke-hook.py` 新增 flag**：`--log-dir` / `--timeout-override` / `--no-wait-for-bg` / `--wait-for-completion`
- **编排器职责收紧**：禁止读取 `dispatch.md`，禁止修改 prompt 模板
- **`build_rules` 字段注入扩展**：同时注入 `pg-build/dev` 与 `pg-build/verify`
- **pg-parse-config.py Maven Surefire 解析清理**
- **pg-propose tasks.md 两阶段骨架填充法 + track 级 `on_conditions`**

### 备注

- 39 commits，144 文件变更（+9191 / -25260 LOC）
- 新增 9 个测试文件：`test_derive_result_path.py` / `test_error_path.py` / `test_fix_routing.py` / `test_pg_build_result.py` / `test_record_flags.py` / `test_record_result_json.py` / `test_event_log.py` / `pg-check-fix-test-boundary.py` 等
- 路径简化对既有项目有迁移成本（pg-build 自动迁移，其它 caller 手工）
- `execution-manifest.yaml` 取代 `environment.yaml` 是破坏性变更
- `pg-skip-agents-md-migration` 兜底开关：CI 中可设置 `PG_SKIP_AGENTS_MD_MIGRATION=1` 跳过 AGENTS.md drift 清单生成

## [0.6.0] - 2026-07-02

### 新增

- **pg-build-v2 事件溯源引擎**：Event Sourcing + Reducer 纯函数取代过程式状态机；`pipeline.events` append-only JSONL 作为唯一持久化入口；YAML 模板与代码解耦；SubPipeline 递归复用 reducer
- **pg-build-v2 v2.1 pipeline reliability**：record 原子化 commit；Sub-agent JSON schema 校验 + `evidence_missing` hard fail；5 维 gate-score（≥ 80 通过）；checkpoint/resume 机制
- **pg-build-v2 v2.2 dispatch 提示词优化**：env.instances/hooks 按 phase 条件注入；标题简化；删除旧"返回格式"段
- **v1/v2 行为对齐**：3 个共享 helper 统一 v1/v2 分支创建 / init commit / context-chain / manifest 校验
- **mark-task CLI**：`state.json` 是 SSOT，`tasks.md` 转为派生视图；配套 CI lint 检测违规变更
- **`actions.health_check`**：声明才生成；支持 HTTP 与 TCP 探针
- **pg-agent workflow + Phase 5**：新增 `CALLER_PG_AGENT` 路由；`pg-init-project` Phase 5 治理 AGENTS.md drift
- **pg-run 菜单增强 + symlink 管理**

### 修复

- **Simple track 路由 / Final-gate 单次派遣**：`type=simple` 不再走 4 个空 sub；final-gate 不被拆成 4 个 sub
- **pg-build bootstrap `prepare_env` 阶段错选 / Simple track 上下文缺失**：state persist bug 修复
- **pg-verify-and-merge Phase 4 防御性切回 master**
- **`renumber-flyway-migration.sh` 路径 bug**
- **Hook `wait_for_completion` 默认行为修复**：start hook 默认 `wait_for_completion=false`

### 变更

- **`pg-pipeline-runner.py` 删除 v1 漂移检测**：默认启用 `state_v2.enabled=true`
- **`pg-build` SKILL.md 新增 v1/v2 行为对齐章节**
- **`pg upgrade` 从 tag 拉取新版本**

### 备注

- pg-build-v2 与 pg-build 并行存在，通过 `/3-pg-build-v2` 命令访问
- 新增 6 个测试文件：`test_state_v2.py` / `test_runner_v2_shadow.py` / `test_replay_archive.py` / `test_mark_task_cli.py` / `test_lint_tasks_md.py` / `test_dispatch_renderer.py`，合计 ~75 新增测试

## [0.5.0] - 2026-06-28

### 变更

- **`project.yaml` 顶层字段统一为 snake_case（破坏性）**：原 PascalCase / camelCase / kebab-case 字段硬切换：`verifyMerge` → `verify_merge`、`verifyMerge.skipTestsIfNoConflict` → `verify_merge.skip_tests_if_no_conflict`、`flyway.migration-path` → `flyway.migration_path`、`git.default-branch` → `git.default_branch`、`apply_change_rules` → `build_rules`
- **`apply_change_rules` → `build_rules` 重命名（破坏性）**：所有规则均注入 `pg-build/dev` 与 `pg-build/verify` prompt；`pg-parse-config.py` / `pg-pipeline-runner.py` / 测试套件 / 各 SKILL 文档同步更新
- **JSON Schema / `pg-parse-config.py` 输出同步重命名**：YAML 不再接受旧名；CLI flag `--default-branch` 保持不变（与 YAML 字段是两套命名体系）

## [0.4.0] - 2026-06-27

### 新增

- **`pg-run` 菜单式运行时命令**：从 `.pg/project.yaml` 读取配置逐级菜单引导；支持 `--module/--env/--role/--action/--cmd` 直达模式
- **`pg-parse-config.py --resolve-env <name>`**：按需解析 environment 的 `resolved_actions`，供 pg-quick-build worker 运行时按需取用
- **`lib/common.sh` 公共库**：SSOT 公共库（caller × session 双维度日志目录路由 + 端口/PID 工具）
- **`test_template_hooks.py`（143 断言）**：验证 5 个模板与 `lib/common.sh` 的一致性、bash 语法、条件 source 守护
- **`pg doctor` 新增检查项**：`.pg/hooks/lib/common.sh` 存在性校验
- **pg-regression run 目录系统**：单次 run 自动创建 `<suite>-<YYYYMMDD>-<NN>/` 子目录结构
- **pg-regression fix-test 历史留痕 / `--run-dir` CLI 参数 / runner 脏检查**

### 变更

- **v4 hooks 协议（破坏性）**：`--change` 改为 `--session`（保留 deprecated alias）；`--skill` 语义拆分，引入 `--caller` 别名，硬缺省 `ad-hoc`；`pg-invoke-hook.py` 新增 `--log-dir` / `--timeout-override`；日志目录路由重构为 caller × session 双维度
- **`pg-run-hook.py` env var 变更（破坏性）**：新增 `PG_RUN_CALLER` / `PG_RUN_SESSION` / `PG_HOOK_LOG_DIR` / `PG_LOG_FILE` / `PG_RESULT_FILE`；`PG_SKILL_NAME` / `PG_CHANGE_NAME` 降级为 deprecated alias
- **`pg-pipeline-runner.py` 删除结构化字段解析（破坏性）**：runner 不再解析 verify/gate 报告结构化字段，改为注入 `verify_report_path` / `gate_report_path` 让 fix agent 读取源报告；`_SUB_TRACK_FIELDS["fix"]` / `["fix-gate"]` 结构化字段删除
- **`pg-quick-build` 不再切分支（破坏性）**：直接在当前分支修改代码；worker prompt 改为注入 `--resolve-env` 按需获取 env 详情；self_check 从 5 项减为 3 项
- **pg-fix-issue `change_name` 独立生成（破坏性）**：日志目录走 `.pg/fix-issue/` 而非 `.pg/changes/`
- **`pg-init-project` Phase 3 新增 SSOT 公共库复制步骤**
- **示例 hook 模板头部新增 `lib/common.sh` 条件 source + v4 env var 注释**
- **README.md §Hook 协议扩展 v4 caller × session 双维度路由 + 三种使用场景**

### 修复

- **`pg-pipeline-runner.py:dispatch_fix_action` 上下文注入缺失**：resume/record 路径上 `_change` setdefault 在 filter_track_context 之前未被填充

### 备注

- v4 hook 协议路由表三处同步（`pg-invoke-hook.py:pg_log_dir_for_skill` / `pg-pipeline-runner.py:_pg_log_dir_for_skill` / `lib/common.sh:pg_resolve_paths`），改动任一处前需同步另外两处
- 测试覆盖更新：`test_invoke_hook.py` 新增 `TestV4Protocol`、`test_prompt_template.py` 适配"必读源报告"新范式

## [0.3.0] - 2026-06-26

### 新增

- **`pg-invoke-hook.py`：统一 hooks 协议入口**：runtime 层独立 CLI，承担 env-level (prepare_env/clean_env) + per-role (start/stop/logs/tail) hook 的 spec 渲染与 `pg-run-hook.py` 调度；供 pg-build / pg-fix-issue / pg-regression 三个 SKILL 共享调用
- **env-level actions 支持**：`--action prepare_env` / `clean_env` 无需 `--role` / `--instance`；spec.role/instance_host 留空
- **错误路径**：missing --role / --instance / unknown env / role / instance / action 全部 exit 1，stderr 输出明确错误

### 变更

- **`pg-pipeline-runner.py:cmd_invoke_hook` thin wrapper 转发（破坏性）**：CLI 形式 100% 向后兼容，但 LLM 面向的新代码统一写 `pg-invoke-hook.py invoke-hook`
- **pg-build / pg-regression 全面切换到 `pg-invoke-hook.py`**：不再走 `pg-pipeline-runner.py invoke-hook` 与 `start-services.sh`
- **`pg-regression/scripts/start-services.sh` 删除**
- **README.md §Hook 协议改为以 `pg-invoke-hook.py` 为唯一入口**

### 备注

- 三个 SKILL 不再互相依赖 runner 路径，hooks 协议入口在 runtime 层单一实现
- 升级路径：旧 prompt 含 `pg-pipeline-runner.py invoke-hook` 仍可工作（thin wrapper 透传）
- 新增 2 个测试文件：`test_invoke_hook.py` (21 测试) + `test_invoke_hook_env_level_actions.py` (6 测试)

## [0.2.0] - 2026-06-24

### 新增

- **`pg upgrade [version]` 命令**：替代 `pg sync`，支持指定版本号，自动补 `v` 前缀作为 git tag 拉取
- **`pg upgrade --list`**：fetch 远程 tags，列出所有可用版本并标记当前版本
- **`pg upgrade --interactive`**：fetch 目标 ref，列出差异文件，检测本地冲突

### 变更

- **`pg sync` → `pg upgrade` 重命名（破坏性）**：`--check` → `--list`
- **移除 `.pg-version` 文件（破坏性）**：改用 `.pg/skills/VERSION` 作为版本唯一来源
- **`pg doctor` / `pg init` 同步切换到 `.pg/skills/VERSION`**

### 修复

- **`_normalize_ref` 逻辑**：纯数字版本号自动补 v 前缀，分支名保持原样

## [0.1.0] - 2026-06-22

### 新增

- 从 webvirt 项目提取 pg-* skills、commands 和 agents
- 13 个技能 / 8 个斜杠命令 / 5 个子代理
- L1 runtime 骨架：`src/runtime/{bin,lib,spec}`
- 3 种语言示例模板：java-maven、go、typescript

### 备注
- 初始"骨架 + 去 webvirt"版本
- Python 测试夹具已泛化，使用 `<module-name>` 占位符
- 完整 hook 协议在 0.2.0 实现
- 完整 `pg` CLI 在 0.2.0 实现
