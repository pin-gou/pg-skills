#!/usr/bin/env bash
# pg-skills hooks 公共工具 (SSOT — Single Source of Truth)
#
# 用途:
#   - 由 pg-init-project 复制到新项目的 .pg/hooks/lib/common.sh
#   - 现有项目可 `cp` 此文件覆盖 .pg/hooks/lib/common.sh 来同步上游改动
#   - 角色/环境 hook (role-*.sh / env-*.sh) 通过 source 此文件获得
#     pg_resolve_paths (caller × session 维度路由) 与 kill_port / wait_for_port 等工具
#
# SSOT 规则:
#   - 本文件是 hook 协议的一部分, 不要改 PG_* env var 名
#   - pg_resolve_paths 的路由表与 .pg/skills/src/runtime/bin/pg-invoke-hook.py:pg_log_dir_for_skill
#     三处必须保持同步
#   - 改动本文件前先看上述两处是否需要同步更新
#   - **优先信任 PG_HOOK_LOG_DIR** (由 pg-invoke-hook.py 预拼的绝对路径); 自拼逻辑作为
#     老式手工调用 (不走 pg-invoke-hook.py) 的兜底
#
# v5 协议 (current):
#   - PG_RUN_CALLER / PG_RUN_SESSION 为唯一字段名.
#   - PG_SKILL_NAME / PG_CHANGE_NAME 已删除 (v4 的 1 版本 alias 已退役).
#   - 删除 caller="" (空) 分支的 silent fallback — 改用 caller=ad-hoc 显式声明.
#   - unknown caller → fail-fast (不再降级到 scripts/logs).
#
# 调用方:
#   - 模板 hook (role-start.sh / role-stop.sh / role-logs.sh / env-prepare.sh / env-clean.sh)
#     头部条件 source 本文件: `if [[ -f "$SELF_DIR/lib/common.sh" ]]; then source ...; pg_resolve_paths; fi`
#   - 现有项目 hook (.pg/hooks/role-*.sh / env-*.sh) 同步 source 本文件

# === 端口常量 ===
BACKEND_PORT=9080
FRONTEND_PORT=3008
AGENT_PORT=9082

# === 路径解析：优先使用 PG_HOOK_LOG_DIR，fallback 自拼 ===
#
# 优先：直接信任 pg-invoke-hook.py 预拼的 PG_HOOK_LOG_DIR（权威路径）
# Fallback（老式手工调用 / 未走 pg-invoke-hook.py）：
#   路由规则（与 .pg/skills/src/runtime/bin/pg-invoke-hook.py:pg_log_dir_for_skill 同步）：
#     pg-build       -> .pg/changes/<session>/2-build/<env>/logs|pids
#     pg-regression  -> .pg/regression/<session>/<env>/logs|pids   (session = <suite>-<date>-<seq>)
#     pg-fix-issue   -> .pg/fix-issue/<session>/<env>/logs|pids    (session 含 fix- 前缀)
#     ad-hoc         -> .pg/ad-hoc/<session>/<env>/logs|pids       (新顶级目录, 不与 SKILL 命名空间混)
#
# 调用方必须在 source 此文件前 export:
#   - PG_HOOK_LOG_DIR  (由 pg-run-hook.py 从 spec.hook_log_dir 注入, 推荐)
#   - PG_RUN_CALLER / PG_RUN_SESSION / PG_ENV (v5 协议核心字段)
pg_resolve_paths() {
    local project_root="${PG_PROJECT_ROOT:-$PWD}"

    # === 优先路径：信任 PG_HOOK_LOG_DIR ===
    if [[ -n "${PG_HOOK_LOG_DIR:-}" ]]; then
        LOG_DIR="$PG_HOOK_LOG_DIR"
        PID_DIR="$LOG_DIR"   # logs 与 pids 同目录 (PG_HOOK_LOG_DIR 指向 logs/)
    else
        # === 兜底路径：自拼 (老式手工调用 / 未走 pg-invoke-hook.py) ===
        local caller="${PG_RUN_CALLER:-ad-hoc}"
        local session="${PG_RUN_SESSION:-}"
        local env="${PG_ENV:-unknown}"

        case "$caller" in
            pg-build)
                [[ -z "$session" ]] && session="manual"
                LOG_DIR="$project_root/.pg/changes/${session}/2-build/${env}/logs"
                PID_DIR="$project_root/.pg/changes/${session}/2-build/${env}/pids"
                ;;
            pg-regression)
                LOG_DIR="$project_root/.pg/regression/${session}/${env}/logs"
                PID_DIR="$project_root/.pg/regression/${session}/${env}/pids"
                ;;
            pg-fix-issue)
                LOG_DIR="$project_root/.pg/fix-issue/${session}/${env}/logs"
                PID_DIR="$project_root/.pg/fix-issue/${session}/${env}/pids"
                ;;
            ad-hoc)
                [[ -z "$session" ]] && session="ad-hoc-unknown"
                LOG_DIR="$project_root/.pg/ad-hoc/${session}/${env}/logs"
                PID_DIR="$project_root/.pg/ad-hoc/${session}/${env}/pids"
                ;;
            *)
                echo_color "31" "ERROR: unknown PG_RUN_CALLER='$caller'" >&2
                return 1
                ;;
        esac
    fi

    mkdir -p "$LOG_DIR" "$PID_DIR"
    BACKEND_LOG="$LOG_DIR/backend.log"
    FRONTEND_LOG="$LOG_DIR/frontend.log"
}

