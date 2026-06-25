# 变更日志

所有对 pg-skills 的重要变更均记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.0] - 2026-06-25

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

### 后续追加 (0.3.0 patch)

#### 新增
- `pg-invoke-hook.py status` 顶级 subcommand: 透传 prepare_env 状态查询到 `pg-pipeline-runner.py prepare-env-status`. CLI 形式 `pg-invoke-hook.py status --change <C> [--stage <S>]`, stdout JSON + exit code 原样透传. 与 `invoke-hook` 平级, LLM-facing 状态查询入口统一在 runtime 层.
- 新增测试 `test_pg_invoke_hook_status.py` (14 个测试): 覆盖 dispatcher 路由 / argparse / 透传 / runner exit code 透传 / 实机端到端 CLI

#### 变更
- **破坏性** (隐式): `pg-invoke-hook.py` 重构为顶级 subcommand 派发器 (`invoke-hook` / `status` 两个并列 subcommand). 调用形式 `pg-invoke-hook.py invoke-hook <flags>` 与新 `pg-invoke-hook.py status <flags>`. 向后兼容: bare flag 形式 (`pg-invoke-hook.py <flags>` 无 subcommand) 仍默认走 `invoke-hook`
- `pg-build/SKILL.md` 文字统一: 6 处 `runner invoke-hook` 改为 `pg-invoke-hook.py invoke-hook` (line 438/439/441/460/462 标题/492-494 表格); 历史兼容说明保留在 line 470 (`pg-pipeline-runner.py invoke-hook 仍可用, thin wrapper 转发...`), 维持 `test_v3_invoke_hook_migration.py:130` 的 regex contract
- `pg-build/agents/verify.md` line 100: `pg-pipeline-runner.py prepare-env-status` 改为 `pg-invoke-hook.py status` (含历史兼容注释)
- `README.md` §Hook 协议: 标志表 `--role`/`--instance` 描述加 "env-level 忽略" 注释; `--action` 列表加 `prepare_env / clean_env`; 新增 §`status` subcommand 章节; §Development 段 `python3 src/runtime/bin/pg validate` 改为 `python3 src/runtime/bin/pg doctor`
- `test_invoke_hook.py`: PROJECT_ROOT 解析从 7 层相对路径上推改为 walk-up 探测 `.pg/project.yaml` (修复 hardlink 路径下的 pre-existing 解析失败)
- `test_v3_invoke_hook_migration.py`: `_find_project_root` 增加 cwd fallback + PG_PROJECT_ROOT env var 支持 (同上修复)

#### 修复
- pre-existing: `test_invoke_hook.py` 在 hardlink 路径下 (`/home/ubuntu/workspace/pg-skills/...`) 走 7 层 `..` 上推到 `/home/.pg/...`, 找不到 `pg-pipeline-runner.py`. 已改为 walk-up 探测, oc2-web-virt 与 upstream pg-skills 双向都能跑
- pre-existing: `test_v3_invoke_hook_migration.py` 同上 hardlink 现象, `_find_project_root` 只 walk-up 10 层且不 fallback cwd, 在 hardlink 路径下失败

#### 备注
- `pg-invoke-hook.py` 现承担 **所有** LLM-facing 入口: hook 协议 (`invoke-hook`) + 状态查询 (`status`). runner 仅承担编排状态机 (`next` / `record` / `check` / `progress`)
- 测试总数: 14 (status) + 21 (invoke-hook) + 6 (env-level actions) + 8 (v3 migration) = **49/49 通过** + `pg doctor` 4/4

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
