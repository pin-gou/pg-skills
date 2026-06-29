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
#   $PG_RUN_CALLER      — caller 身份 (pg-build / pg-regression / pg-fix-issue / pg-agent / ad-hoc)
#   $PG_RUN_SESSION     — session 名 (与 caller 正交)
#   $PG_ROLE / $PG_INSTANCE_NAME — per-role 维度
#   $PG_INSTANCE_HOST   — instance 所在 host (gRPC / HTTP 探针的目标)
#   $PG_ENV / $PG_STAGE — 当前 env / stage
#
# 行为:
#   按 $PG_ROLE 分发到对应探针:
#     - backend  → HTTP GET ${PG_INSTANCE_HOST}:${backend_port}/actuator/health
#     - frontend → HTTP GET ${PG_INSTANCE_HOST}:${frontend_port}/
#     - agent    → TCP check ${PG_INSTANCE_HOST}:${agent_port}
#     - 其它 role → 默认 TCP check ${PG_INSTANCE_HOST}:${custom_port} (需在
#                  pg-init-project 复制时手动改)
#   探针函数由 hook-helpers.sh 提供, pg-init-project 复制时一起 source.
#
# 退出码:
#   0  → 探针成功 (exit_code=0 → result.json status="pass")
#   ≠0 → 探针失败 (exit_code≠0 → result.json status="fail", category=service_health_check)

set -uo pipefail  # 注意: 不加 -e, 由 hook-helpers.sh trap ERR 控制
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PG_SKILLS_PATH="${PG_SKILLS_PATH:-$SELF_DIR}"
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

# === 路径派生 (per-skill 路由, 由 pg_resolve_paths 决定) ===
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$HOOK_DIR/lib/common.sh" ]]; then
    source "$HOOK_DIR/lib/common.sh"
    pg_resolve_paths
fi

mkdir -p "$LOG_DIR" "$PID_DIR"

# === 探针分发 ===
ROLE="${PG_ROLE:-}"
INSTANCE="${PG_INSTANCE_NAME:-}"
HOST="${PG_INSTANCE_HOST:-localhost}"

START=$(date +%s)

case "$ROLE" in
    backend)
        # backend 默认 Spring Boot 9080 + /actuator/health
        # 实际端口由 .pg/hooks/lib/common.sh 的 BACKEND_PORT 常量提供 (9080),
        # 多 instance 场景下应通过 PG_INSTANCE_PORT 注入, 当前 schema 未提供, 暂用 BACKEND_PORT
        pg_http_health_check "$ROLE" "$INSTANCE" "$HOST" "${BACKEND_PORT:-9080}" "/actuator/health" \
            || pg_fail --category=service_health_check --code=PG-E-0902 \
                       --message="backend health check failed at ${HOST}:${BACKEND_PORT:-9080}/actuator/health" \
                       --hint="Check backend logs at $LOG_DIR/backend.log"
        ;;
    frontend)
        pg_http_health_check "$ROLE" "$INSTANCE" "$HOST" "${FRONTEND_PORT:-3008}" "/" \
            || pg_fail --category=service_health_check --code=PG-E-0903 \
                       --message="frontend health check failed at ${HOST}:${FRONTEND_PORT:-3008}/" \
                       --hint="Check frontend logs at $LOG_DIR/frontend.log"
        ;;
    agent)
        # agent 通常是 gRPC, TCP check 端口就绪即可 (gRPC 探活复杂, 留给业务层)
        pg_tcp_health_check "$ROLE" "$INSTANCE" "$HOST" "${AGENT_PORT:-9082}" \
            || pg_fail --category=service_health_check --code=PG-E-0904 \
                       --message="agent port not ready at ${HOST}:${AGENT_PORT:-9082}" \
                       --hint="Check agent logs at $LOG_DIR/agent.log"
        ;;
    *)
        pg_fail --category=service_health_check --code=PG-E-0905 \
                --message="health-check: unknown role: $ROLE" \
                --hint="Add a case branch in role-health-check.sh for role '$ROLE'"
        ;;
esac

DURATION=$(($(date +%s) - START))
pg_exit --status=pass --duration=$DURATION \
        --metadata="role=\"$ROLE\" instance=\"$INSTANCE\" host=\"$HOST\""