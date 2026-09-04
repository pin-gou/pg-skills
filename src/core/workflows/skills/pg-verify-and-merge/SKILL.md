---
name: pg-verify-and-merge
description: 仅当用户显式触发合并工作流时使用（用户明确说"verify 并合并"、"合并到 master"、"模拟合并验证"等）；pg-build 完成后**不会自动触发**，禁止自行加载。功能：将 feature branch 模拟合并到 master 并按需验证后合并。
license: MIT
compatibility: 项目根目录需要 `.pg/project.yaml`（v3.0 schema：modules / environments / tracks / stages / regression.suite / verify_merge / flyway / git）。SKILL 通过 `python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py pg-verify-and-merge` 统一注入所有配置（tracks / regressionSuites / verify_merge / flyway / git 五段 JSON），不再单独调用 `--key` 取值。
metadata:
  author: pg
  version: "3.0"
---

# pg-verify-and-merge

## 概述

pg-build 完成后，将 feature branch 合并到 master 前，先将 feature branch 合并到 master 的工作区（模拟合并），然后**按受影响范围与 merge 冲突状态**决定是否需要运行测试套件，确保主分支稳定性。

**核心原则：** 在合并后的代码上验证，不在 feature branch 上验证。

**最终 workspace 状态约束**（v3.0 补充）：

- **合并成功（Phase 3 commit + push 完成）**：Phase 4 切回 default_branch，流程结束。
- **合并失败（Phase 3 中止或上游 Phase 失败）**：Phase 4 **保持当前 workspace 不动**，留给人工排查。
- **禁止**：合并成功后切回 feature branch（即使想清理 feature branch 也应由人在 default_branch 上独立执行）。

**关键改进（v3.0）**：

- **与 pg-regression / pg-fix-issue 同一套配置**：所有命令、路径、env 派生自 `.pg/project.yaml`，不再有 v2 的 `pipeline.tracks.*.lint` 和已废弃的 `testSuites.*` 段引用（硬切换，无兼容层）。
- **AffectedTracks 自动推断**：从 `<change>/tasks.md` 章节号读起，tasks.md 缺失则 fallback 到 `git diff` + `tracks.<t>.root` 路径前缀匹配，最后 fallback 到 `regression.suite` 的 key 列表。**simple track 永远过滤**（`openapi-gen` 等跑 commands 不跑 TDVG，无 regression.suite）。
- **按 AffectedTracks 过滤**：只跑 manager agent 传入（或自动推断）的受影响 track 对应的 testSuite（不是全跑）。
- **merge 无冲突时跳过测试**：`verify_merge.skip_tests_if_no_conflict=true`（默认）时，无冲突 = 跳过 Phase 2 = 加速合并。
- **分支新鲜度检查与自动 rebase**：合并前检测特征分支落后 default_branch 的 commit 数，超过 `max_branch_staleness`（默认 10）时自动 rebase 到最新，消除旧 merge-base 导致的 stale 文件回溯。
- **Diff Scope Gate**：合并后检测每个 staged 文件，确认特征分支确实改过它；发现"特征分支从未改动却出现在合并结果"的文件则中止，防止旧版文件无声覆盖已合入的功能（如本次修复的 timeline/i18n 回归）。
- **envSetup / verifySetup 派生**：从 `environments.<env>.prepare_env` 派生 envSetup，从 `required_roles` 的 `start` action 派生 verifySetup probe。
- **outputFormat 智能推断**：按 `modules.<m>.language + test_key` 推断（`e2e → playwright`，`java → maven-surefire`，`go → go-test`），可在 `regression.suite.<n>.output_format` 显式覆盖。
- **Key 改进**：模拟合并后不切换分支，Phase 2 的验证和 Phase 3 的提交都在 default_branch 上完成。

## 何时使用

- pg-build 工作流执行完成，feature branch 功能验证通过
- 准备将 feature branch 合并到 master

## 入口上下文

| 上下文变量 | 来源 | 用途 |
|-----------|------|------|
| `AffectedTracks` | **自动推断**（见下） | 决定哪些 track 需要 lint / 跑测试 |

manager agent **无需显式传入** `AffectedTracks`（除非有特殊原因要覆盖）。

### AffectedTracks 推断（自动）

**4 层 fallback**，第一个命中的优先：

1. **CLI 参数**：`pg-parse-config.py pg-verify-and-merge --affected-tracks backend,frontend`（manager agent 显式覆盖时使用）
2. **`execution-manifest.yaml` tracks**：读 `<change>/execution-manifest.yaml` 的 `stages[].tracks[].id`，拼为 `dev.frontend` 格式（pg-gen-manifest.py 已自动过滤全部 `- 无` 的 track，比 tasks.md 更精确）
3. **`tasks.md` 章节号**：读 `<change>/tasks.md` 的 `## {N}. {stage.name}.{track_id} ...` 二级章节，提取所有 `track_id` 并去重
4. **`git diff` 路径前缀匹配**：`git diff origin/<Git.default_branch> HEAD --name-only` 与 `tracks.<t>.modules[*].root` 做前缀匹配
5. **`regression.suite` keys 兜底**：所有 `regression.suite.<n>` 的 key（去掉 simple track）

**Simple track 永远过滤**：`tracks.<t>.type == "simple"` 的 track（如 `openapi-gen`）在所有 5 层路径中都会被剔除，因为它们跑 commands 不跑 TDVG，没有 regression.suite 对应。simple track 的代码生成已经在 pg-build 阶段由 runner 直接验证过。

