#!/usr/bin/env bash
# pg-skills hooks 公共工具 (SSOT — Single Source of Truth)
#
# 用途:
#   - 由 pg-init-project 复制到新项目的 .pg/hooks/lib/common.sh
#   - 现有项目可 `cp` 此文件覆盖 .pg/hooks/lib/common.sh 来同步上游改动
#   - 角色/环境 hook (role-*.sh / env-*.sh) 通过 source 此文件获得
#     pg_resolve_paths (per-skill 路径路由) 与 kill_port / wait_for_port 等工具
#
# SSOT 规则:
#   - 本文件是 hook 协议的一部分, 不要改 PG_* env var 名
#   - pg_resolve_paths 的路由表 (.pg/changes / .pg/regression / .pg/fix-issue)
#     与 .pg/skills/src/runtime/bin/pg-invoke-hook.py:pg_log_dir_for_skill
#     与 .pg/skills/src/opencode/skills/pg-build/scripts/pg-pipeline-runner.py:_pg_log_dir_for_skill
#     三处必须保持同步
#   - 改动本文件前先看上述三处是否需要同步更新
#
# 调用方:
#   - 模板 hook (role-start.sh / role-stop.sh / role-logs.sh / env-prepare.sh / env-clean.sh)
#     头部条件 source 本文件: `if [[ -f "$SELF_DIR/lib/common.sh" ]]; then source ...; pg_resolve_paths; fi`
#   - 现有项目 hook (.pg/hooks/role-*.sh / env-*.sh) 同步 source 本文件

# === 端口常量 ===
BACKEND_PORT=9080
FRONTEND_PORT=3008
AGENT_PORT=9082

# === 路径解析：根据 PG_SKILL_NAME 自动选择 LOG_DIR/PID_DIR ===
#
# 路由规则（与 .pg/skills/src/runtime/bin/pg-invoke-hook.py:pg_log_dir_for_skill 同步）：
#   pg-build       -> .pg/changes/<change>/2-build/<env>/logs|pids
#   pg-regression  -> .pg/regression/<suite>/<env>/logs|pids   (从 regression-<suite> 截 suite)
#   pg-fix-issue   -> .pg/fix-issue/<change>/<env>/logs|pids   (change = fix-<date>-<slug>)
#   兜底 / unknown -> scripts/logs|pids (兼容手工调用)
#
# 调用方必须在 source 此文件前 export PG_SKILL_NAME (由 pg-run-hook.py 从 spec.skill 注入)
# 以及 PG_CHANGE_NAME / PG_ENV (兼容 PG_ENV_NAME)。
pg_resolve_paths() {
    local project_root="${PG_PROJECT_ROOT:-$PWD}"
    local skill="${PG_SKILL_NAME:-}"
    local change="${PG_CHANGE_NAME:-}"
    local env="${PG_ENV:-${PG_ENV_NAME:-unknown}}"

    case "$skill" in
        pg-build)
            LOG_DIR="$project_root/.pg/changes/${change}/2-build/${env}/logs"
            PID_DIR="$project_root/.pg/changes/${change}/2-build/${env}/pids"
            ;;
        pg-regression)
            local suite="${change#regression-}"
            LOG_DIR="$project_root/.pg/regression/${suite}/${env}/logs"
            PID_DIR="$project_root/.pg/regression/${suite}/${env}/pids"
            ;;
        pg-fix-issue)
            LOG_DIR="$project_root/.pg/fix-issue/${change}/${env}/logs"
            PID_DIR="$project_root/.pg/fix-issue/${change}/${env}/pids"
            ;;
        "")
            LOG_DIR="$project_root/scripts/logs"
            PID_DIR="$project_root/scripts/pids"
            ;;
        *)
            echo_color "33" "WARN: unknown PG_SKILL_NAME='$skill', falling back to scripts/logs" >&2
            LOG_DIR="$project_root/scripts/logs"
            PID_DIR="$project_root/scripts/pids"
            ;;
    esac

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
kill_pid_file() {
    local pid_file=$1
    local name=$2
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "Stopping $name (PID $pid)..."
            kill "$pid" 2>/dev/null || true
            local count=0
            while kill -0 "$pid" 2>/dev/null; do
                sleep 1
                count=$((count + 1))
                if [ $count -ge 5 ]; then
                    echo "Force killing $name (PID $pid)..."
                    kill -9 "$pid" 2>/dev/null || true
                    break
                fi
            done
            if ! kill -0 "$pid" 2>/dev/null; then
                echo_color "32" "$name stopped"
            fi
        else
            echo_color "33" "$name is not running (stale PID file)"
        fi
        rm -f "$pid_file"
    else
        echo_color "33" "$name is not running (no PID file)"
    fi
}
