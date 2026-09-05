---
name: publish-new-version
description: 发布 pg-skills 新版本——更新 VERSION 文件、根据 git log/git diff 更新 CHANGELOG.md/README.md/docs、用指定版本号打 v* tag 并推送 remote、用 gh 创建 GitHub release。当用户说"发布新版本"、"发版"、"打 tag 发 release"、"更新版本号到 x.y.z"或提到版本号时使用。仅限用户显式触发，不自动执行。
license: MIT
compatibility: 需要 gh 已登录、有远程仓库写权限
metadata:
  author: pg-spec
  version: "1.0"
---

# publish-new-version

按指定版本号完成一次完整的 pg-skills 发版：更新版本号 → 生成变更日志 → 同步文档 → 打 tag 推送 → 创建 GitHub release。

> **仅限用户显式触发**：收到明确的发版请求（如"发布 0.9.3"、"发版"）后执行，不因其他任务自动运行。发版是破坏性操作（推送 tag + 创建 release 不可撤销），每个执行步骤（尤其打 tag 与 gh release）都要先让用户确认。

---

## 输入

| 参数 | 说明 | 示例 |
|------|------|------|
| 版本号 | 目标 semver 版本（不带 `v` 前缀） | `0.9.3` |

## 前置条件

| 项 | 要求 | 校验失败行为 |
|----|------|------------|
| `gh` 已认证 | `gh auth status` 返回已登录 | 终止并让用户先登录 |
| 目标版本号 | semver 格式，高于当前 `VERSION`，且 `git tag` 中不存在 | 终止并报告 |
| 工作区状态 | 待发布变更已提交（先做 release commit 再发版） | 提示先提交 |
| 远程可写 | `git remote -v` 有 origin，且当前在默认分支（main） | 终止并报告 |

---

## 核心流程

### 步骤 1：更新 VERSION

将新版本号写入 `VERSION` 文件（**不带** `v` 前缀，如 `0.9.3`）。

```bash
echo "0.9.3" > VERSION
```

### 步骤 2：分析 git log / git diff，更新 CHANGELOG.md / README.md / docs

**2.1 分析变更**

以当前 VERSION 对应的上一个 tag（如 `v0.9.2`）为基线：

```bash
git tag --list        # 确认上一个版本 tag
git log --oneline v0.9.2..HEAD                     # 本版本全部 commit（逐条核对用）
git log v0.9.2..HEAD --stat                        # 变更涉及的文件
git diff v0.9.2..HEAD --stat                       # 代码量/文件面
```

**2.2 更新 CHANGELOG.md**（SSOT 规则，见 AGENTS.md §6.4）

在文件顶部（`# 变更日志` 标题下）新增一个 section，标题为 `## [<版本号>] - <发布日期>`，版本号与 `VERSION` 文件保持一致。撰写要求：

- **从用户视角撰写**：写"变更对用户的影响"（用户得到什么、需要做什么），不罗列实现细节、函数名或内部机制
- **破坏性变更优先**：需要用户主动修改的内容（如 project.yaml 格式、命令/字段变更）放在最前，标注"升级前必读"，明确说明改什么
- **发布前核对**：用 `git log --oneline v<prev-tag>..HEAD` 列出全部 commit，确保每个用户可见变更都有对应条目，不遗漏、不夸大；写完后用 `git diff CHANGELOG.md` 自查

**2.3 同步版本号引用**

更新 VERSION 时，必须同步修改所有文档/代码中 `git subtree add --prefix=.pg/skills pg-skills v<old> --squash` 命令中的版本号为**新版本**（把 `v<old>` 全部替换为 `v<new>`）。当前受影响文件：

| 文件 | 说明 |
|------|------|
| `README.md` | 第 43、119 行附近的 subtree 安装命令 |
| `docs/index.html` | 第 894 行附近的 subtree 命令 |
| `docs/pg-skills.md` | 第 126 行附近的 subtree 命令 |
| `docs/cards/07-onboarding.svg` | 第 12 行 SVG 中的 subtree 命令 |
| `src/core/init.py` | 第 188 行 `pg init` 输出的安装提示 |

用 grep 全仓库核对没有遗漏：

```bash
grep -rn "pg-skills v[0-9]" README.md docs/ src/core/ --include="*.md" --include="*.html" --include="*.svg" --include="*.py"
```

**2.4 更新其他文档**

依据 2.1 的变更分析，更新 `README.md`、`docs/pg-skills.md` 等中描述的功能/命令/用法（如新增命令、skill 或行为变更）。CHANGELOG 中每条用户可见变更对应的文档面要同步。

**2.5 提交并推送**

将版本号 + 文档变更合入一个提交，并推送到 remote，确保 release commit 在远程 main 分支上可见（否则下方 tag 会指向一个远程不存在的孤悬 commit）：

```bash
git add VERSION CHANGELOG.md README.md docs/ src/core/init.py
git commit -m "changelog: 补充 v0.9.3 全量变更"
git push origin main
```

> 只有用户明确要求时才可在此过程中附带提交代码变更。步骤 3 打 tag 前，确认本次 commit 已在远程（`git log origin/main -1 --oneline` 与本地 HEAD 一致）。

### 步骤 3：创建 tag 并推送 remote

版本号格式为 `v<版本号>`（如 `v0.9.2`），使用 annotated tag：

```bash
git tag -a v0.9.3 -m "Release v0.9.3"
git push origin v0.9.3
```

推送前向用户确认 tag 名称无误。推送成功后 tag 已在 remote 生效（可用 `git ls-remote --tags origin` 验证）。

### 步骤 4：用 gh 创建 release

从 CHANGELOG.md 提取本版本 section 的正文作为 release notes，用 gh 创建：

```bash
gh release create v0.9.3 \
  --title "v0.9.3" \
  --notes "$(从 CHANGELOG.md 提取 ## [0.9.3] 至下一个 ## 之间的正文)"
```

提取可用 python/sed 脚本化，或直接复制 section 正文。发布后确认：

```bash
gh release view v0.9.3
```

---

## 完成检查清单

- [ ] `VERSION` 文件为新版本号（无 `v` 前缀）
- [ ] `CHANGELOG.md` 顶部有 `## [<新版本>] - <日期>` section，所有 commit 的用户可见变更已覆盖
- [ ] `grep -rn "pg-skills v[0-9]"` 中所有 subtree 引用均已更新为新版本
- [ ] release commit 已推送（`git log origin/main -1 --oneline` 与本地 HEAD 一致）
- [ ] tag `v<新版本>` 已推送（`git ls-remote --tags origin` 可见）
- [ ] `gh release view v<新版本>` 正常返回，release notes 完整

---

## 注意事项

- **版本号规范**：`VERSION` 文件不带 `v` 前缀（`0.9.3`），git tag 带 `v` 前缀（`v0.9.3`），两者差一个前缀，不要混淆
- **SSOT 约束**：步骤 2.3 的 subtree 引用同步是本仓库强约束（AGENTS.md §6.4），漏改会导致 `pg init` 输出过时安装命令
- **CHANGELOG 只写用户可见变更**：内部重构、脚本内部实现变化不写；写错或夸大比少写危害更大
- **不改消费项目的版本号**：本 SKILL 只发布 pg-skills 仓库自身的版本，不触碰嵌入到消费项目 `.pg/skills/` 中的任何文件
- **tag 与 release 不可撤销**：推送/创建前必须让用户确认
