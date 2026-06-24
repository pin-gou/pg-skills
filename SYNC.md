# pg-skills 同步指南

本文档说明本项目（`web-virt`）与 [pg-skills 仓库](https://gitee.com/shao_hq/pg-skills) 的双向同步机制。

## 架构

```
web-virt 仓库（origin）                pg-skills 仓库（pg-skills remote）
─────────────────────                ─────────────────────────
  .pg/skills/  ←── git subtree ──→   master
       ↑
       └── 硬链接到 .opencode/{agents,commands,skills/pg-*}/
```

- `.pg/skills/` 用 `git subtree add --prefix=.pg/skills pg-skills master --squash` 引入
- `.opencode/{agents,commands,skills/pg-*}/` 是 symlink，指向 `.pg/skills/src/opencode/` 下的对应目录
- opencode CLI 通过 symlink 加载 pg-skills 提供的 commands / agents / skills

## 远程配置

```bash
git remote -v
# origin    git@gitee.com:shao_hq/web-virt.git
# pg-skills git@gitee.com:shao_hq/pg-skills.git
```

## 日常同步操作

### 场景 1：在 web-virt 中修改了 .pg/skills 下的文件

```bash
# 1. 修改 .pg/skills/ 下的文件
vim .pg/skills/src/opencode/skills/pg-build/SKILL.md

# 2. 提交到 web-virt（这是必须的主项目 commit）
git add .pg/skills/
git commit -m "fix: pg-build SKILL.md 修复 xxx"

# 3. 推送到 pg-skills 仓库
git subtree push --prefix=.pg/skills pg-skills master
```

### 场景 2：pg-skills 仓库有更新，需要同步到 web-virt

```bash
# 一条命令搞定
git subtree pull --prefix=.pg/skills pg-skills master --squash
```

冲突时改为手工 cp：
```bash
# 1. 拉取临时目录
rm -rf /tmp/pg-skills-sync
git clone git@gitee.com:shao_hq/pg-skills.git /tmp/pg-skills-sync
cd /tmp/pg-skills-sync

# 2. 逐文件 copy
diff -r .pg/skills/ /tmp/pg-skills-sync/  # 看差异
# 手动 cp 受影响文件

# 3. 在 web-virt 仓库 commit
cd <web-virt>
git add .pg/skills/
git commit -m "chore: sync pg-skills 上游更新"
```

### 场景 3：在 pg-skills 仓库直接修改（不推荐）

偶尔在 pg-skills 仓库直接改了（例如紧急修复）。同步回 web-virt 用场景 2 的命令。

## 故障排查

### Q: `git subtree pull` 报 "Subtree is already at commit ..."

**原因**：本地 `.pg/skills/` 内容与 pg-skills/master HEAD 一致，pull 是 noop。

**验证**：
```bash
git log --oneline -1 pg-skills/master
git ls-tree pg-skills/master | wc -l
git ls-tree HEAD .pg/skills | wc -l
# 文件数应一致
```

### Q: `git subtree push` 推送后担心污染

**机制说明**：`git subtree push` 会生成一个 `Merge commit 'XXX' as '.pg/skills'` 形式的 commit，diff stat 可能显示大量行（因为包含了删除 .pg/skills 然后重新引入的过程）。但实际 **tree hash 不会变**（fast-forward 性质）。

**安全验证**（push 前）：
```bash
# 1. 看 split 出的 commit
git subtree split --prefix=.pg/skills HEAD

# 2. 对比 tree hash
LOCAL_TREE=$(git rev-parse HEAD:.pg/skills)
SPLIT_TREE=$(git rev-parse <split-commit>^{tree})
MASTER_TREE=$(git rev-parse pg-skills/master^{tree})
echo "local=$LOCAL_TREE split=$SPLIT_TREE master=$MASTER_TREE"
# 三个 hash 一致 = push 不会改变文件
```

**如发现污染**（如 split 出的 tree 与 master tree 不一致）：
```bash
# 1. 立即 force 回滚
rm -rf /tmp/pg-skills-rollback
git clone git@gitee.com:shao_hq/pg-skills.git /tmp/pg-skills-rollback
cd /tmp/pg-skills-rollback
git reset --hard <污染前的 commit>
git push --force origin master
cd - && rm -rf /tmp/pg-skills-rollback
```

### Q: 硬链接/symlink 损坏

`.opencode/{agents,commands,skills/pg-*}/` 是 symlink，**手动删 symlink 后**需要重建：

```bash
# 例如：删了 .opencode/skills/pg-build 后重建
cd .opencode/skills
ln -s ../../.pg/skills/src/opencode/skills/pg-build pg-build
```

或者全部重建（参考 853290fc commit "fix: 恢复 .opencode 下 pg-* symlinks"）：

```bash
# agents
for d in pg-build pg-fix-issue pg-quick-build pg-regression; do
    ln -sf ../../.pg/skills/src/opencode/agents/$d .opencode/agents/$d
done
ln -sf ../../.pg/skills/src/opencode/agents/explore.md .opencode/agents/explore.md
ln -sf ../../.pg/skills/src/opencode/agents/pg-manager.md .opencode/agents/pg-manager.md

# commands
for f in pg-1-define.md pg-2-propose.md pg-2.1-propose-refine.md pg-2b-quick-build.md \
         pg-3-build.md pg-4-regression.md pg-5-fix-issue.md pg-6-archive.md; do
    ln -sf ../../.pg/skills/src/opencode/commands/$f .opencode/commands/$f
done

# skills
for d in pg-archive pg-browser-testing-with-devtools pg-build pg-fix-issue \
         pg-propose pg-propose-refine pg-quick-build pg-regression \
         pg-systematic-diagnosing pg-verify-and-merge; do
    ln -sf ../../.pg/skills/src/opencode/skills/$d .opencode/skills/$d
done
```

## 一次性迁移历史

本项目历史上 `.pg/skills/` 是手动 cp 副本（非标准 `git subtree add`），导致 `git subtree pull/push` 命令不可用。

迁移 commit 链：
1. `372b73f3` chore: 移除 .pg/skills 旧副本
2. `793587bc` Squashed '.pg/skills/' content from commit d9a5fcf3
3. `67614347` Merge commit '793587bc' as '.pg/skills'

迁移后，标准 `git subtree` 命令全部可用。

## 重要约束

1. **不要在 web-virt 中直接编辑 .opencode/ 下的 pg-* symlink**——它们是 symlink，最终指向 `.pg/skills/`，编辑 symlink 等于编辑源文件
2. **`.pg/skills/` 路径不要混用**——所有 pg-skills 仓库的内容必须在 `.pg/skills/` 下，不要在主项目根目录直接放置
3. **subtree push 前先 commit 到 web-virt**——subtree push 是基于 web-virt HEAD 的 split，没 commit 的修改不会进 push
4. **跨项目改 pg-skills 内容必须在 pg-skills 仓库**——subtree 是单向同步，反向会污染