# === echo_color: 彩色输出 ===
echo_color() {
    local color=$1
    shift
    echo -e "\033[${color}m$*\033[0m"
}

# === check_port: 检测端口是否占用 ===
check_port() {
    local port=$1
    if command -v netstat >/dev/null 2>&1; then
        netstat -tuln 2>/dev/null | grep -qE "(:$port |:$port$)"
    elif command -v ss >/dev/null 2>&1; then
        ss -tuln 2>/dev/null | grep -qE "(:$port |:$port$)"
    else
        nc -z localhost "$port" 2>/dev/null
    fi
}

# === kill_port: 强制杀掉占用某端口的进程 ===
kill_port() {
    local port=$1
    local name=$2
    if command -v netstat >/dev/null 2>&1; then
        local pid
        pid=$(netstat -tulnp 2>/dev/null | grep ":$port " | grep -oP '\d+(?=/)')
        if [ -n "$pid" ]; then
            echo_color "33" "Killing $name process on port $port (PID: $pid)"
            kill -9 "$pid" >/dev/null 2>&1 || true
            sleep 1
        fi
    elif command -v ss >/dev/null 2>&1; then
        local pid
        pid=$(ss -tulnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+')
        if [ -n "$pid" ]; then
            echo_color "33" "Killing $name process on port $port (PID: $pid)"
            kill -9 "$pid" >/dev/null 2>&1 || true
            sleep 1
        fi
    fi
}

# === wait_for_port: 等待端口就绪（最多 max_wait 秒） ===
wait_for_port() {
    local port=$1
    local name=$2
    local max_wait=${3:-60}
    local count=0
    echo "Waiting for $name to be ready on port $port..."
    while ! check_port "$port"; do
        sleep 1
        count=$((count + 1))
        if [ $count -ge "$max_wait" ]; then
            echo_color "31" "ERROR: $name not ready after ${max_wait}s"
            return 1
        fi
    done
    echo_color "32" "$name is ready on port $port"
}

# === wait_for_port_with_monitor: 等待端口就绪，同时监控进程存活和日志错误 ===
# 参数: port name max_wait pid_file log_file
# 相比 wait_for_port 额外做:
#   1. 每轮检测 PID 是否还活着 (kill -0)
#   2. 进程死后立即 dump 日志尾部并退出，不等 timeout
#   3. timeout 时也 dump 日志尾部帮助排查
wait_for_port_with_monitor() {
    local port=$1
    local name=$2
    local max_wait=${3:-60}
    local pid_file=$4
    local log_file=$5
    local count=0
    echo "Waiting for $name to be ready on port $port..."
    while ! check_port "$port"; do
        if [ -n "$pid_file" ] && [ -f "$pid_file" ]; then
            local pid
            pid=$(cat "$pid_file" 2>/dev/null)
            if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
                echo_color "31" "ERROR: $name process (PID $pid) exited prematurely"
                if [ -n "$log_file" ] && [ -f "$log_file" ]; then
                    echo_color "33" "Last 20 lines of $log_file:"
                    tail -20 "$log_file" | sed 's/^/  /'
                fi
                rm -f "$pid_file"
                return 1
            fi
        fi
        sleep 1
        count=$((count + 1))
        if [ $count -ge "$max_wait" ]; then
            echo_color "31" "ERROR: $name not ready after ${max_wait}s"
            if [ -n "$log_file" ] && [ -f "$log_file" ]; then
                echo_color "33" "Last 20 lines of $log_file:"
                tail -20 "$log_file" | sed 's/^/  /'
            fi
            rm -f "$pid_file"
            return 1
        fi
    done
    echo_color "32" "$name is ready on port $port"
}

# === kill_pid_file: 从 PID 文件停止进程 ===
# === kill_pid_file: 已迁移到 hook-helpers.sh:pg_stop_bg ===
# 旧函数已弃用 (v5+). 新代码请直接调 pg_stop_bg (由 hook-helpers.sh 提供).
# 本函数保留为兼容垫片, 转发到 pg_stop_bg 并打 WARN.
kill_pid_file() {
    echo "WARN: kill_pid_file 已弃用, 请改用 pg_stop_bg (hook-helpers.sh)" >&2
    pg_stop_bg "$@"
}
