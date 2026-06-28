#!/usr/bin/env bash
# pg-skills template: environment prepare_env action.
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/prepare_env.sh (或 env-<env>-prepare.sh)
#   2. 把下面的 TODO 块替换为实际的环境准备命令
#   3. chmod +x
#
# 本模板对应 schema 节点:
#   environments.<env>.prepare_env.script
#
# 由 pg-run-hook.py 在 stage 开始时调起 (PG_HOOK_TYPE=prepare),
# 注入 env vars 见 SSOT: .pg/skills/src/runtime/spec/hook-env-vars.yaml
# (PG_RUN_CALLER / PG_RUN_SESSION 为环境级时仍注入;
#  PG_ROLE / PG_INSTANCE_* 为环境级时未注入).
#
# 用途: 启 db / 跑 migration / 启 cache / 预热数据 —— 一次性准备工作,
# 跑完 stage 后由 clean_env.sh 收回.
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

# ---- TODO: 替换为环境准备命令 ----
# 模板默认实现: 空 body. 替换为你环境的实际命令:
#   例 (启动 MariaDB + 跑 flyway): cd db/db-mariadb && docker compose up -d \
#         && sleep 5 && mvn -pl webvirt-bootstrap flyway:migrate
#   例 (只跑 migration): mvn -pl webvirt-bootstrap flyway:migrate
#   例 (启 redis + 预热): docker compose up -d redis && redis-cli FLUSHALL
# 失败上报: 调用 `pg_fail --code=PG-E-1020` (dependency_not_ready).
# 详细字段: --category / --code / --message / --hint / --related-log / --agent-recoverable.

START=$(date +%s)
DURATION=$(($(date +%s) - START))
pg_exit --status=pass --duration=$DURATION \
        --metadata="env=\"${PG_ENV:-}\" stage=\"${PG_STAGE:-}\""