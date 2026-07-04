---
name: pg-verify-and-merge
description: 将 feature branch 模拟合并到 master 并按需验证后合并。pg-build 完成后自动触发。
license: MIT
compatibility: 项目根目录需要 `.pg/project.yaml`（v3.0 schema：modules / environments / tracks / stages / regression.suite / verify_merge / flyway / git）。SKILL 通过 `python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-verify-and-merge` 统一注入所有配置（tracks / regressionSuites / verify_merge / flyway / git 五段 JSON），不再单独调用 `--key` 取值。
metadata:
  author: pg
  version: "3.0"
---

# pg-verify-and-merge

## 概述

pg-build 完成后，将 feature branch 合并到 master 前，先将 feature branch 合并到 master 的工作区（模拟合并），然后**按受影响范围与 merge 冲突状态**决定是否需要运行测试套件，确保主分支稳定性。

**核心原则：** 在合并后的代码上验证，不在 feature branch 上验证。

**关键改进（v3.0）**：

- **与 pg-regression / pg-fix-issue 同一套配置**：所有命令、路径、env 派生自 `.pg/project.yaml`，不再有 v2 的 `pipeline.tracks.*.lint` 和已废弃的 `testSuites.*` 段引用（硬切换，无兼容层）。
- **AffectedTracks 自动推断**：从 `<change>/tasks.md` 章节号读起，tasks.md 缺失则 fallback 到 `git diff` + `tracks.<t>.root` 路径前缀匹配，最后 fallback 到 `regression.suite` 的 key 列表。**simple track 永远过滤**（`openapi-gen` 等跑 commands 不跑 TDVG，无 regression.suite）。
- **按 AffectedTracks 过滤**：只跑 manager agent 传入（或自动推断）的受影响 track 对应的 testSuite（不是全跑）。
- **merge 无冲突时跳过测试**：`verify_merge.skip_tests_if_no_conflict=true`（默认）时，无冲突 = 跳过 Phase 2 = 加速合并。
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
| `verify_merge.skip_tests_if_no_conflict` | `verify_merge.skip_tests_if_no_conflict` | Phase 1.5 跳过判断 |
| `flyway.migration_path` | `flyway.migration_path` | Phase 0 migration 重编号 |
| `git.default_branch` | `git.default_branch` | Phase 1/3 目标分支 |

## 前置条件

- Feature branch 已推送到远端
- 当前在 feature branch 上，无未提交的修改（pg-build 已完成并提交）
- `git remote` 可访问 origin/`Git.default_branch`
- `.pg/changes/<change>/tasks.md` 存在（pg-build 阶段已生成）

## 阶段结构

**前置步骤**（orchestrator 执行）：

```bash
# 一次性注入所有配置到 temp/vm-context.json
mkdir -p temp
python3 .pg/skills/src/opencode/scripts/pg-parse-config.py pg-verify-and-merge \
    --change-dir ".pg/changes/<CHANGE>" \
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
    ├── 合并
    └── 检测 unmerged 文件，写入 temp/merge-status.txt
    ↓
Phase 1.5: 判定是否跳过 Phase 2
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
bash .opencode/skills/pg-verify-and-merge/scripts/renumber-flyway-migration.sh \
    --migration-dir "$MIGRATION_PATH" \
    --default-branch "$DEFAULT_BRANCH" || {
    echo "FLYWAY_RENUMBER_FAILED"
    exit 1
}

# Step 2: 对 AffectedTracks 每个 track 跑 lint
# lint_cmd 已是 dict {cmd, timeout_seconds}（pg-parse-config.py 直接产出）
for track in $AFFECTED; do
    LINT_CMD=$(python3 -c "import json; t=json.load(open('temp/vm-context.json'))['tracks'].get('$track',{}).get('lint_cmd'); print(t['cmd'] if t else '')")
    if [ -n "$LINT_CMD" ]; then
        TIMEOUT=$(python3 -c "import json; t=json.load(open('temp/vm-context.json'))['tracks'].get('$track',{}).get('lint_cmd'); print(t.get('timeout_seconds', 1800))")
        echo "=== Lint $track (timeout=${TIMEOUT}s) ==="
        timeout "$TIMEOUT" bash -c "$LINT_CMD" 2>&1 | tail -50
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

# 切换到目标分支并以 squash 方式合并
git checkout "$DEFAULT_BRANCH"
git pull origin "$DEFAULT_BRANCH"
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

**为什么用 `--squash` 而不是 `--no-ff`**：
- 避免 feature branch 上的所有中间提交（特别是 `chore(<change>): auto-record ...` 系列与 `archive change ...`）逐条进 master 历史。
- Phase 3 用一条业务性 commit message（如 `Merge branch 'feat/pg/<change>'`）取代所有中间提交，master 历史更干净。
- 冲突检测行为与普通 merge 一致：`--squash` 仍会因冲突失败退出。

**关键约束：** Phase 1 完成后，整个 Phase 2 验证期间都必须保持在 `Git.default_branch` 分支上，**禁止切换回 feature branch**。这样 Phase 2 验证的就是合并后的代码。

---

### Phase 1.5: 判定是否跳过测试

> **核心目标**：根据 merge 状态与 AffectedTracks，决定 Phase 2 是否需要跑测试。
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
        python3 .pg/skills/src/opencode/scripts/pg-parse-test-results.py parse \
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

### Phase 4: 清理

```bash
CURRENT_BRANCH=$(cat temp/feature-branch.txt 2>/dev/null || git branch --show-current)
DEFAULT_BRANCH=$(python3 -c "import json; print(json.load(open('temp/vm-context.json'))['git']['default_branch'])")

