#!/bin/bash
# scripts/renumber-flyway-migration.sh
#
# 自动重编号当前分支新增的 Flyway migration 文件，解决并行开发冲突。
#
# 用法：
#   bash scripts/renumber-flyway-migration.sh                            # 自动检测并重编号
#   bash scripts/renumber-flyway-migration.sh --dry-run                   # 预览，不做实际变更
#   bash scripts/renumber-flyway-migration.sh \                          # 从 config 指定参数
#       --migration-dir "webvirt-backend/.../migration" \
#       --default-branch master
#
# 工作方式：
#   1. 找到默认分支上不存在的 migration 文件（即当前分支新增的）
#   2. 按文件名字母序保持它们的相对顺序
#   3. 以默认分支当前最大版本号 + 1 为起始，重新编号
#   4. 同步更新 pg-spec 中 design.md 的文件名引用
#
# 配置来源（优先级：命令行 > 自动检测）：
#   --migration-dir    migration 目录（相对项目根），默认 webvirt-backend/.../migration
#   --default-branch   参照分支名，默认自动检测 master/main

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 默认值 ───────────────────────────────────────────────────────────
MIGRATION_REL="webvirt-backend/webvirt-bootstrap/src/main/resources/db/migration"
DEFAULT_BRANCH=""
DRY_RUN=false

# ── 参数解析 ──────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --migration-dir) MIGRATION_REL="$2"; shift 2 ;;
    --default-branch) DEFAULT_BRANCH="$2"; shift 2 ;;
    --dry-run|-n) DRY_RUN=true; shift ;;
    --help|-h)
      echo "Usage: $0 [--migration-dir <path>] [--default-branch <branch>] [--dry-run|-n]"
      exit 0 ;;
    *) echo "❌ 未知参数: $1"; exit 1 ;;
  esac
done

MIGRATION_DIR="$PROJECT_DIR/$MIGRATION_REL"

# ── 检测默认分支 ──────────────────────────────────────────────────────
if [ -z "$DEFAULT_BRANCH" ]; then
  for ref in "master" "main" "origin/master" "origin/main"; do
    if git -C "$PROJECT_DIR" rev-parse --verify "$ref" &>/dev/null; then
      DEFAULT_BRANCH="$ref"
      break
    fi
  done
  if [ -z "$DEFAULT_BRANCH" ]; then
    echo "❌ 未指定 --default-branch 且无法自动检测 (master/main)" >&2
    exit 1
  fi
fi

CURRENT_BRANCH=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD)
echo "🌿 当前分支: $CURRENT_BRANCH"
echo "📦 参照分支: $DEFAULT_BRANCH"

# ── 收集 master 上已有的 migration 文件列表 ───────────────────────────
# 从 git tree 中读取，不依赖本地文件系统
MASTER_FILES=$(git -C "$PROJECT_DIR" ls-tree -r "$DEFAULT_BRANCH" --name-only "$MIGRATION_REL/" 2>/dev/null || true)

if [ -z "$MASTER_FILES" ]; then
  echo "⚠️  无法从 $DEFAULT_BRANCH 读取 migration 列表，尝试从 HEAD 对比..." >&2
  # 降级：用 merge-base 与 HEAD 比较
  MERGE_BASE=$(git -C "$PROJECT_DIR" merge-base HEAD "$DEFAULT_BRANCH" 2>/dev/null || echo "")
  if [ -n "$MERGE_BASE" ]; then
    MASTER_FILES=$(git -C "$PROJECT_DIR" ls-tree -r "$MERGE_BASE" --name-only "$MIGRATION_REL/" 2>/dev/null || true)
  fi
fi

# ── 找出当前分支新增的 migration 文件 ─────────────────────────────────
NEW_FILES=()
while IFS= read -r -d '' f; do
  basename_f=$(basename "$f")
  path_in_master="$MIGRATION_REL/$basename_f"
  if ! echo "$MASTER_FILES" | grep -qxF "$path_in_master"; then
    NEW_FILES+=("$f")
  fi
