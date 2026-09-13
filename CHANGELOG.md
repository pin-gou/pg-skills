# 变更日志

## [1.0.0] - 2026-09-13

**升级前必读（破坏性变更）**
- **`describe_env` 协议移除**：`project.yaml` 不再接受 `environments.<env>.describe_env`；`pg-invoke-hook.py --action describe_env` 已删除；`env-description.schema.json`、`describe-env.sh` 模板及 `PG_CHANGE_ID` / `PG_OUTPUT_PATH` 环境变量一并移除。仍声明 describe_env 的 project.yaml 需删除该段才能通过校验；依赖环境探测产物的流程需改用 `prepare_env` / `clean_env` hook
- **`project.yaml` 移除 `tracks` / `stages` 段**：schema 不再接受这两个顶层键，`pg doctor` 也不再把它们列为必填。仍在使用 tracks/stages 编排的 project.yaml 需删除这两段后才能通过校验（当前工作流已收敛到 pg-auto-pilot，运行时只消费 modules + environments）
- **`project.yaml` 移除死字段**：`verify_merge`、`git`（含 `default_branch`）、action 级 `host` / `hosts` / `parallel`、`instance.libvirt_uri` 不再被 schema 接受。这些字段此前从未被运行时读取，但已声明它们的 project.yaml 需删除后才能通过校验
- **工作流收敛到单 SKILL**：`src/core/workflows/skills/` 下除 `pg-auto-pilot` 外的 10 个 SKILL（pg-define / pg-propose / pg-build / pg-fix-issue / pg-quick-build / pg-regression / pg-verify-and-merge / pg-archive / pg-browser-testing-with-devtools / pg-systematic-diagnosing）全部移除；`pg-init-project` 随之重写（见"新增 / 改进"）
- **slash 命令收敛**：`/1-pg-define` / `/1-pg-grill` / `/2-pg-propose` / `/2b-pg-quick-build` / `/3-pg-build` / `/4-pg-regression` / `/5-pg-fix-issue` / `/6-pg-archive` 全部删除，仅保留 `/0-pg-auto-pilot`
- **sub-agent 收敛**：`pg-manager` 及 `pg-build/*`、`pg-fix-issue/*`、`pg-quick-build/*`、`pg-regression/*` agent 删除，仅保留 `explore.md`
- **合并改手动**：`pg-verify-and-merge` 删除后不再有自动合并能力，合并到 default 分支改用手动 `git merge`
- **`pg-invoke-hook.py` caller 收敛**：`--skill`/`--caller` 可选值仅剩 `pg-agent` / `ad-hoc`；`status` 子命令删除
- **`project.yaml` schema 段收敛**：`verify_merge` / `flyway` / `propose` / `build` / `regression` 段标记 deprecated（历史 project.yaml 仍可解析，新增不再建议使用）

**新增 / 改进**
- **`pg-init-project` 重写（v1.0）**：对齐 pg-auto-pilot 单工作流——不再生成 tracks / stages / code-review，caller 仅 `pg-agent` / `ad-hoc`，保留 Phase 5 AGENTS.md drift 检测与 agent-protocol 注入
- **`pg-parse-config.py` 精简**：`WORKFLOW_KEYS` 仅剩 `pg-agent`；pg-verify-and-merge / pg-regression 专用逻辑删除
- **初始化模板修复**：`pg init` 生成的 placeholder `project.yaml` 的 `roles` 改为数组格式；`pg-run` symlink 权限设置改用 `lstat`，不再因目标缺失崩溃
- **适配器同步**：opencode / mobile-coder / deepseek-harness 适配器仅渲染 `pg-auto-pilot` 工作流
- **官网文档重写**：`docs/index.html` 精简为 6 章并新增 pg-run 使用章节