**输出位置**：`pg-parse-config.py pg-verify-and-merge` 输出的 `__meta.affected_tracks` 数组 + `__meta.affected_tracks_source` 字符串（`cli` / `manifest` / `tasks_md` / `git_diff` / `suite_keys`），方便 manager agent 调试。

## 配置依赖

本 SKILL **不单独调用** `pg-parse-config.py --key <field>`。所有配置由 orchestrator 一次性调用 `pg-parse-config.py pg-verify-and-merge` 获取完整 5 段 JSON，存入 `temp/vm-context.json`（Phase 0 顶部执行一次），后续所有 phase 从该文件读取。

| 输出键 | 来源字段 | 用途 |
|-------|---------|------|
| `tracks.<t>.lint_cmd` | `tracks.<t>.lint` (override) → fallback `modules.<tracks.<t>.modules[0]>.lint` | Phase 0 Step 2 按受影响 track 跑 lint |
| `regressionSuites.<t>.envSetup` | `environments.<env>.prepare_env` (action 渲染) | Phase 2 suite 启动环境 |
| `regressionSuites.<t>.verifySetup` | `environments.<env>.actions.<role>.start` (first role) | Phase 2 suite 环境就绪探测 |
| `regressionSuites.<t>.runAllCommand` | `modules.<m>.test.<test_key>` 串行链 (含 timeout 包装) | Phase 2 跑测试 |
| `regressionSuites.<t>.outputFormat` | `regression.suite.<n>.output_format` (override) → fallback `modules.<m>.language + test_key` 推断 | Phase 2 解析失败清单 |
| `verify_merge.skip_tests_if_no_conflict` | `verify_merge.skip_tests_if_no_conflict` | Phase 1.6 跳过判断 |
| `verify_merge.stale_diff_gate` | `verify_merge.stale_diff_gate` | Phase 1.5 是否启用 stale 文件检测（默认 true） |
| `verify_merge.max_branch_staleness` | `verify_merge.max_branch_staleness` | Phase 1 Step 1 分支落后阈值（默认 10） |
| `verify_merge.auto_rebase_stale` | `verify_merge.auto_rebase_stale` | Phase 1 Step 1 是否自动 rebase（默认 true） |
| `flyway.migration_path` | `flyway.migration_path` | Phase 0 migration 重编号 |
| `git.default_branch` | `git.default_branch` | Phase 1/3 目标分支 |

## 前置条件

- Feature branch 已推送到远端
- 当前在 feature branch 上，无未提交的修改（pg-build 已完成并提交）
- `git remote` 可访问 origin/`Git.default_branch`
- `.pg/changes/<change>/` 或 `.pg/changes/archive/<date>-<change>/` 任一存在
  (pg-build 完成时自动 archive 到后者, change 目录已搬到 archive 下)

## 阶段结构

**前置步骤**（orchestrator 执行）：

CHANGE 路径推断规则 (pg-build 完成时已 archive):
  1. 编排器优先传 archive 路径: `archive/2026-07-05-harden-authz-and-input-validation`
  2. fallback: 传原 change 名 `harden-authz-and-input-validation`,
     pg-parse-config.py 内部 fallback 到 git_diff 推断 affected_tracks

```bash
# 一次性注入所有配置到 temp/vm-context.json
# --json-only: 抑制 banner, stdout 只输出 JSON (v2.0.1), 无需 python 管道截断
mkdir -p temp
python3 .pg/skills/src/core/workflows/scripts/pg-parse-config.py \
    pg-verify-and-merge --json-only \
    --change-dir ".pg/changes/${CHANGE}" \
    > temp/vm-context.json
# ↑ stdout: tracks / regressionSuites / verify_merge / flyway / git / __meta
# 注: __meta 含 affected_tracks 数组 (simple track 已过滤)
```

**可选**：manager agent 显式覆盖（用 `--affected-tracks backend,frontend` 替换 `--change-dir`）。

**所有 phase 均为 orchestrator 自执行**（无 sub-agent 派遣）。

```
[Setup] temp/vm-context.json (5 段 JSON, 含 affected_tracks)
    ↓
Phase 0: Auto-fix on Feature Branch（feature branch）
    ├── Step 1: Renumber Flyway migrations
    ├── Step 2: 对 AffectedTracks 每个 track 跑 lint
    └── Step 3: 提交所有修复
    ↓
Phase 1: 模拟合并到 master（切换到 default_branch）
    ├── Step 1: 分支新鲜度检查与自动 rebase（仍在 feature branch）
    │     - 落后 origin/<default> 超过 max_branch_staleness 个 commit → 自动 rebase 或中止
    │     - rebase 后 merge-base = 最新 default_branch，消除 stale 文件回溯
    ├── Step 2: squash 合并
    └── Step 3: 检测 unmerged 文件，写入 temp/merge-status.txt
    ↓
Phase 1.5: Diff Scope Gate（stale 文件检测）[新增]
    ├── 对每个 staged 文件判断: 合并改动了它，但特征分支从未改动它 → stale 回溯
    └── 发现 stale 文件 → 中止（人工确认误报后可 override 继续）
    ↓
Phase 1.6: 判定是否跳过 Phase 2（原 Phase 1.5，仅 Scope Gate 通过后才可跳过）
    ├── 条件 1: merge 无冲突 + skip_tests_if_no_conflict=true → SKIP
    └── 条件 2: AffectedTracks 中无可运行 testSuite → SKIP
    ↓
Phase 2: 按受影响 testSuites 顺序跑测试（可能整体跳过）
    ├── Phase 2x-1: regressionSuites[0]: envSetup → verifySetup → runAllCommand → 解析
    ├── Phase 2x-2: regressionSuites[1]: ...
    └── ...
    ↓
Phase 3: 提交并推送（保持在 default_branch）
    ↓
Phase 4: 清理
```