# 防御性收尾：无条件切回 default_branch
# 确保即使 Phase 1 失败后编排者走了 feature branch fallback，最终 workspace 仍在 master
git checkout "$DEFAULT_BRANCH"

# 提示保留 feature branch 用于审计（后续手工清理）
echo "Feature branch '$CURRENT_BRANCH' has been merged and committed to $DEFAULT_BRANCH"
echo "Feature branch 保留用于审计，后续手工清理时执行:"
echo "  git branch -d $CURRENT_BRANCH"
echo "  git push origin --delete $CURRENT_BRANCH"
```

**注意：** Phase 4 无条件执行 `git checkout $DEFAULT_BRANCH` 作为防御性收尾。不再依赖"整个流程都在 default_branch 上"的假设——即使编排者在 Phase 1 失败后走了 feature branch fallback，Phase 4 也保证最终 workspace 回到 master。

---

## 输出格式

```
目标分支: <Git.default_branch>
AffectedTracks: <tracks>
Skip Tests: <true|false>
Skip Reason: <reason if skipped>
Phase: <phase> (<phase_name>)
状态: SUCCESS|FAILED
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
| Phase 2 (envSetup) | 中止，提示环境问题 | 依赖服务未启动或配置错误 |
| Phase 2 (verifySetup) | 中止，提示环境未就绪 | 30 次重试后仍未就绪 |
| Phase 2 (runAllCommand) | 中止，提示修复并重试 | 测试失败，需人工修复 |
| Phase 3 | 中止，提示手动解决合并问题 | 冲突窗口期 default_branch 可能又有新提交 |

**不回退。** 任何阶段失败直接中止并报告，由人工决策下一步。

## 与 pg-build 的集成

`pg-build` 完成后，所有 phase 均通过时，**自动触发** pg-verify-and-merge 工作流，无需人工确认。

```
pg-build（feature 开发与验证）
    ↓ (manager agent 自动触发，从最终报告读取 affected_tasks → 转为 AffectedTracks 传入)
pg-verify-and-merge（合并前验证与合并）
```

编排器（manager agent）在 pg-build 末尾输出最终报告后，加载 `pg-verify-and-merge` SKILL 继续执行。

## 配置变更记录

### v2.0 → v3.0 硬切换（无兼容层）

| v2 字段 | v3.0 替代 | 备注 |
|---------|----------|------|
| `pipeline.tracks.<t>.lint` | `tracks.<t>.lint` (override) 或 `modules.<tracks.<t>.modules[0]>.lint` | 字段名变更 + 字段位置变更 |
| `testSuites.<t>.{envSetup,verifySetup,runAllCommand,outputFormat}` | `regression.suite.<t>` + 派生计算 | 段名变更 + 字段派生 |
| AffectedTracks 由 manager agent 显式传入 | **自动推断**（tasks.md → git diff → suite_keys 三层 fallback） | 减少 LLM 手工负担 |
| 4 次 `--key` 调用取配置 | **1 次** `pg-parse-config.py pg-verify-and-merge` 取全部配置 | 注入 temp/vm-context.json 后所有 phase 共用 |

**硬切换声明**：v2 字段在 `pg-parse-config.py` 中返回 `null`（无任何 fallback），`--key pipeline.tracks.X.lint` 和 `--key testSuites.X.*` 都会拿不到值。SKILL.md 也不再出现这两个字段名。
