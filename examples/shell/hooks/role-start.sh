#!/usr/bin/env bash
# pg-skills template: role lifecycle action (start / restart).
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/<role>-start.sh (或 <role>-restart.sh)
#   2. 把下面的 TODO 块替换为实际的启动命令
#   3. chmod +x .pg/hooks/<role>-start.sh
#
# 本模板对应 schema 节点:
#   environments.<env>.roles.<r>.actions.{start, restart}.script
#
# 由 pg-run-hook.py 调起, 注入的 env vars 见 SSOT:
#   .pg/skills/src/runtime/spec/hook-env-vars.yaml
# 本模板最常用:
#   $PG_SKILLS_PATH     — pg-skills 根 (source hook-helpers.sh)
#   $PG_HOOK_LOG_DIR    — 预拼日志绝对目录 (lib/common.sh:pg_resolve_paths 优先)
#   $PG_LOG_FILE        — stdout/stderr 目标 (caller 注入)
#   $PG_RESULT_FILE     — 写 result.json 路径
#   $PG_RUN_CALLER      — caller 身份 (pg-build / pg-regression / pg-fix-issue / ad-hoc)
#   $PG_RUN_SESSION     — session 名 (与 caller 正交)
#   $PG_ROLE / $PG_INSTANCE_NAME — per-role 维度
#   $PG_ENV / $PG_STAGE — 当前 env / stage
#
# 注意: 本 hook 的 stdout/stderr 由 caller 通过 $PG_LOG_FILE 控制.
#       lib/common.sh 中的 pg_resolve_paths 仅影响 hook 内部 LOG_DIR/PID_DIR 派生.

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

# ---- TODO: 替换为本 role 的启动命令 ----
# 模板默认实现: 空 body (下面 t0/t1/pg_exit 仅做占位).
# 替换为你环境的实际命令, 遵循以下模式:
#
# 1) 用 pg_start_bg 替代 setsid+redirect+PID 写入模板:
#      if ! pid=$(pg_start_bg "$LOG_DIR/backend.log" "$PID_DIR/backend.pid" \
#              "KEY=VALUE" ... -- \
#              mvn spring-boot:run ...); then
#          pg_fail --category=service_start_failure ...
#      fi
#
# 2) 端口就绪检查 (后台服务启动后):
#      if ! wait_for_port_with_monitor $PORT "$PG_ROLE" 60 \
#              "$PID_DIR/${PG_ROLE}.pid" "$LOG_DIR/${PG_ROLE}.log"; then
#          pg_fail --category=service_start_timeout ...
#      fi
#
# 3) HTTP 就绪检查 (依赖 SSOT lib/common.sh 的 wait_for_http):
#      if ! wait_for_http "http://localhost:${PORT}/" "$PG_ROLE" 30 "$LOG_DIR/${PG_ROLE}.log"; then
#          pg_fail --category=service_health_check ...
#      fi
#
# 4) 成功 → pg_exit, 失败 → pg_fail (会 exit 1 并写 result.json).
#    不要直接 exit 1 — 会绕过结构化错误报告.
#
# pg_start_bg 优势: (a) setsid 自动 detach (b) env 走 argv, 无 shell 注入
# (c) PID 文件写入由框架保证 (d) setsid 不可用时降级 nohup+disown.
#
# 注意: invoke-hook CLI 对 start action 默认 wait_for_completion=true.
# 如需 fire-and-forget, 在 project.yaml 加 wait_for_completion: false
# 或传 --no-wait-for-bg.

# ---- 占位示例 (替换为实际启动逻辑) ----
t0=$(date +%s)
# --- 在此处插入 pg_start_bg + wait_for_port + wait_for_http ---
t1=$(date +%s)
echo_color "33" "TODO: 替换 role-start.sh 为实际启动命令"
echo "  elapsed: $(($t1 - $t0))s"

START=$(date +%s)
DURATION=$(($(date +%s) - START))
pg_exit --status=pass --duration=$DURATION \
        --metadata="role=\"${PG_ROLE:-}\" instance=\"${PG_INSTANCE_NAME:-}\""