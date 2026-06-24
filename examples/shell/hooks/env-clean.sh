#!/usr/bin/env bash
# pg-skills template: environment clean_env action.
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/clean_env.sh (或 env-<env>-clean.sh)
#   2. 把 CMD_PLACEHOLDER 替换为实际的环境清理命令
#   3. chmod +x
#
# 本模板对应 schema 节点:
#   environments.<env>.clean_env.script
#
# 由 pg-run-hook.py 在 stage 结束时调起 (PG_HOOK_TYPE=clean).
# 与 prepare_env.sh 配对, 用于收回资源 (停 db / 清临时数据 / 卸容器).
#
# 注意: clean_env 命令应当幂等; 跑两次不应当报错.

set -euo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PG_SKILLS_PATH="${PG_SKILLS_PATH:-$SELF_DIR}"
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

# ---- TODO: 替换为环境清理命令 ----
# 例: cd db/db-mariadb && docker compose down -v
# 例: pkill -f 'kuboard-server' || true; rm -rf .pg/runs/<change>/temp
CMD_PLACEHOLDER="echo REPLACE_ME_WITH_CLEAN_ENV_COMMAND"

START=$(date +%s)
if bash -c "$CMD_PLACEHOLDER" > "$PG_LOG_FILE" 2>&1; then
    DURATION=$(($(date +%s) - START))
    pg_exit --status=pass --duration=$DURATION \
            --metadata="cmd=\"$CMD_PLACEHOLDER\" env=\"${PG_ENV:-}\" stage=\"${PG_STAGE:-}\""
else
    EC=$?
    DURATION=$(($(date +%s) - START))
    pg_fail \
        --category=health_check_fail \
        --code=PG-E-1021 \
        --message="clean_env for '${PG_ENV:-?}' failed (exit $EC)" \
        --hint="Check $PG_LOG_FILE. Clean commands should be idempotent — prefer '|| true' for non-essential steps." \
        --related-log="$PG_LOG_FILE" \
        --agent-recoverable=true
fi