### Phase 0: Auto-fix on Feature Branch

> Phase 0 在 feature branch 上执行，auto-fix 可能产生新的提交。
>
> **Step 1（Renumber Flyway）与 Step 2（lint）的修改会合并为同一个 commit 提交。** 如果 renumber 有变更但 lint 无变更，也会单独提交。

```bash
# 获取当前分支名（后续 Phase 需要）
CURRENT_BRANCH=$(git branch --show-current)
MIGRATION_PATH=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['flyway']['migration_path'])")
DEFAULT_BRANCH=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['git']['default_branch'])")
AFFECTED=$(python3 -c "import json; print(' '.join(json.load(open('temp/vm-context.json'))['__meta']['affected_tracks']))")

# Step 1: Renumber Flyway migrations —— 自动解决并行开发的版本冲突
# （比如两个分支都写了 V21，后合分支的 V21 会被重编号为 V22）
bash .pg/skills/src/core/workflows/skills/pg-verify-and-merge/scripts/renumber-flyway-migration.sh \
    --migration-dir "$MIGRATION_PATH" \
    --default-branch "$DEFAULT_BRANCH" || {
    echo "FLYWAY_RENUMBER_FAILED"
    exit 1
}

# Step 2: 对 AffectedTracks 每个 track 跑 lint
# lint_cmd 已是 dict {cmd, timeout_seconds}（pg-parse-config.py 直接产出）
# lint 日志落到 <change>/3-merge/lint-logs/, 与 2-build/ 解耦 (verify-and-merge 专属空间)
# archive 路径推断: 优先 __meta.change_dir, 找不到则 glob archive/<date>-<change>
CHANGE_NAME=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['__meta']['change_dir'].split('/')[-1])")
CHANGE_DIR_REL=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['__meta']['change_dir'])")
CHANGE_DIR="${PROJECT_ROOT:-.}/$CHANGE_DIR_REL"
# 兜底: 如果 __meta.change_dir 路径不存在 (archive 后), 找 archive 下同名 change
if [ ! -d "$CHANGE_DIR" ]; then
    CHANGE_DIR=$(ls -d "./.pg/changes/archive/"*"-${CHANGE_NAME}" 2>/dev/null | head -1)
fi
LINT_LOG_DIR="$CHANGE_DIR/3-merge/lint-logs"
mkdir -p "$LINT_LOG_DIR"

for track in $AFFECTED; do
    LINT_CMD=$(python3 -c "import json; t=json.load(open('temp/vm-context.json'))['tracks'].get('$track',{}).get('lint_cmd'); print(t['cmd'] if t else '')")
    if [ -n "$LINT_CMD" ]; then
        TIMEOUT=$(python3 -c "import json; t=json.load(open('temp/vm-context.json'))['tracks'].get('$track',{}).get('lint_cmd'); print(t.get('timeout_seconds', 1800))")
        LINT_LOG="$LINT_LOG_DIR/lint-${track}-$(date +%Y%m%dT%H%M%S).log"
        echo "=== Lint $track (timeout=${TIMEOUT}s, log=$LINT_LOG) ==="
        timeout "$TIMEOUT" bash -c "$LINT_CMD" > "$LINT_LOG" 2>&1
        LINT_EXIT=$?
        echo "--- lint exit code: $LINT_EXIT ---"
        # 仅失败时 tail -50, 成功时静默
        if [ $LINT_EXIT -ne 0 ]; then
            tail -50 "$LINT_LOG"
        fi
    else
        echo "track '$track' 无 lint 命令，跳过"
    fi
done

# Step 3: 提交所有修复（lint + renumber 一起提交）
git add -A
git diff --cached --quiet || git commit -m "style: auto-fix before merge verification"
git push origin HEAD

# 保存 feature branch 名到临时文件（Phase 4 需要，避免切换到 master 后丢失）
echo "$CURRENT_BRANCH" > temp/feature-branch.txt
```

**验证条件：**
- Flyway migration 版本号与 master 无冲突
- 受影响 track 的 lint 全部通过
- 所有修改已提交并推送成功

**输出：** 将 `CURRENT_BRANCH` 记录到 `temp/feature-branch.txt`，供后续 phase 使用。

---

### Phase 1: 模拟合并到 master

> 此 phase 从 feature branch 切换到 `Git.default_branch`，将 feature branch **以 squash 方式**合并到工作区（staged 但未提交）。squash 把 feature branch 上的所有提交（包括 pg-build 自动产生的 `chore(<change>): auto-record ...` 与 `archive change ...` 等历史性提交）压成一个 staged 改动集，避免污染 master 历史。