**死代码清理**
- **移除开发者工具 `tools/project-editor`**：随工作流收敛删除（其编辑对象 tracks / stages 已不存在）
- **移除孤儿脚本 `pg-parse-test-results.py`**：无任何调用方与测试，测试结果解析职责由各模块 `test` 命令直接承担
- **移除历史迁移工具链**：`migrate-define-summary.py`、`define-summary.schema.json`、`define-summary.example.yaml` 及其单测——迁移对象所属 SKILL 已移除，无 CLI 入口
- **移除 `pg-parse-config.py` 失效的脚本校验**：其校验路径读取不存在的 `pipeline` 键，永远返回成功（"VALIDATION BLOCKING" 承诺实际从未生效），连同死分支 `resolved_actions` 一并删除，行为无变化
- **其他死代码**：空包 `src/core/runtime/`、`rendering.unresolved_workflow_tokens()`、`hook-helpers._pg_parse_kv()`、4 处未使用 import、pg-run 中 3 处死赋值与 1 处恒真条件、pg-invoke-hook 中恒空的 `spec_phase` 字段，以及 schema 中对不存在脚本 `pg-validate-proposal.py` 的悬空引用

## [0.9.4] - 2026-09-12

**改进**
- **pg-run 更新菜单更顺手**：更新 Tab 直接列出官方仓库全部版本（main 置顶、新版本在前、每页 10 项），选中后 y/N 确认即可强制更新，不再需要手工输入版本号；切换 Tab 先显示 Loading 再出列表，远程拉取失败时给出明确提示
- **skill 门控规则统一**：除 Auto-Pilot 外，所有 pg-* skill 统一为"与构建无关的日常任务禁止自行加载；未加载 pg-define SKILL 时不得提示用户使用本 SKILL"，减少 agent 在无关任务中刷存在感
- **Auto-Pilot 执行约束更严**：改完代码后若实例已在运行，必须 restart（或 stop+start）再继续，确保新代码被应用

## [0.9.3] - 2026-09-05

**行为变更（使用前请知悉）**
- **工作流 skill 仅限用户显式触发**：`pg-define` / `pg-propose` / `pg-build` / `pg-fix-issue` / `pg-quick-build` / `pg-regression` / `pg-verify-and-merge` 只能在用户通过对应 `/pg-*` 命令或明确自然语言请求时由 agent 加载，agent 不得自行启动；`pg-build` 完成后不再自动触发 `pg-verify-and-merge`，需用户明确指示后执行（如"verify 并合并"）

**新增**
- **Auto-Pilot 自动驾驶模式**：新增 `/0-pg-auto-pilot` 命令与 `pg-auto-pilot` skill——不限定 LLM 如何规划与执行，只要求实施计划含"启动实例并验证结果"步骤、执行前让用户选定环境并确认环境准备方式
- **DeepSeek Harness 集成**：`pg init` 新增 `--tool deepseek-harness` 适配器，可在 DeepSeek Harness 工具环境中安装 pg-* 工作流
- **pg-run 新增"更新"Tab**：菜单中可直接"检查更新"（拉取官方仓库版本列表）或"强制更新到指定版本"（留空 = main 分支，或输入 tag），无需退出菜单命令行操作

**改进**
- **`pg upgrade` 默认更新到 main 分支**：不再默认拉取 master
- **更新检查更可靠**：改为直接拉取官方 pg-skills 仓库的 tag 列表，不再依赖消费项目中是否配置了 pg-skills remote
- **修复**：pg-run 更新子菜单在 raw（非 cooked）终端模式下无法输入版本号的问题

## [0.9.2] - 2026-08-15

**升级前必读**
- 需要更新 `project.yaml`：`environments.<name>.roles` 由键值对改为数组格式（`[{name, ...}]`），旧写法将解析失败
- V-* 编号统一为 `V-{track_id}-{seq}` 格式，旧的 `V-NNN` 编号将不再被接受

