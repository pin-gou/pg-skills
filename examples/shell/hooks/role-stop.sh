#!/usr/bin/env bash
# pg-skills template: role stop action.
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/<role>-stop.sh
#   2. 把下面的 TODO 块替换为实际的停止命令
#   3. chmod +x .pg/hooks/<role>-stop.sh
#
# 本模板对应 schema 节点:
#   environments.<env>.roles.<r>.actions.stop.script
#
# 由 pg-run-hook.py 调起, 注入 env vars 见 SSOT:
#   .pg/skills/src/runtime/spec/hook-env-vars.yaml
#
# 注意: stop 命令应当幂等 (第二次跑无副作用). 常见实现:
#   pkill -f <process-pattern> || true
#   docker compose down
#   ssh user@host 'systemctl stop <svc>'
# 模板默认实现利用 lib/common.sh:kill_pid_file (已 source) 从 PID 文件优雅关停.
#
# 注意: 本 hook 的 stdout/stderr 由 caller 通过 $PG_LOG_FILE 控制.
#       lib/common.sh 中的 pg_resolve_paths 仅影响 hook 内部 LOG_DIR/PID_DIR 派生.

set -uo pipefail  # 注意: 不加 -e, 由 hook-helpers.sh trap ERR 控制
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PG_SKILLS_PATH="${PG_SKILLS_PATH:-$SELF_DIR}"
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
trap 'pg_fail_on_error $? $LINENO' ERR

# === 路径派生 (per-skill 路由, 由 pg_resolve_paths 决定) ===
# 若 .pg/hooks/lib/common.sh 存在, 调 pg_resolve_paths 把 LOG_DIR/PID_DIR
# 按 PG_RUN_CALLER 路由到 .pg/changes/ / .pg/regression/ / .pg/fix-issue/ / .pg/ad-hoc/
# 若 lib/ 缺失 (eg. 手工复制本模板但没带 lib/), 跳过派生, 输出走 $PG_LOG_FILE
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$HOOK_DIR/lib/common.sh" ]]; then
    source "$HOOK_DIR/lib/common.sh"
    pg_resolve_paths
fi

# ---- TODO: 替换为本 role 的停止命令 ----
# 模板默认实现: 从 PID 文件优雅关停 (用 hook-helpers.sh:pg_stop_bg, 已 source).
# 替换为你环境的实际命令:
#   例 (Java 后端):     pg_stop_bg "$PID_DIR/backend.pid" "Backend"
#   例 (前端 vite):     pg_stop_bg "$PID_DIR/frontend.pid" "Frontend"
#   例 (docker compose): docker compose down
#   例 (远端 systemd):  ssh user@host 'systemctl stop my-app'
# stop 必须幂等 — 进程不存在时不要 exit 非零 (pg_stop_bg 已处理).
#
# pg_stop_bg 行为: SIGTERM → 等 grace_seconds (默认 5s) → SIGKILL.
# 取代 lib/common.sh:kill_pid_file (已弃用).
#
# 错误处理: 如果停服失败, 用 pg_fail 结构化报告:
#   例 (docker):
#     if ! docker compose down; then
#         pg_fail --category=service_stop_failure --code=PG-E-0930 \
#             --message="停止服务失败" \
#             --hint="Check docker status" \
#             --agent-recoverable=true
#     fi
#
# 成功: 用 pg_exit 报告.
# 失败: 用 pg_fail 报告 (会 exit 1 并写结构化 result.json).
# 不要直接 exit 1.

# ---- 占位示例 (替换为实际停止命令) ----
# pg_stop_bg "$PID_DIR/backend.pid" "Backend" 2>&1 || \
#     pg_fail --category=service_stop_failure --code=PG-E-0930 \
#         --message="停止 Backend 失败" \
#         --agent-recoverable=true
echo_color "33" "TODO: 替换 role-stop.sh 为实际停止命令"

START=$(date +%s)
DURATION=$(($(date +%s) - START))
pg_exit --status=pass --duration=$DURATION \
        --metadata="role=\"${PG_ROLE:-}\" instance=\"${PG_INSTANCE_NAME:-}\""