```bash
# $CURRENT_BRANCH 在 Phase 0 中已获取
CURRENT_BRANCH=$(git branch --show-current)
DEFAULT_BRANCH=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['git']['default_branch'])")

# --- Step 1: 分支新鲜度检查与自动 rebase（仍在 feature branch 上执行） ---
# 根因预防: 特征分支落后 default_branch 过久时, squash 合并会以旧 merge-base 计算 3-way,
# 把特征分支树上从未改动的旧文件（如已被 main 升级过的 timeline/i18n）回溯进合并结果。
# rebase 到最新 default_branch 后 merge-base = 最新 HEAD, 这类文件自动取 main 版本。
STALE_LIMIT=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['verify_merge'].get('max_branch_staleness', 10))")
AUTO_REBASE=$(python3 -c "import json; print(str(json.load(open('temp/vm-context.json'))['verify_merge'].get('auto_rebase_stale', True)).lower())")
MERGE_BASE=$(git merge-base "origin/$DEFAULT_BRANCH" "origin/$CURRENT_BRANCH" 2>/dev/null || echo "")
if [ -z "$MERGE_BASE" ]; then
    echo "MERGE_BASE_MISSING: 分支关系异常，请人工确认 origin/$CURRENT_BRANCH 与 origin/$DEFAULT_BRANCH 分支基态"
    exit 1
fi
STALENESS=$(git rev-list --count "$MERGE_BASE..origin/$DEFAULT_BRANCH" 2>/dev/null || echo "0")
if [ "$STALENESS" -gt "$STALE_LIMIT" ]; then
    echo "⚠️ 特征分支落后 origin/$DEFAULT_BRANCH 共 $STALENESS 个 commit（阈值 $STALE_LIMIT）"
    if [ "$AUTO_REBASE" = "true" ]; then
        echo "→ 自动 rebase 到最新 origin/$DEFAULT_BRANCH"
        git fetch origin "$DEFAULT_BRANCH" || { echo "FETCH_FAILED"; exit 1; }
        git rebase "origin/$DEFAULT_BRANCH" || {
            echo "REBASE_CONFLICT: rebase 冲突，需人工解决后 git rebase --continue"
            exit 1
        }
        git push --force-with-lease origin "HEAD:$CURRENT_BRANCH" || {
            echo "PUSH_FAILED: 自动 rebase 后 push 失败，请人工处理"
            exit 1
        }
        echo "✓ 已 rebase 到最新 origin/$DEFAULT_BRANCH 并推送"
    else
        echo "STALE_BRANCH: auto_rebase_stale=false，请先在 feature branch 上 rebase 到 origin/$DEFAULT_BRANCH 再重试"
        exit 1
    fi
else
    echo "✓ 分支新鲜（落后 $STALENESS 个 commit，阈值 $STALE_LIMIT）"
fi

# --- Step 2: 切换到目标分支并以 squash 方式合并 ---
git checkout "$DEFAULT_BRANCH"

# 处理本地 default_branch 与 origin 偏离（详见下方"本地 default_branch 偏离"小节）
git pull --rebase origin "$DEFAULT_BRANCH" || {
    echo "PULL_FAILED: 本地与 origin 偏离，请人工处理"
    exit 1
}

git merge --squash --no-commit "origin/$CURRENT_BRANCH"

if [ $? -ne 0 ]; then
    # 合并冲突 → 回退到 feature branch
    git merge --abort 2>/dev/null || true
    git checkout "$CURRENT_BRANCH"
    echo "MERGE_CONFLICT"
    exit 1
fi

# 检测 unmerged 文件
UNMERGED=$(git ls-files -u | awk '{print $4}' | sort -u)
if [ -z "$UNMERGED" ]; then
    echo "MERGE_STATUS=CLEAN" > temp/merge-status.txt
    echo "✓ merge 无冲突，工作区干净"
else
    echo "MERGE_STATUS=DIRTY" > temp/merge-status.txt
    echo "⚠️ merge 有 unmerged 文件: $UNMERGED"
    # 这种情况下合并本应在前一步因 git merge 失败而中止，作为兜底
    git merge --abort 2>/dev/null || true
    git checkout "$CURRENT_BRANCH"
    echo "MERGE_CONFLICT"
    exit 1
fi
```

**验证条件：** 无合并冲突。Phase 1 成功后，工作区处于模拟合并状态（squashed staged, not committed），`temp/merge-status.txt` 内容为 `MERGE_STATUS=CLEAN`。

#### 本地 default_branch 与 origin 偏离

`git pull origin "$DEFAULT_BRANCH"` 在本地分支已超前或分叉时会 fatal：

```
fatal: Need to specify how to reconcile divergent branches.
```

**处理策略**：

1. **优先 `--rebase`**（`git pull --rebase origin "$DEFAULT_BRANCH"`）：
   - 保持 history linear，便于 squash-merge 后的 commit 干净
   - **推荐用于 pg-build 流程**（pg-build 自动产生的 `chore: auto-record` 系列 commit 不会污染 master 历史）
2. **次选 `--no-rebase`**（merge 策略）：
   - 会产生 merge commit，但保留完整本地历史
   - 仅当 rebase 出现冲突时使用
3. **绝不要 `--ff-only`**：
   - 偏离场景下永远会失败，等于死锁

**冲突处理**：

rebase/merge 过程中若 default_branch 上有与 feature branch 冲突的提交：

- rebase：按 git 提示解决后 `git rebase --continue`
- merge：按 git 提示解决后 `git commit`（merge commit）

常见冲突位置：
- `execution-manifest.yaml`（pg-propose 自动生成）
- `tasks.md`（tasks 标记更新）
- `.pg/changes/<change>/2-build/pipeline.events`（pg-build 自动 append）

