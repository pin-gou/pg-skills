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
# 由 pg-run-hook.py 调起, 注入的 env vars (见 .pg/skills/src/runtime/lib/pg-run-hook.py):
#   PG_RUN_CALLER       调用方身份 (pg-build / pg-regression / pg-fix-issue / ad-hoc)
#   PG_RUN_SESSION      session 名 (与 caller 正交, e.g. 提案名 / auto-<date>-<pid>)
#   PG_ROLE             当前 role 名
#   PG_INSTANCE_NAME    instance 名
#   PG_INSTANCE_HOST    instance host
#   PG_HOOK_TYPE        hook 类型 (start / stop / restart / logs / tail ...)
#   PG_CHANGE_NAME      DEPRECATED alias of PG_RUN_SESSION (1 版本兼容)
#   PG_SKILL_NAME       DEPRECATED alias of PG_RUN_CALLER (1 版本兼容)
#   PG_STAGE            当前 stage 名
#   PG_ENV              当前 environment 名
#   PG_LOG_FILE         标准输出 / 错误重定向目标
#   PG_RESULT_FILE      hook 退出前写 result.json 的路径
#   PG_HOOK_LOG_DIR     pg-invoke-hook.py 预拼的日志绝对目录 (lib/common.sh:pg_resolve_paths 优先信任)
#   PG_SKILLS_PATH      pg-skills 仓库根
#
# 注意: 不要在本 hook 里 cd "$PG_MODULE_ROOT" —— module 维度的命令不进 hook,
# 本 hook 服务的是 environments 维度. 命令应直接作用于 role 实例 (例如启动后台进程、
# 连 SSH 到 instance 等).
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

mkdir -p "$LOG_DIR" "$PID_DIR"

# ---- TODO: 替换为本 role 的启动命令 ----
# 模板默认实现: 空 body (启动成功的典型场景由 hook-helpers.sh trap 接管错误).
# 替换为你环境的实际命令:
#   例 (Java 后端 mvn):    mvn spring-boot:run -pl webvirt-bootstrap
#   例 (前端 vite dev):    pnpm --dir webvirt-frontend dev
#   例 (数据库 docker):    docker compose up -d postgres
#   例 (远端 systemd):     ssh user@host 'systemctl start my-app'
# 把 PID 写到 $PID_DIR/${PG_ROLE}.pid (供 stop / health 复用),
# 把 stdout/stderr 重定向到 $LOG_DIR/${PG_ROLE}.log.
#
# 启动后建议: wait_for_port_with_monitor $PORT "$PG_ROLE" 60 \
#     "$PID_DIR/${PG_ROLE}.pid" "$LOG_DIR/${PG_ROLE}.log"
# 来确认端口就绪 + 进程存活 (lib/common.sh 已 source).

START=$(date +%s)
DURATION=$(($(date +%s) - START))
pg_exit --status=pass --duration=$DURATION \
        --metadata="role=\"${PG_ROLE:-}\" instance=\"${PG_INSTANCE_NAME:-}\""