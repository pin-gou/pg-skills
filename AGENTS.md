# AGENTS.md

本目录（`.pg/skills/`）作为 **git subtree** 嵌入到您的项目仓库中，独立推送到 `pg-skills` 仓库。

## 远程仓库

| 远程名 | 仓库地址 |
|--------|---------|
| `origin` | 您的项目仓库（当前嵌入 pg-skills 的仓库） |
| `pg-skills` | `git@gitee.com:shao_hq/pg-skills.git` |

## 推送变更到 pg-skills

修改 `.pg/skills/` 下的文件后，先提交到当前分支，再执行：

```bash
git subtree push --prefix=.pg/skills pg-skills master
```

这会从您的项目仓库中拆分出 `.pg/skills/` 目录的历史，推送到 `pg-skills` 的 `master` 分支。

## 从 pg-skills 拉取更新

```bash
git subtree pull --prefix=.pg/skills pg-skills master --squash
```

## 工作流示意图

```
您的项目仓库                              pg-skills 仓库
─────────────────────────             ────────────────────
  .pg/skills/  ←── subtree push ───  master
  .pg/skills/  ── subtree pull ──→   master
```

## 注意事项

- 不要在 `pg-skills` 仓库直接修改后然后在您的项目仓库中手动复制——始终用 `subtree pull`。
- 提交信息中涉及 `.pg/skills/` 的变更，使用 squash 方式合并到子树历史中。
- `git subtree push` 会把修改该目录下文件的所有提交打包推送，**不影响**您的项目仓库的其他代码。
