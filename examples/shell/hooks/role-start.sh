#!/usr/bin/env bash
# pg-skills template: role lifecycle action (start / restart).
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/<role>-start.sh (或 <role>-restart.sh)
#   2. 把 CMD_PLACEHOLDER 替换为实际的启动命令
#   3. chmod +x .pg/hooks/<role>-start.sh
#
# 本模板对应 schema 节点:
#   environments.<env>.roles.<r>.actions.{start, restart}.script
#
# 由 pg-run-hook.py 调起, 注入的 env vars (见 .pg/skills/src/runtime/lib/pg-run-hook.py):
#   PG_ROLE             当前 role 名
#   PG_INSTANCE_NAME    instance 名
#   PG_INSTANCE_HOST    instance host
#   PG_HOOK_TYPE        hook 类型 (start / stop / restart / logs / tail ...)
#   PG_CHANGE_NAME      当前 change 名 (可选)
#   PG_STAGE            当前 stage 名
#   PG_ENV              当前 environment 名
#   PG_LOG_FILE         标准输出 / 错误重定向目标
#   PG_RESULT_FILE      hook 退出前写 result.json 的路径
#   PG_SKILLS_PATH      pg-skills 仓库根
#
# 注意: 不要在本 hook 里 cd "$PG_MODULE_ROOT" —— module 维度的命令不进 hook,
# 本 hook 服务的是 environments 维度. 命令应直接作用于 role 实例 (例如启动后台进程、
# 连 SSH 到 instance 等).

set -euo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PG_SKILLS_PATH="${PG_SKILLS_PATH:-$SELF_DIR}"
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

# ---- TODO: 替换为本 role 的启动命令 ----
# 例 (Java 后端): ./run-kuboard-server.sh
# 例 (前端): pnpm --dir kb-portal dev
# 例 (数据库): docker compose up -d db
# 占位符故意保留非空字符串, 防止未替换就运行.
CMD_PLACEHOLDER="echo REPLACE_ME_WITH_START_COMMAND"

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
        --code=PG-E-1010 \
        --message="${PG_HOOK_TYPE:-start} for role '${PG_ROLE:-?}' failed (exit $EC)" \
        --hint="Check $PG_LOG_FILE. Confirm the start command, port availability, and required deps (DB/cache)." \
        --related-log="$PG_LOG_FILE" \
        --agent-recoverable=true
fi