**根因预防**：pg-build 完成后**不要**在 default_branch 上直接 commit，应保持工作区干净，pg-verify-and-merge 自然会 pull 一次到最新。

**为什么用 `--squash` 而不是 `--no-ff`**：
- 避免 feature branch 上的所有中间提交（特别是 `chore(<change>): auto-record ...` 系列与 `archive change ...`）逐条进 master 历史。
- Phase 3 用一条业务性 commit message（如 `Merge branch 'feat/pg/<change>'`）取代所有中间提交，master 历史更干净。
- 冲突检测行为与普通 merge 一致：`--squash` 仍会因冲突失败退出。

**关键约束：** Phase 1 完成后，整个 Phase 2 验证期间都必须保持在 `Git.default_branch` 分支上，**禁止切换回 feature branch**。这样 Phase 2 验证的就是合并后的代码。

---

### Phase 1.5: Diff Scope Gate（stale 文件检测）

> **核心目标**：检测合并结果中是否包含"特征分支从未改动"的文件。若发现，说明这些文件是在旧 merge-base 下被回溯成旧版（如已合入 main 的 timeline/i18n 功能被无声覆盖），必须中止。
>
> 通过条件：`stale_diff_gate=true`（默认）时，所有 staged 文件必须已被特征分支改动过。
>
> **override 机制**：当人工确认所有 stale 文件属于误报（如特征分支的 commit 被交互式 rebase 压平导致"从未改动"判断不准确），可写入 `temp/stale-gate-override` 跳过拦截。

```bash
DEFAULT_BRANCH=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['git']['default_branch'])")
CURRENT_BRANCH=$(cat temp/feature-branch.txt 2>/dev/null)
GATE_ENABLED=$(python3 -c "import json; print(str(json.load(open('temp/vm-context.json'))['verify_merge'].get('stale_diff_gate', True)).lower())")

if [ "$GATE_ENABLED" = "true" ]; then
    # 检查 override
    if [ -f temp/stale-gate-override ]; then
        echo "⚠️ Stale Gate override 已存在，跳过检测"
    else
        MERGE_BASE=$(git merge-base "origin/$DEFAULT_BRANCH" "origin/$CURRENT_BRANCH")
        STALE_FILES=()
        while IFS= read -r file; do
            [ -z "$file" ] && continue
            # 特征分支是否改过该文件？没改过却出现在合并结果里 = stale 回溯信号
            if git diff --quiet "$MERGE_BASE" "origin/$CURRENT_BRANCH" -- "$file"; then
                STALE_FILES+=("$file")
            fi
        done < <(git diff --cached --name-only)

        if [ ${#STALE_FILES[@]} -gt 0 ]; then
            echo "❌ Diff Scope Gate 拦截：以下文件在合并结果中被改动，但特征分支从未改动它们。"
            echo "   这通常是旧 merge-base 导致的 stale 回溯（如把已合入 main 的功能覆盖回旧版）。"
            printf '   - %s\n' "${STALE_FILES[@]}"
            echo "---"
            echo "   人工确认误报后，可执行: echo USR_OVERRIDE > temp/stale-gate-override"
            echo "   然后重新执行本 SKILL（Phase 1.5 会跳过检测）"
            exit 1
        fi
        echo "✓ Diff Scope Gate 通过：合并结果未携带特征分支范围外的文件改动"
    fi
fi
```

**验证条件：**
- `stale_diff_gate=true` 时：所有 `git diff --cached --name-only` 文件均被特征分支改动过，或 `temp/stale-gate-override` 存在
- `stale_diff_gate=false` 时：直接跳过，输出提示

**关键说明**：该 Gate 与 Phase 1 Step 1 的分支新鲜度检查互补。新鲜度检查通过 rebase 消除大部分 stale 回溯；Gate 则是最后的兜底防线，确保即使 rebase 未做（如 `auto_rebase_stale=false`）或 rebase 后仍有残留，也能阻止无声覆盖。

---

### Phase 1.6: 判定是否跳过测试

> **核心目标**：根据 merge 状态与 AffectedTracks，决定 Phase 2 是否需要跑测试。
> **前置条件**：Scope Gate（Phase 1.5）必须已通过（或已 override 跳过），否则跳过判断无意义。
>
> 跳过条件（任一满足即跳过）：
> 1. merge 无冲突（`MERGE_STATUS=CLEAN`）且 `verify_merge.skip_tests_if_no_conflict=true`（默认 true）
> 2. AffectedTracks 中没有可运行的 testSuite（如全部 track 都是 openapi-gen 等无 testSuite 的类型）

```bash
mkdir -p temp
SKIP_TESTS=false
SKIP_REASON=""

# 条件 1: merge 无冲突 + 配置允许跳过
MERGE_STATUS=$(cat temp/merge-status.txt | cut -d= -f2)
SKIP_IF_NO_CONFLICT=$(python3 -c "import json; print(str(json.load(open('temp/vm-context.json'))['verify_merge']['skip_tests_if_no_conflict']).lower())")
if [ "$MERGE_STATUS" = "CLEAN" ] && [ "$SKIP_IF_NO_CONFLICT" = "true" ]; then
    SKIP_TESTS=true
    SKIP_REASON="merge 无冲突且 skip_tests_if_no_conflict=true"
fi

# 条件 2: 过滤出 AffectedTracks 中存在 regression.suite 的子集
#          (pg-parse-config.py 已经按 AffectedTracks 过滤了 regressionSuites,
#          所以只需看输出字典的 keys)
if [ "$SKIP_TESTS" = "false" ]; then
    SUITES_TO_RUN=$(python3 -c "import json; print(' '.join(json.load(open('temp/vm-context.json')).get('regressionSuites', {}).keys()))")
    echo "$SUITES_TO_RUN" > temp/test-suites-to-run.txt
    
    if [ -z "$SUITES_TO_RUN" ]; then
        SKIP_TESTS=true
        SKIP_REASON="AffectedTracks 中没有可运行的 regression.suite"
    fi
fi

echo "SKIP_TESTS=$SKIP_TESTS" > temp/skip-tests.txt
echo "SKIP_REASON=$SKIP_REASON" >> temp/skip-tests.txt

if [ "$SKIP_TESTS" = "true" ]; then
    echo "✓ 跳过 Phase 2: $SKIP_REASON"
fi
```

