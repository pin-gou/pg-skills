#!/usr/bin/env bash
# pg-context-chain.sh — Context Chain 管理脚本
#
# 所有时间戳由脚本内部获取 (date -Iseconds)，不依赖调用方传入。
# 所有写入使用 >> 追加，永不覆盖。
#
# 用法:
#   pg-context-chain.sh init <change-name>
#   pg-context-chain.sh restart <change-name>
#   pg-context-chain.sh sub-start <change-name> <track> <sub>
#   pg-context-chain.sh sub-end <change-name> <track> <sub> <status> [report] [summary] [outputs] [issues]
#   pg-context-chain.sh phase-start <change-name> <phase-id>
#   pg-context-chain.sh phase-end <change-name> <phase-id> [summary]
#   pg-context-chain.sh rollback-set <change-name> <track> <reason> <source> [level=path]
#   pg-context-chain.sh rollback-clear <change-name> <track>
#   pg-context-chain.sh rollback-get <change-name> <track>
#   pg-context-chain.sh workflow-complete <change-name> <status>
#
# 状态文件: .pg/changes/<change>/2-build/.context-chain.state

set -euo pipefail

COMMAND="${1:-}"
CHANGE="${2:-}"
CHANGE_DIR=".pg/changes/${CHANGE}"
APPLY_DIR="2-build"
CONTEXT_CHAIN="${CHANGE_DIR}/${APPLY_DIR}/context-chain.md"
STATE_FILE="${CHANGE_DIR}/${APPLY_DIR}/.context-chain.state"

now() {
  date -Iseconds
}

write_state() {
  local key="$1" value="$2"
  # 如果 state 文件已存在且已有该 key，则替换；否则追加
  if grep -q "^${key}=" "$STATE_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$STATE_FILE"
  else
    echo "${key}=${value}" >> "$STATE_FILE"
  fi
}

read_state() {
  local key="$1"
  grep "^${key}=" "$STATE_FILE" 2>/dev/null | cut -d= -f2- || true
}

cmd_init() {
  mkdir -p "$CHANGE_DIR/$APPLY_DIR"
  if [[ -f "$CONTEXT_CHAIN" ]]; then
    # 文件已存在 → 追加 RESTART 记录（保留完整历史）
    cmd_restart "$CHANGE"
  else
    # 首次运行 → 创建新文件
    cat > "$CONTEXT_CHAIN" <<- EOF
# Context Chain - ${CHANGE}

---
*此文件由编排器自动管理，请勿手动修改*

EOF
    local ts
    ts=$(now)
    write_state "start_timestamp" "$ts"
  fi
}

