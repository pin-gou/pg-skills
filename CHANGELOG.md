# 变更日志

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