**改进**
- **合并更安全**：合并前自动检测分支是否落后，落后过多时自动 rebase；合并后校验是否有"本次改动之外"的文件被覆盖，发现异常会中止合并并提示
- **restart 更省心**：role 没有 restart 脚本也能直接重启，自动按"停止→启动→健康检查"执行
- **能力自动对账**：定界时声明环境能力，提案阶段自动检查所用能力是否满足，不满足会提前提示
- **支持重新定界**：定界之后想调整范围，可用 `/1-pg-define --redefine <change-id>` 重新定界，无需重开
- **质量校验更严**：verifiable/degraded/skipped 三种状态必须落实到对应产物文档中，推动方案落地更完整
- **初始化体验优化**：`pg upgrade` 自动补齐缺失的骨架目录，`pg init` 自动生成合适的 `.gitignore`
- **进度预览更好用**：产物（md/json）在进度面板中直接渲染预览，支持一键展开/折叠
- **模板更完善**：hook 模板新增 `pg_run_bash` 辅助和 `PG_INSTANCE_PORT` 环境变量，减少手写样板

**其他**
- 支持 Python 3.9+；渲染时自动排除 `__pycache__` 等非源文件
- 文档全面更新（desribe_env 语义、定义阶段环境检查提示、首页重写）

## [0.9.1] - 2026-08-06

- **define-summary.yaml 产物协议**：pg-1-define 新增"定界后环境验证"环节，探测真实环境后落盘 define-summary.yaml（含 V-* 状态三态）
- **pg-propose 自动加载 define-summary**：阶段 1.8 自动加载并校验，V-* 状态作为写作上下文；向后兼容（无此文件则跳过）
- **env_resource_refs 强引用**：design.md/scenario 文件必须引用 define-summary 中已声明的资源，交叉校验
- **pg-gen-tasks-skeleton v1.3**：verify 章节自动注入 V-* 状态对账子段
- **迁移工具**：`migrate-define-summary.py` 将旧格式自动转换为新格式
- **progress-monitor 重构**：服务端/前端全面重构

## [0.9.0] - 2026-08-03

**破坏性变更**
- 删除 `pg-propose-refine` 流程（/2.1-pg-propose-refine 命令）、`env-capability.yaml` 机制
- pg-fix-issue SKILL 精简 ~2900 行，从 6 阶段瀑布流改为扁平流程

**新增**
- **v6 hook 协议 — describe_env**：新 action 探测 env 资源并输出 env-description.yaml；pg-propose 阶段 1d.5 改用此方式
- **explore sub-agent**：代码探索优先使用 CodeGraph
- **pg-quick-build v2.1**：新增真实环境探测（Phase 0.5），V-* 可达性过滤，不可达 V-* 过多时建议走 pg-propose
- **pg-validate-proposal.py 3 条新规则**：V-* 映射、scenario 引用防护、章节编号连续性
- **pg-build bootstrap 防御加固**：脏分支检测、重复 bootstrap 检测

## [0.8.3] - 2026-07-19

- **pg-build 集成验证不可跳过**：跨环境依赖必须满足
- **pg-propose API 端点强制完整性**：design.md 必须含完整 Request/Response Body
- **pg-build scenario-fix 诊断**：输出 drift.md 记录设计偏移与修复方案
- **pg-build 启动前脏分支检查**
- **AGENTS.md + 品构文档**：新增架构文档与 12 张 SVG 技能卡片
- **`build.injections` → `propose.injections` 重命名**（破坏性：需更新 project.yaml）

## [0.8.2] - 2026-07-16

**破坏性**
- **Scenario Track 机制**：新增 `type: scenario` pipeline track，支持独立生命周期
- **manifest v3**：`execution-manifest.yaml` 升级，新增 `enabled`/`reason`/`on_conditions_eval`

**新增**
- pg-gen-scenario.py：生成 scenario 骨架
- pg-propose v3.7：流程精简，占位符递归校验，全推荐自动 refine

## [0.8.1] - 2026-07-14

**破坏性**
- **`review_level` 字段全量移除**：改用 `code_review_enabled` / `code_review_profiles` / `code_review_languages`

