#!/usr/bin/env bash
# pg-skills template: role lifecycle action (start / restart).
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/<role>-start.sh
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
#   $PG_INSTANCE_PORT   — 实例声明的端口 (project.yaml instances[].port)
#   $PG_ENV / $PG_STAGE — 当前 env / stage
#
# 注意: 本 hook 的 stdout/stderr 由 caller 通过 $PG_LOG_FILE 控制.
#       lib/common.sh 中的 pg_resolve_paths 仅影响 hook 内部 LOG_DIR/PID_DIR 派生.

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

# === 端口定义 ===
# 对于 role 自身的服务端口，使用 PG_INSTANCE_PORT
# 注意：如果本 role 需要代理到另一个后端服务，不要使用 PG_INSTANCE_PORT
# （PG_INSTANCE_PORT 是本 role 实例的端口，不是后端服务的端口）
PORT="${PG_INSTANCE_PORT:-8080}"

# 清理占用端口 (幂等, 仅当端口被占用时杀进程)
if check_port "$PORT"; then
    echo "端口 $PORT 已被占用，清理中..."
    kill_port "$PORT" "${PG_ROLE:-unknown}"
    sleep 1
fi

# ---- TODO: 替换为本 role 的启动命令 ----
# 模板默认实现: 空 body. 替换为你环境的实际命令, 遵循以下模式:
#
# 1) 直接 exec (无 shell 操作符) → 用 pg_start_bg:
#      if ! pid=$(pg_start_bg "$LOG_DIR/app.log" "$PID_DIR/app.pid" \
#              "KEY=VALUE" ... -- \
#              /path/to/binary --flag "$PORT"); then
#          pg_fail --category=service_start_failure ...
#      fi
#
# 2) 含 shell 操作符 (&&, ||, cd) → 用 pg_run_bash:
#      if ! pid=$(pg_run_bash "$LOG_DIR/app.log" "$PID_DIR/app.pid" \
#              "KEY=VALUE" "PATH=$PATH" -- \
#              "cd /app && npm run start -- --port $PORT"); then
#          pg_fail --category=service_start_failure ...
#      fi
#
# 3) 端口就绪检查 (后台服务启动后):
#      if ! wait_for_port_with_monitor "$PORT" "$PG_ROLE" 60 \
#              "$PID_DIR/${PG_ROLE}.pid" "$LOG_DIR/${PG_ROLE}.log"; then
#          pg_fail --category=service_start_timeout ...
#      fi
#
# 4) HTTP 就绪检查 (依赖 SSOT lib/common.sh 的 wait_for_http):
#      if ! wait_for_http "http://localhost:${PORT}/" "$PG_ROLE" 30 "$LOG_DIR/${PG_ROLE}.log"; then
#          pg_fail --category=service_health_check ...
#      fi
#
# 5) 成功 → pg_exit, 失败 → pg_fail (会 exit 1 并写 result.json).
#    不要直接 exit 1 — 会绕过结构化错误报告.
#
# pg_start_bg 优势: (a) setsid 自动 detach (b) env 走 argv, 无 shell 注入
# (c) PID 文件写入由框架保证 (d) setsid 不可用时降级 nohup+disown.
# pg_run_bash 优势: 自动包装 shell 操作符, 避免 "cd && xx" 等被 exec 错误解析.

# ---- 占位示例 (替换为实际启动逻辑) ----
echo_color "33" "TODO: 替换 role-start.sh 为实际启动命令"

DURATION=$(( $(date +%s) - START_TIME ))
pg_exit --status=pass --duration=$DURATION \
        --metadata="role=\"${PG_ROLE:-}\" instance=\"${PG_INSTANCE_NAME:-}\" port=\"$PORT\""