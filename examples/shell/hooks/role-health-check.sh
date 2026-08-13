#!/usr/bin/env bash
# pg-skills template: role-health-check.sh (per-role)
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/<role>-health-check.sh
#   2. 按 role 类型填入探针路径 / 端口 (见下方的 role 分发)
#   3. chmod +x .pg/hooks/<role>-health-check.sh
#
# 本模板对应 schema 节点:
#   environments.<env>.roles.<r>.actions.health_check.script
#
# 由 pg-run-hook.py 调起, 注入的 env vars 见 SSOT:
#   .pg/skills/src/runtime/spec/hook-env-vars.yaml
# 本模板最常用:
#   $PG_SKILLS_PATH     — pg-skills 根 (source hook-helpers.sh)
#   $PG_HOOK_LOG_DIR    — 预拼日志绝对目录 (lib/common.sh:pg_resolve_paths 优先)
#   $PG_LOG_FILE        — stdout/stderr 目标 (caller 注入)
#   $PG_RESULT_FILE     — 写 result.json 路径
#   $PG_RUN_CALLER      — caller 身份
#   $PG_ROLE / $PG_INSTANCE_NAME — per-role 维度
#   $PG_INSTANCE_HOST   — instance 所在 host
#   $PG_INSTANCE_PORT   — 实例声明的端口 (project.yaml instances[].port)
#   $PG_ENV / $PG_STAGE — 当前 env / stage
#
# 行为:
#   按 $PG_ROLE 分发到对应探针, 端口优先使用 $PG_INSTANCE_PORT
#   (若未注入则 fallback 到各 role 的常量默认值).
#
# 退出码:
#   0  → 探针成功 (result.json status="pass")
#   ≠0 → 探针失败 (result.json status="fail", category=service_health_check)

set -uo pipefail  # 注意: 不加 -e, 由 hook-helpers.sh trap ERR 控制
PROJECT_ROOT="${PG_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export PG_SKILLS_PATH="${PG_SKILLS_PATH:-$PROJECT_ROOT/.pg/skills}"
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

# === 路径派生 (per-skill 路由, 由 pg_resolve_paths 决定) ===
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$HOOK_DIR/../lib/common.sh" ]]; then
    source "$HOOK_DIR/../lib/common.sh"
    pg_resolve_paths
fi
START_TIME=$(date +%s)
mkdir -p "$LOG_DIR" "$PID_DIR"

# === 探针分发 ===
ROLE="${PG_ROLE:-}"
INSTANCE="${PG_INSTANCE_NAME:-}"
HOST="${PG_INSTANCE_HOST:-localhost}"
PORT="${PG_INSTANCE_PORT:-}"

case "$ROLE" in
    backend)
        pg_http_health_check "$ROLE" "$INSTANCE" "$HOST" "${PORT:-${BACKEND_PORT:-9080}}" "/actuator/health" \
            || pg_fail --category=service_health_check --code=PG-E-0902 \
                       --message="backend health check failed at ${HOST}:${PORT:-${BACKEND_PORT:-9080}}/actuator/health" \
                       --hint="Check backend logs at $LOG_DIR/backend.log"
        ;;
    frontend)
        pg_http_health_check "$ROLE" "$INSTANCE" "$HOST" "${PORT:-${FRONTEND_PORT:-3008}}" "/" \
            || pg_fail --category=service_health_check --code=PG-E-0903 \
                       --message="frontend health check failed at ${HOST}:${PORT:-${FRONTEND_PORT:-3008}}/" \
                       --hint="Check frontend logs at $LOG_DIR/frontend.log"
        ;;
    agent)
        pg_tcp_health_check "$ROLE" "$INSTANCE" "$HOST" "${PORT:-${AGENT_PORT:-9082}}" \
            || pg_fail --category=service_health_check --code=PG-E-0904 \
                       --message="agent port not ready at ${HOST}:${PORT:-${AGENT_PORT:-9082}}" \
                       --hint="Check agent logs at $LOG_DIR/agent.log"
        ;;
    *)
        pg_fail --category=service_health_check --code=PG-E-0905 \
                --message="health-check: unknown role: $ROLE" \
                --hint="Add a case branch in role-health-check.sh for role '$ROLE'"
        ;;
esac

DURATION=$(( $(date +%s) - START_TIME ))
pg_exit --status=pass --duration=$DURATION \
        --metadata="role=\"$ROLE\" instance=\"$INSTANCE\" host=\"$HOST\" port=\"$PORT\""