**验证条件：**
- `temp/skip-tests.txt` 中 `SKIP_TESTS` 字段为 `true` 或 `false`
- `SKIP_TESTS=false` 时 `temp/test-suites-to-run.txt` 中至少有一个 suite 名

**关键说明**：`regressionSuites` 字典的 keys 已经是 `AffectedTracks ∩ regression.suite.<n>` 的交集（pg-parse-config.py 自动算好），不需要再做一次 `--key testSuites.$track` 探测。

---

### Phase 2: 测试套件运行

> **核心原则：** 在合并后的代码上验证，不在 feature branch 上验证。
>
> **状态保持：** Phase 2 整个过程都运行在 `Git.default_branch` 分支上，此时工作区已经包含 feature branch 的变更（staged but not committed）。Phase 2 验证的就是合并后的代码。
>
> **最终 workspace 约束：** Phase 4 成功后切回 `Git.default_branch`；失败时保持当前 workspace 不动（详见 §Phase 4 与 §异常处理）。
>
> **跳过逻辑**：当 `SKIP_TESTS=true` 时，整个 Phase 2 输出跳过原因，不执行任何测试。

#### Phase 2a-2e 通用逻辑：envSetup → verifySetup → runAllCommand → 解析

> Phase 2 内部对每个待跑 testSuite 顺序执行以下 4 步：
>
> 1. **envSetup**：启动该 suite 依赖的最小环境集（参考 pg-regression 的 set -e 严格模式）
> 2. **verifySetup**：轮询直到环境就绪（30 次重试 × 3 秒）
> 3. **runAllCommand**：跑测试命令（已含 `timeout N bash -c '...'` 包装）
> 4. **解析 outputFormat**：用 pg-parse-test-results.py 解析失败清单，给出报告

```bash
SKIP_TESTS=$(cat temp/skip-tests.txt | cut -d= -f2)

if [ "$SKIP_TESTS" = "true" ]; then
    SKIP_REASON=$(grep "^SKIP_REASON=" temp/skip-tests.txt | cut -d= -f2-)
    echo "=== Phase 2: 跳过 ==="
    echo "跳过原因: $SKIP_REASON"
else
    SUITES=$(cat temp/test-suites-to-run.txt)
    for suite in $SUITES; do
        echo "=== Phase 2x: $suite 测试套件 ==="
        
        # 从 temp/vm-context.json 一次性取该 suite 的全部 4 个字段
        SUITE_JSON=$(python3 -c "import json; print(json.dumps(json.load(open('temp/vm-context.json'))['regressionSuites']['$suite']))")
        
        # 1. envSetup (set -e 严格模式)
        ENV_SETUP=$(python3 -c "import json,sys; print(json.loads('''$SUITE_JSON''').get('envSetup') or '')")
        if [ -n "$ENV_SETUP" ]; then
            echo "--- envSetup ---"
            set -e
            eval "$ENV_SETUP"
            set +e
        fi
        
        # 2. verifySetup (30 次重试 × 3 秒)
        VERIFY_SETUP=$(python3 -c "import json,sys; print(json.loads('''$SUITE_JSON''').get('verifySetup') or '')")
        if [ -n "$VERIFY_SETUP" ]; then
            echo "--- verifySetup ---"
            READY=false
            for i in $(seq 1 30); do
                sleep 3
                if eval "$VERIFY_SETUP" > /dev/null 2>&1; then
                    READY=true
                    break
                fi
            done
            if [ "$READY" != "true" ]; then
                echo "❌ $suite 环境就绪失败（verifySetup 30 次重试后仍未通过）"
                exit 1
            fi
        fi
        
        # 3. runAllCommand (已含 timeout 包装)
        RUN_ALL=$(python3 -c "import json,sys; d=json.loads('''$SUITE_JSON''')['runAllCommand']; print(d['cmd'])")
        RUN_TIMEOUT=$(python3 -c "import json,sys; d=json.loads('''$SUITE_JSON''')['runAllCommand']; print(d.get('timeout_seconds', 1800))")
        echo "--- runAllCommand (timeout=${RUN_TIMEOUT}s) ---"
        timeout "$RUN_TIMEOUT" bash -c "$RUN_ALL" 2>&1 | tee "temp/$suite-test-output.log"
        TEST_EXIT=$?
        
        # 4. 解析 outputFormat (字符串或数组, 取第一个)
        OUTPUT_FORMAT=$(python3 -c "import json,sys; d=json.loads('''$SUITE_JSON''')['outputFormat']; print(d if isinstance(d, str) else d[0])")
        TYPE=$(echo "$OUTPUT_FORMAT" | sed 's/-json$/playwright/;s/-surefire$/maven/')
        python3 .pg/skills/src/core/workflows/scripts/pg-parse-test-results.py parse \
            --type $TYPE \
            --log-file "temp/$suite-test-output.log" \
            --out "temp/$suite-failures.json"
        
        # 检查失败
        FAILED=$(python3 -c "import json; d=json.load(open('temp/$suite-failures.json')); print(d.get('summary',{}).get('failed',0))")
        if [ "$FAILED" -gt 0 ] || [ $TEST_EXIT -ne 0 ]; then
            echo "❌ $suite 测试失败 ($FAILED 个)"
            cat "temp/$suite-failures.json"
            exit 1
        fi
    done
fi
```