done < <(find "$MIGRATION_DIR" -maxdepth 1 -name 'V*.sql' -print0 | sort -z)

if [ ${#NEW_FILES[@]} -eq 0 ]; then
  echo "✅ 没有需要重编号的新 migration 文件"
  exit 0
fi

echo ""
echo "📄 当前分支新增的 migration 文件（共 ${#NEW_FILES[@]} 个）:"
for f in "${NEW_FILES[@]}"; do
  echo "   $(basename "$f")"
done

# ── 找到 master 上当前最大版本号 ─────────────────────────────────────
MAX_VERSION=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  f=$(basename "$line")
  v=$(echo "$f" | sed -n 's/^V0*\([0-9][0-9]*\)__.*/\1/p')
  if [ -n "$v" ] && [ "$v" -gt "$MAX_VERSION" ]; then
    MAX_VERSION=$v
  fi
done <<< "$MASTER_FILES"

echo ""
echo "🔢 $DEFAULT_BRANCH 上最大版本号: V${MAX_VERSION}"

# ── 构建重编号映射 ────────────────────────────────────────────────────
NEXT=$((MAX_VERSION + 1))
declare -a RENAME_PAIRS
declare -a NEW_NAMES

IDX=0
for f in "${NEW_FILES[@]}"; do
  basename_f=$(basename "$f")
  old_ver=$(echo "$basename_f" | sed -n 's/^V0*\([0-9][0-9]*\)__.*/\1/p')
  desc=$(echo "$basename_f" | sed -n 's/^V[0-9]*__\(.*\)/\1/p')
  new_name="V${NEXT}__${desc}"
  echo "   $(basename "$f")  →  $new_name"

  if [ "$old_ver" = "$NEXT" ]; then
    echo "   ⏭️  版本 V${NEXT} 无需变动"
    NEXT=$((NEXT + 1))
    continue
  fi

  RENAME_PAIRS[$IDX]="$f|$MIGRATION_DIR/$new_name|$basename_f|$new_name"
  NEW_NAMES[$IDX]="$new_name"
  IDX=$((IDX + 1))
  NEXT=$((NEXT + 1))
done

if [ ${#RENAME_PAIRS[@]} -eq 0 ]; then
  echo "✅ 所有文件版本号已是最新，无需变更"
  exit 0
fi

if [ "$DRY_RUN" = true ]; then
  echo ""
  echo "⏸️  DRY-RUN 模式，未做实际变更"
  exit 0
fi

# ── 执行重命名 ────────────────────────────────────────────────────────
echo ""
echo "🚀 执行重命名..."

for pair in "${RENAME_PAIRS[@]}"; do
  IFS='|' read -r old_path new_path old_name new_name <<< "$pair"
  if git -C "$PROJECT_DIR" ls-files --error-unmatch "$old_path" &>/dev/null; then
    git -C "$PROJECT_DIR" mv "$old_path" "$new_path"
  else
    mv "$old_path" "$new_path"
  fi
  echo "   ✓ $old_name → $new_name"
done

# ── 更新设计文档引用 ──────────────────────────────────────────────────
echo ""
echo "📝 更新设计文档引用..."

DESIGN_FILES=$(find "$PROJECT_DIR/pg-spec" -name 'design.md' 2>/dev/null || true)
for pair in "${RENAME_PAIRS[@]}"; do
  IFS='|' read -r _ _ old_name new_name <<< "$pair"
  [ -z "$DESIGN_FILES" ] && break
  for df in $DESIGN_FILES; do
    if grep -q "$old_name" "$df" 2>/dev/null; then
      sed -i "s/$old_name/$new_name/g" "$df"
      echo "   ✓ 更新 $(realpath --relative-to="$PROJECT_DIR" "$df")"
    fi
  done
done

echo ""
echo "✅ 重编号完成，共处理 ${#RENAME_PAIRS[@]} 个文件"
echo ""
echo "💡 提示:"
echo "   - 使用 git status 查看变更"
echo "   - 确认无误后提交即可合并"
