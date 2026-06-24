#!/usr/bin/env bash
# pg-skills template: environment prepare_env action.
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/prepare_env.sh (或 env-<env>-prepare.sh)
#   2. 把 CMD_PLACEHOLDER 替换为实际的环境准备命令
#   3. chmod +x
#
# 本模板对应 schema 节点:
#   environments.<env>.prepare_env.script
#
# 由 pg-run-hook.py 在 stage 开始时调起 (PG_HOOK_TYPE=prepare),
# 注入 env vars 见 role-start.sh 头部 (PG_ROLE 等为环境级时未注入).
#
# 用途: 启 db / 跑 migration / 启 cache / 预热数据 —— 一次性准备工作,
# 跑完 stage 后由 clean_env.sh 收回.

set -euo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PG_SKILLS_PATH="${PG_SKILLS_PATH:-$SELF_DIR}"
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

# ---- TODO: 替换为环境准备命令 ----
# 例 (启动 MariaDB + 跑 flyway): cd db/db-mariadb && docker compose up -d && sleep 5 && mvn -pl kuboard-server flyway:migrate
# 例 (只跑 migration): mvn -pl kuboard-server flyway:migrate
CMD_PLACEHOLDER="echo REPLACE_ME_WITH_PREPARE_ENV_COMMAND"

START=$(date +%s)
if bash -c "$CMD_PLACEHOLDER" > "$PG_LOG_FILE" 2>&1; then
    DURATION=$(($(date +%s) - START))
    pg_exit --status=pass --duration=$DURATION \
            --metadata="cmd=\"$CMD_PLACEHOLDER\" env=\"${PG_ENV:-}\" stage=\"${PG_STAGE:-}\""
else
    EC=$?
    DURATION=$(($(date +%s) - START))
    pg_fail \
        --category=dependency_not_ready \
        --code=PG-E-1020 \
        --message="prepare_env for '${PG_ENV:-?}' failed (exit $EC)" \
        --hint="Check $PG_LOG_FILE. Common cause: DB not up yet, migration ran twice, ports already in use." \
        --related-log="$PG_LOG_FILE" \
        --agent-recoverable=true
fi
