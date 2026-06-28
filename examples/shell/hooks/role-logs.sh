#!/usr/bin/env bash
# pg-skills template: role logs action.
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/<role>-logs.sh
#   2. 把下面的 TODO 块替换为实际的日志抓取命令
#   3. chmod +x .pg/hooks/<role>-logs.sh
#
# 本模板对应 schema 节点:
#   environments.<env>.roles.<r>.actions.logs.script
#
# 由 pg-run-hook.py 调起 (PG_HOOK_TYPE=logs), 注入 env vars 见 SSOT:
#   .pg/skills/src/runtime/spec/hook-env-vars.yaml
#
# 注意: logs 命令通常是只读快照 (一次性 dump), 不是 tail. 如果要 tail 流,
# 复制本文件改名 role-tail.sh 并把 PG_HOOK_TYPE 检测加进模板.
#
# --tail-lines 参数传递 (LLM ↔ runner invoke-hook):
#   当 LLM 调用 `runner invoke-hook --action logs --tail-lines N` 时,
#   runner 会把 `--tail-lines N` 作为 args 末尾追加到本脚本的 $@ 末尾。
#   在 CMD 中用 `$@` 读取:
#     tail -n "${1:-100}" "$LOG_DIR/${PG_ROLE}.log" "$@"
#   这样 LLM 显式传的 --tail-lines N 会直接传给底层命令 (如 tail 的 -n)。
#   如果 LLM 不传 --tail-lines, $1 为空 — 需要 CMD 自己处理默认值。
#   同样的约定适用于 actions.tail。
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

# ---- TODO: 替换为你的日志抓取命令 ----
# 模板默认实现: 从 $LOG_DIR (pg_resolve_paths 派生) 读取 role 对应的日志尾部.
# 替换为你环境的实际命令:
#   例 (Spring Boot 应用): journalctl -u my-app --no-pager -n "${1:-100}"
#   例 (Docker):           docker logs --tail "${1:-100}" <container>
#   例 (vite dev):         tail -n "${1:-100}" .pg/logs/vite.log
#   例 (远端 SSH):         ssh user@host 'journalctl -u my-app --no-pager -n "${1:-100}"'
# $1 由 runner 注入 LLM 传的 --tail-lines N (缺省 100).
lines="${1:-100}"
tail -n "$lines" "$LOG_DIR/${PG_ROLE:-unknown}.log"

START=$(date +%s)
DURATION=$(($(date +%s) - START))
pg_exit --status=pass --duration=$DURATION \
        --metadata="role=\"${PG_ROLE:-}\" lines=\"$lines\""