**新增**
- **Verify/Gate 按 track 关闭**：project.yaml 新增 `tracks.<id>.verify_enabled` / `gate_enabled`
- **design.md 缺陷协议**：fix-review 检测到 design/tasks 文档错误时触发 workflow_failed
- **P0 硬约束机制**：P0 FAIL 时强制 escalate，绕过 score 阈值
- **review rule docs 注入**：review 阶段自动注入 code-review profile 文档

## [0.8.0] - 2026-07-09

**破坏性**
- **code-review 阶段**：pg-build 五阶段模型新增 review 子阶段
- **`code_view` → `code_review` 全量重命名**
- **pg-propose SKILL.md 重构**：810 行 → 303 行，模板下放到 references/

**新增**
- **Profile 引擎**：支持多 profile（default/java-spring/go/vue3/security），按 language 自动派发
- **pg-gen-tasks-skeleton.py**：替代 LLM 手工生成 tasks.md heading
- **pg-fix-issue v3.2 重构**：6 阶段扁平流程
- **品构品牌命名**：slogan「让 AI 写出可托付的代码」
- **pg-run health_check 菜单**

## [0.7.0] - 2026-07-05

**破坏性**
- **pg-build v2 取代 v1**：事件溯源状态机取代过程式 51 个 save_state 调用
- **路径简化**：日志目录从 `<env>/logs` 改为 `<env>-logs`（pg-build 自动迁移，其它需手工）
- **execution-manifest.yaml 成为环境 SSOT**：environment.yaml 弃用

**新增**
- **pg-verify-and-merge AffectedTracks 5 层 fallback**
- **pg-regression A/B/C 三分类自动修复**：A 类自动修、B 类附 rationale、C 类禁止
- **pg-check-fix-test-boundary.py**：硬规则检测时自动回滚
- **pg-parse-test-results.py skipped 解析**

## [0.6.0] - 2026-07-02

- **pg-build-v2 事件溯源引擎**：Event Sourcing + Reducer 取代过程式状态机；SubPipeline 递归复用
- **mark-task CLI**：state.json 为 SSOT，tasks.md 改为派生视图
- **actions.health_check**：支持 HTTP/TCP 探针
- **pg-init-project Phase 5**：AGENTS.md drift 治理

## [0.5.0] - 2026-06-28

**破坏性**
- **project.yaml 全量 snake_case**：`verifyMerge` → `verify_merge`、`git.default-branch` → `git.default_branch` 等
- **`apply_change_rules` → `build_rules` 重命名**

## [0.4.0] - 2026-06-27

**破坏性**
- **v4 hooks 协议**：`--change` → `--session`；新增 `--caller`；日志目录路由改为 caller × session 双维度
- **pg-quick-build 不再切分支**：直接在当前分支修改
- **pg-fix-issue 日志目录独立**：走 `.pg/fix-issue/` 而非 `.pg/changes/`

**新增**
- **pg-run 菜单式运行时命令**：支持逐级菜单和直达模式
- **pg-parse-config.py --resolve-env**：按需解析 env 详情
- **lib/common.sh 公共库**：caller × session 双维度日志路由

## [0.3.0] - 2026-06-26

- **pg-invoke-hook.py 统一入口**：hooks 协议在 runtime 层单一实现，pg-build/pg-regression 全面切换
- **env-level actions 支持**（prepare_env/clean_env 无需角色）

## [0.2.0] - 2026-06-24

- **`pg sync` → `pg upgrade` 重命名**：支持指定版本号、交互式升级
- **`.pg-version` 文件移除**：改用 `.pg/skills/VERSION` 作为 SSOT

## [0.1.0] - 2026-06-22

- 从 webvirt 项目提取 pg-* 技能体系：13 个技能 / 8 个斜杠命令 / 5 个子代理
- L1 runtime 骨架 + 3 种语言示例模板