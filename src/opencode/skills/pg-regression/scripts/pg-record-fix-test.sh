#!/usr/bin/env bash
# pg-record-fix-test.sh — 留痕 pg-regression/fix-test agent 调用的 prompt / response / result.
#
# 由 pg-regression SKILL Phase 2.3a 在每个 fix-test agent 返回后立即调用,
# 把 agent 的完整输入与输出落到 ${RUN_DIR}/fix-test/${IDX}-${SLUG}/.
#
# 设计原则:
#   - prompt / response 通过 --prompt-file / --response-file 路径传入, 避免大文件在 bash heredoc 中转义
#   - result-json 是可选的, 直接以 JSON 字符串写入 3-result.json
#   - idx 全局递增, 与 fix-issues/ 共享序号空间
#   - target-slug 与 SKILL:429 保持一致: kebab-case, 字母数字 + 连字符, 限长 40
#   - 不传 --result-json 时仅落 1-prompt.md / 2-response.md, 跳过 3-result.json
#   - 幂等: 已存在的目标目录会保留已有 1-prompt.md / 2-response.md, 不覆盖 (避免 agent 重复 dispatch 冲掉审计)
#
# Usage:
#   bash pg-record-fix-test.sh \
#     --run-dir "$RUN_DIR" \
#     --idx 1 \
#     --target "tests/e2e/specs/admin/maintenance/host/host-disk-path.spec.ts" \
#     --prompt-file /tmp/fix-test-prompt-1.md \
#     --response-file /tmp/fix-test-response-1.md \
#     [--result-json '{"summary":{"fixed":1,"cantFix":0},"fixes":[],"cantFixIssues":[],"modifiedFiles":[]}']

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: pg-record-fix-test.sh --run-dir DIR --idx N --target TARGET \
                             --prompt-file FILE --response-file FILE \
                             [--result-json JSON]

必填参数:
  --run-dir        regression run 目录 (如 .pg/regression/frontend-20260627-02)
  --idx            全局递增序号 (正整数, 与 fix-issues/ 共享序号空间)
  --target         测试单元标识 (如 xxx.spec.ts 或 xxxTest.java)
  --prompt-file    agent 输入提示词的临时文件路径
  --response-file  agent 完整回复的临时文件路径

可选参数:
  --result-json    结构化结果 JSON 字符串, 直接写入 3-result.json
USAGE
  exit 2
}

RUN_DIR=""
IDX=""
TARGET=""
PROMPT_FILE=""
RESPONSE_FILE=""
RESULT_JSON=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir)        RUN_DIR="$2";        shift 2 ;;
    --idx)            IDX="$2";            shift 2 ;;
    --target)         TARGET="$2";         shift 2 ;;
    --prompt-file)    PROMPT_FILE="$2";    shift 2 ;;
    --response-file)  RESPONSE_FILE="$2";  shift 2 ;;
    --result-json)    RESULT_JSON="$2";    shift 2 ;;
    -h|--help)        usage ;;
    *) echo "❌ unknown arg: $1" >&2; usage ;;
  esac
done

# === 参数校验 ===
if [[ -z "$RUN_DIR" || -z "$IDX" || -z "$TARGET" || -z "$PROMPT_FILE" || -z "$RESPONSE_FILE" ]]; then
  echo "❌ missing required args" >&2
  usage
fi

if ! [[ "$IDX" =~ ^[1-9][0-9]*$ ]]; then
  echo "❌ --idx must be a positive integer (got: $IDX)" >&2
  exit 2
fi

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "❌ --prompt-file not found: $PROMPT_FILE" >&2
  exit 2
fi

if [[ ! -f "$RESPONSE_FILE" ]]; then
  echo "❌ --response-file not found: $RESPONSE_FILE" >&2
  exit 2
fi

# === target-slug (与 SKILL:429 一致) ===
SLUG="$(printf '%s' "$TARGET" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//' | cut -c1-40)"
if [[ -z "$SLUG" ]]; then
  SLUG="unknown"
fi

BASE="${RUN_DIR}/fix-test/${IDX}-${SLUG}"
mkdir -p "$BASE"

# === 1-prompt.md (幂等: 已存在不覆盖) ===
if [[ -f "$BASE/1-prompt.md" ]]; then
  echo "⚠️  $BASE/1-prompt.md exists, skip (idempotent)"
else
  cp "$PROMPT_FILE" "$BASE/1-prompt.md"
fi

# === 2-response.md (幂等) ===
if [[ -f "$BASE/2-response.md" ]]; then
  echo "⚠️  $BASE/2-response.md exists, skip (idempotent)"
else
  cp "$RESPONSE_FILE" "$BASE/2-response.md"
fi

# === 3-result.json (可选, 写一次即不再覆盖) ===
if [[ -n "$RESULT_JSON" ]]; then
  if [[ -f "$BASE/3-result.json" ]]; then
    echo "⚠️  $BASE/3-result.json exists, skip (idempotent)"
  else
    printf '%s\n' "$RESULT_JSON" > "$BASE/3-result.json"
  fi
fi

echo "✅ 留痕: $BASE"