**验证条件：**
- 当 `SKIP_TESTS=true` 时：输出跳过原因即可
- 当 `SKIP_TESTS=false` 时：每个 testSuite 都必须 envSetup 成功 + verifySetup 就绪 + runAllCommand 通过 + 无失败用例

**判定标准：**
- 通过率 = 通过的测试数 / 总测试数
- 允许因测试环境数据不足（如列表页数据为空导致翻页测试失败）或外部依赖问题导致的失败
- 禁止因本次变更引入的代码问题导致的失败（如 API 接口变化、组件渲染错误等）

---

### Phase 3: 提交并推送

> Phase 3 分为两步：Step A（orchestrator 执行）负责生成语义化 commit message，Step B（bash）负责提交和推送。

#### Step A（orchestrator 执行）— 撰写 commit-message.txt

在进入 Step B 之前，orchestrator（LLM）读取以下信息来源，构建一条反映变更内容的 commit message：

- **`proposal.md`**：提取标题、变更类型（feat/fix/refactor）和背景
- **`design.md`**：提取变更范围（affected_tracks、改动概要）
- **`git log origin/$CURRENT_BRANCH --not $DEFAULT_BRANCH --oneline --stat`**：提取文件变更清单

格式约束：
- **第一行（subject）** ≤ 50 字符，格式 `<type>(<scope>): <简短描述>`
- **正文** 每行 ≤ 72 字符，在 72 字符处软换行

写入 `temp/commit-message.txt`：

```bash
cat > temp/commit-message.txt << 'MSG'
feat(operation): 新增资源盘点视图

变更内容:
- 后端 MenuDefinition.java 注册 /operation/inventory 菜单(5 行)
- 前端新增 inventory.ts API 封装模块(3 个方法)
- 前端新增 inventory/list.vue 盘点主页
- 前端新增 StatCards/QuickFilterTabs/HostExpansionTable
- 前端新增 E2E 测试

Verification: merge clean, no conflicts.
Affected: backend, frontend
MSG
```

> orchestrator 按实际变更内容替换上述示例模板，保证 subject ≤ 50 字符、正文每行 ≤ 72 字符。`SKIP_TESTS=true` 时 Verification 行写 `merge clean, no conflicts.`，`false` 时写 `all tests passed.`。

#### Step B（bash 执行）— 提交并推送

```bash
CURRENT_BRANCH=$(git branch --show-current)
DEFAULT_BRANCH=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['git']['default_branch'])")

git commit -m "$(cat temp/commit-message.txt)"

if [ $? -ne 0 ]; then
    echo "COMMIT_FAILED"
    exit 1
fi

git push origin "$DEFAULT_BRANCH"
```

**验证条件：** squash 合并提交已推送到远端 `Git.default_branch`。

> **注意**：
> - Phase 3 不再执行 `git merge --abort` 再 `git merge`，因为我们已经在正确的分支和工作区状态下。只需 `git commit` 即可。
> - orchestrator 写入 `temp/commit-message.txt` 后，Step B 的 bash 读取该文件作为 commit message。`temp/commit-message.txt` 在 Phase 4 不清理（feature branch 清理时会自然消失）。

---

### Phase 4: 清理（按合并结果分支）

```bash
DEFAULT_BRANCH=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['git']['default_branch'])")
CURRENT_BRANCH=$(cat temp/feature-branch.txt 2>/dev/null || git branch --show-current)

# 检测 Phase 3 是否成功（HEAD 是本次合并的 commit）
# 启发式：HEAD commit message 含 Merge/feat/fix/chore/refactor/perf/docs 前缀
HEAD_MSG=$(git log -1 --format=%s HEAD 2>/dev/null)
if echo "$HEAD_MSG" | grep -qE "^(Merge branch|feat\(|fix\(|chore\(|refactor\(|perf\(|docs\()"; then
    # === 成功路径：已合并并 commit，切回 default_branch ===
    git checkout "$DEFAULT_BRANCH"
    echo "✓ Workspace 已切回 $DEFAULT_BRANCH"
    echo "✓ Feature branch '$CURRENT_BRANCH' 已合并，保留用于审计"
    echo "  后续手工清理时（在 $DEFAULT_BRANCH 上）执行:"
    echo "    git branch -d $CURRENT_BRANCH"
    echo "    git push origin --delete $CURRENT_BRANCH"
else
    # === 失败路径：Phase 3 未完成，保持当前 workspace 不动 ===
    echo "⚠️ 检测到合并未完成（Phase 3 失败或中止）"
    echo "  Workspace 保留在 $CURRENT_BRANCH 供人工排查"
    echo "  排查完成后，由人决定是否切回 $DEFAULT_BRANCH"
    exit 1
fi
```

