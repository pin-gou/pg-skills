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
# 注意: stop 命令应当幂等 (第二次跑无副作用).
# 模板默认实现利用 pg_stop_bg 从 PID 文件优雅关停.
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

# ---- TODO: 替换为本 role 的停止命令 ----
# 模板默认实现: 从 PID 文件优雅关停 (用 hook-helpers.sh:pg_stop_bg).
# 替换为你环境的实际命令:
#   例 (Java 后端):     pg_stop_bg "$PID_DIR/backend.pid" "Backend"
#   例 (前端 vite):     pg_stop_bg "$PID_DIR/frontend.pid" "Frontend"
#   例 (docker compose): docker compose down
# stop 必须幂等 — 进程不存在时不要 exit 非零 (pg_stop_bg 已处理).
#
# pg_stop_bg 行为: SIGTERM → 等 grace_seconds (默认 5s) → SIGKILL.
#
# 成功: 用 pg_exit 报告. 失败: 用 pg_fail 报告. 不要直接 exit 1.

echo_color "33" "TODO: 替换 role-stop.sh 为实际停止命令"

DURATION=$(( $(date +%s) - START_TIME ))
pg_exit --status=pass --duration=$DURATION \
        --metadata="role=\"${PG_ROLE:-}\" instance=\"${PG_INSTANCE_NAME:-}\""