cmd_restart() {
  mkdir -p "$CHANGE_DIR"
  local ts
  ts=$(now)
  write_state "start_timestamp" "$ts"
  cat >> "$CONTEXT_CHAIN" <<- EOF

### ${ts} - WORKFLOW RESTART
**状态**: RESTARTED
**说明**: 编排器重新执行 \`/3-pg-build\`，从第一个未完成项继续

EOF
}

cmd_sub_start() {
  local track="$3" sub="$4"
  local ts
  ts=$(now)
  cat >> "$CONTEXT_CHAIN" <<- EOF

### ${ts} - ${track}:${sub} START
**状态**: IN_PROGRESS

EOF
}

cmd_sub_end() {
  local track="$3" sub="$4" status="$5"
  local report="${6:-}" summary="${7:-}" outputs="${8:-}" issues="${9:-}"
  local ts
  ts=$(now)
  cat >> "$CONTEXT_CHAIN" <<- EOF

### ${ts} - ${track}:${sub} END
**状态**: ${status}
**报告**: ${report}
**摘要**: ${summary}
**输出文件**: ${outputs}
**问题**: ${issues}

EOF
}

cmd_phase_start() {
  local phase="$3"
  local ts
  ts=$(now)
  cat >> "$CONTEXT_CHAIN" <<- EOF

### ${ts} - ${phase} START
**状态**: IN_PROGRESS

EOF
}

cmd_phase_end() {
  local phase="$3"
  local summary="${4:-}"
  local ts
  ts=$(now)
  cat >> "$CONTEXT_CHAIN" <<- EOF

### ${ts} - ${phase} END
**状态**: COMPLETED
**摘要**: ${summary}

EOF
}

cmd_rollback_set() {
  local track="$3" reason="$4" source="$5" level="${6:-path}"
  local ts
  ts=$(now)
  cat >> "$CONTEXT_CHAIN" <<- EOF

## rollback_context: ${track}
- timestamp: ${ts}
- level: ${level}
- reason: ${reason}
- source: ${source}
ENDMARKER
EOF
  # sed 移除末尾的 ENDMARKER 行和它前面的空行
  sed -i '/^ENDMARKER$/d' "$CONTEXT_CHAIN"
}

cmd_rollback_clear() {
  local track="$3"
  local ts
  ts=$(now)
  if grep -q "^## rollback_context: ${track}$" "$CONTEXT_CHAIN" 2>/dev/null; then
    # 删除该 track 的 rollback 段（标题 + 3 行内容）
    sed -i "/^## rollback_context: ${track}$/,/^## rollback_context:/{/^## rollback_context: ${track}$/d; /^## rollback_context:/!d;}" "$CONTEXT_CHAIN"
    # 如果上面 sed 后还有残余空行，清理
    sed -i '/^$/N;/^\n$/D' "$CONTEXT_CHAIN" 2>/dev/null || true
  fi
}

cmd_rollback_get() {
  local track="$3"
  local block
  block=$(awk -v t="${track}" '
    $0 == "## rollback_context: " t {
      found = 1; next
    }
    found && /^## rollback_context: / {
      found = 0; exit
    }
    found {
      print
    }
  ' "$CONTEXT_CHAIN" 2>/dev/null || true)
  if [[ -z "$block" ]]; then
    echo '{"found": false}'
  else
    local ts reason source level
    ts=$(echo "$block" | grep "^\- timestamp:" | sed 's/^.*: //')
    level=$(echo "$block" | grep "^\- level:" | sed 's/^.*: //')
    reason=$(echo "$block" | grep "^\- reason:" | sed 's/^.*: //')
    source=$(echo "$block" | grep "^\- source:" | sed 's/^.*: //')
    [[ -z "$level" ]] && level="path"
    cat <<- JSON
{"found": true, "timestamp": "${ts}", "level": "${level}", "reason": "${reason}", "source": "${source}"}
JSON
  fi
}

cmd_workflow_complete() {
  local status="$3"
  local ts
  ts=$(now)

  # 从 state 读取启动时间，计算总耗时
  local start_ts
  start_ts=$(read_state "start_timestamp" || echo "")
  local duration=""
  if [[ -n "$start_ts" ]]; then
    local start_epoch end_epoch elapsed minutes seconds
    start_epoch=$(date -d "$start_ts" +%s 2>/dev/null || echo 0)
    end_epoch=$(date -d "$ts" +%s 2>/dev/null || echo 0)
    if [[ "$start_epoch" -gt 0 && "$end_epoch" -gt 0 ]]; then
      elapsed=$((end_epoch - start_epoch))
      minutes=$((elapsed / 60))
      seconds=$((elapsed % 60))
      duration="${minutes}m ${seconds}s"
    fi
  fi

  cat >> "$CONTEXT_CHAIN" <<- EOF

### ${ts} - WORKFLOW COMPLETED
**状态**: ${status}
**总耗时**: ${duration}

EOF
}

usage() {
  sed -n '/^# 用法:/,/^[^#]/p' "$0" | head -n -1
  exit 1
}

case "${COMMAND}" in
  init)
    [[ -z "$CHANGE" ]] && usage
    cmd_init
    ;;
  restart)
    [[ -z "$CHANGE" ]] && usage
    cmd_restart
    ;;
  sub-start)
    [[ -z "${3:-}" || -z "${4:-}" ]] && usage
    cmd_sub_start "$@"
    ;;
  sub-end)
    [[ -z "${3:-}" || -z "${4:-}" || -z "${5:-}" ]] && usage
    cmd_sub_end "$@"
    ;;
  phase-start)
    [[ -z "${3:-}" ]] && usage
    cmd_phase_start "$@"
    ;;
  phase-end)
    [[ -z "${3:-}" ]] && usage
    cmd_phase_end "$@"
    ;;
  rollback-set)
    [[ -z "${3:-}" || -z "${4:-}" || -z "${5:-}" ]] && usage
    cmd_rollback_set "$@"
    ;;
  rollback-clear)
    [[ -z "${3:-}" ]] && usage
    cmd_rollback_clear "$@"
    ;;
  rollback-get)
    [[ -z "${3:-}" ]] && usage
    cmd_rollback_get "$@"
    ;;
  workflow-complete)
    [[ -z "${3:-}" ]] && usage
    cmd_workflow_complete "$@"
    ;;
  *)
    usage
    ;;
esac
