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

# ---- TODO: 替换为环境清理命令 ----
# 模板默认实现: 空 body. 替换为你环境的实际命令:
#   例 (停 docker compose): cd db/db-mariadb && docker compose down -v
#   例 (清临时数据):       rm -rf .pg/runs/<change>/temp
# clean_env 必须幂等; 进程/容器不存在时不要 exit 非零 (用 '|| true').

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

DURATION=$(( $(date +%s) - START_TIME ))
pg_exit --status=pass --duration=$DURATION \
        --metadata="env=\"${PG_ENV:-}\" stage=\"${PG_STAGE:-}\""