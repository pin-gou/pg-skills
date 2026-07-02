# 变更日志

所有对 pg-skills 的重要变更均记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
