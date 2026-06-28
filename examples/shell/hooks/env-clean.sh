#!/usr/bin/env bash
# pg-skills template: environment clean_env action.
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/clean_env.sh (或 env-<env>-clean.sh)
#   2. 把下面的 TODO 块替换为实际的环境清理命令
#   3. chmod +x
#
# 本模板对应 schema 节点:
#   environments.<env>.clean_env.script
#
# 由 pg-run-hook.py 在 stage 结束时调起 (PG_HOOK_TYPE=clean).
# 与 prepare_env.sh 配对, 用于收回资源 (停 db / 清临时数据 / 卸容器).
# 注入 env vars 见 SSOT: .pg/skills/src/runtime/spec/hook-env-vars.yaml
#
# 注意: clean_env 命令应当幂等; 跑两次不应当报错.
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

# ---- TODO: 替换为环境清理命令 ----
# 模板默认实现: 空 body. 替换为你环境的实际命令:
#   例 (停 docker compose): cd db/db-mariadb && docker compose down -v
#   例 (清临时数据):       rm -rf .pg/runs/<change>/temp
#   例 (组合):             pkill -f 'webvirt-bootstrap' || true \
#                             && docker compose down -v
# clean_env 必须幂等; 进程/容器不存在时不要 exit 非零 (用 '|| true').
#
# 错误处理: 如果清理命令失败, 用 pg_fail 结构化报告:
#   例:
#     if ! docker compose down -v; then
#         pg_fail --category=health_check_fail --code=PG-E-1021 \
#             --message="清理环境失败" \
#             --hint="Check docker status" \
#             --agent-recoverable=true
#     fi
#
# 成功: 用 pg_exit 报告.
# 失败: 用 pg_fail 报告 (会 exit 1 并写结构化 result.json).
# 不要直接 exit 1 — 会绕过结构化错误报告.

clean_cmd=""
# clean_cmd="cd db/db-mariadb && docker compose down -v"

if [ -n "$clean_cmd" ]; then
    echo "Running: $clean_cmd"
    if ! bash -c "$clean_cmd"; then
        pg_fail \
            --category=health_check_fail \
            --code=PG-E-1021 \
            --message="环境清理失败" \
            --hint="Check clean_env output above" \
            --agent-recoverable=true
    fi
    echo_color "32" "环境清理完成"
fi

START=$(date +%s)
DURATION=$(($(date +%s) - START))
pg_exit --status=pass --duration=$DURATION \
        --metadata="env=\"${PG_ENV:-}\" stage=\"${PG_STAGE:-}\""