**注意：** Phase 4 不再"无条件切回 default_branch"，而是按 Phase 3 的实际结果分支：

- **成功**（HEAD 是本次合并的 commit）→ 切回 default_branch → 流程结束
- **失败**（HEAD 不是合并 commit）→ 保持当前 workspace 不动 → `exit 1` 让编排器中止

---

## 输出格式

```
目标分支: <Git.default_branch>
AffectedTracks: <tracks>
Skip Tests: <true|false>
Skip Reason: <reason if skipped>
Phase: <phase> (<phase_name>)
状态: SUCCESS|FAILED
最终 workspace: <branch-name>  ← SUCCESS 时必为 Git.default_branch；FAILED 时为执行中止分支
最终 HEAD SHA: <git rev-parse HEAD>
```

失败时额外输出：
```
失败 Phase: <failed_phase>
失败原因: <description>
下一步: 根据失败阶段参考异常处理表
```

## 异常处理

| 失败阶段 | 处理方式 | 说明 |
|---------|---------|------|
| **Setup**（pg-parse-config.py 失败） | 中止，提示修复 config.yaml | exit code ≠ 0 通常因 config 不合规（如 regression.suite 缺 module） |
| Setup（affected_tracks 推断全部失败） | 中止，提示手动传 `--affected-tracks` | tasks.md 缺失 + git diff 失败 + 无 suite_keys 三层兜底全失败 |
| Phase 0 (renumber) | 中止，提示手动检查 migration 版本冲突 | 自动重编号失败，通常因本地 default_branch 不存在或 git tree 不完整 |
| Phase 0 (lint) | 中止，提示手动修复 | lint 自动修复未必全覆盖 |
| Phase 1 | 中止，提示手动解决冲突 | 合并冲突必须人工介入 |
| Phase 1 (merge 无冲突但分支 stale) | 中止，提示人工处理 | 详见下方 `STALE_BRANCH` / `REBASE_CONFLICT` |
| Phase 1.5 (Scope Gate) | 中止，提示人工确认 stale 文件 | 合并结果携带特征分支从未改动过的文件（stale 回溯），确认误报后写 `temp/stale-gate-override` 重试 |
| Phase 1 (STALE_BRANCH) | 中止，提示先 rebase 再重试 | `auto_rebase_stale=false`，但特征分支落后超过 `max_branch_staleness` |
| Phase 1 (REBASE_CONFLICT) | 中止，人工解决 rebase 冲突后 `git rebase --continue`，再重跑本 SKILL | 自动 rebase 时与 main 上的新提交冲突 |
| Phase 2 (envSetup) | 中止，提示环境问题 | 依赖服务未启动或配置错误 |
| Phase 2 (verifySetup) | 中止，提示环境未就绪 | 30 次重试后仍未就绪 |
| Phase 2 (runAllCommand) | 中止，提示修复并重试 | 测试失败，需人工修复 |
| Phase 3 | 中止，提示手动解决合并问题 | 冲突窗口期 default_branch 可能又有新提交 |

**不回退。** 任何阶段失败直接中止并报告，由人工决策下一步。

### Phase 4 之后的越权操作（防御性约束）

| 场景 | 行为 |
|------|------|
| 合并**成功**后主动切回 feature branch | **视为协议违反**。清理 feature branch 应由人在 default_branch 上独立执行 |
| 合并**失败**后主动切到 default_branch | **视为协议违反**。失败时 workspace 应保留在错误现场供人排查 |
| 合并成功后直接 `git branch -D` 删除 feature branch（即使 squash merge 完成） | 不在本 SKILL 权限内 — squash commit 在 default_branch 上独立存在，feature branch 删除策略由项目治理决定 |

## 与 pg-build 的集成

`pg-build` 完成后**不会自动触发**本工作流。编排器输出最终报告后即停止，等待用户明确指示（如自然语言"verify 并合并"、"合并到 master"）后再加载本 SKILL。

```
pg-build（feature 开发与验证）
    ↓ (输出最终报告后停止，等待用户明确指示)
pg-verify-and-merge（合并前验证与合并）  ← 仅由用户显式触发
```

编排器（manager agent）在 pg-build 末尾输出最终报告后**终止**，不再继续执行；由用户明确要求合并时再加载 `pg-verify-and-merge` SKILL。

## 配置变更记录

### v2.0 → v3.0 硬切换（无兼容层）

| v2 字段 | v3.0 替代 | 备注 |
|---------|----------|------|
| `pipeline.tracks.<t>.lint` | `tracks.<t>.lint` (override) 或 `modules.<tracks.<t>.modules[0]>.lint` | 字段名变更 + 字段位置变更 |
| `testSuites.<t>.{envSetup,verifySetup,runAllCommand,outputFormat}` | `regression.suite.<t>` + 派生计算 | 段名变更 + 字段派生 |
| AffectedTracks 由 manager agent 显式传入 | **自动推断**（tasks.md → git diff → suite_keys 三层 fallback） | 减少 LLM 手工负担 |
| 4 次 `--key` 调用取配置 | **1 次** `pg-parse-config.py pg-verify-and-merge` 取全部配置 | 注入 temp/vm-context.json 后所有 phase 共用 |

**硬切换声明**：v2 字段在 `pg-parse-config.py` 中返回 `null`（无任何 fallback），`--key pipeline.tracks.X.lint` 和 `--key testSuites.X.*` 都会拿不到值。SKILL.md 也不再出现这两个字段名。
