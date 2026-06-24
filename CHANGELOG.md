# 变更日志

所有对 pg-skills 的重要变更均记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
