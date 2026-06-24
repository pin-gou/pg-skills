#!/usr/bin/env bash
# pg-skills template: role stop action.
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/<role>-stop.sh
#   2. 把 CMD_PLACEHOLDER 替换为实际的停止命令
#   3. chmod +x .pg/hooks/<role>-stop.sh
#
# 本模板对应 schema 节点:
#   environments.<env>.roles.<r>.actions.stop.script
#
# 由 pg-run-hook.py 调起, 注入的 env vars 见 role-start.sh 头部注释.
#
# 注意: stop 命令应当幂等 (第二次跑无副作用). 常见实现:
#   pkill -f <process-pattern> || true
#   docker compose down
#   ssh user@host 'systemctl stop <svc>'

set -euo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PG_SKILLS_PATH="${PG_SKILLS_PATH:-$SELF_DIR}"
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

# ---- TODO: 替换为本 role 的停止命令 ----
# 例: pkill -f 'kuboard-server|spring-boot:run' || true
# 占位符故意保留非空字符串, 防止未替换就运行.
CMD_PLACEHOLDER="echo REPLACE_ME_WITH_STOP_COMMAND"

START=$(date +%s)
if bash -c "$CMD_PLACEHOLDER" > "$PG_LOG_FILE" 2>&1; then
    DURATION=$(($(date +%s) - START))
    pg_exit --status=pass --duration=$DURATION \
            --metadata="cmd=\"$CMD_PLACEHOLDER\" role=\"${PG_ROLE:-}\" instance=\"${PG_INSTANCE_NAME:-}\""
else
    EC=$?
    DURATION=$(($(date +%s) - START))
    pg_fail \
        --category=health_check_fail \
        --code=PG-E-1011 \
        --message="stop for role '${PG_ROLE:-?}' failed (exit $EC)" \
        --hint="Check $PG_LOG_FILE. Stop commands should be idempotent (use '|| true' when pattern may not match)." \
        --related-log="$PG_LOG_FILE" \
        --agent-recoverable=true
fi
