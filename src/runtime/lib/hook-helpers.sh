#!/usr/bin/env bash
# pg-foundation hook helpers — Phase 2 错误传播契约工具集
#
# Hook 入口加载方式:
#   source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"
#   trap 'pg_fail_on_error $? $LINENO' ERR
#
# 提供:
#   pg_fail <args...>      — 显式报告失败, 写 result.json 后 exit 1
#   pg_exit <args...>      — 显式报告成功, 写 result.json 后 exit 0
#   pg_fail_on_error <ec> <line>  — trap 兜底, 启发式推断 category
#   pg_validate_category <name>   — 校验 category 在枚举中
#   pg_start_bg <log> <pid> [env_kv ...] -- <cmd ...>
#                             — 后台启动命令, setsid detach + 安全 env 注入
#                             + 写 PID 文件. 适合 role-start.sh 等长驻服务.
#   pg_stop_bg <pid_file> <name> [<timeout>]
#                             — 优雅关停 PID 文件指向的进程 (SIGTERM → SIGKILL).
#                             取代 lib/common.sh:kill_pid_file.

set -euo pipefail

# 找到 error-categories.yaml 路径
if [[ -z "${PG_SKILLS_PATH:-}" ]]; then
    echo "WARN: PG_SKILLS_PATH not set, falling back to script location" >&2
    PG_SKILLS_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
PG_ERROR_CATEGORIES="$PG_SKILLS_PATH/src/runtime/spec/error-categories.yaml"

# ----- 参数解析辅助 -----
_pg_parse_kv() {
    # 把 --key=value / --key value 解析为 KEY=VALUE
    local prefix="$1"
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --${prefix}=*) echo "${1#--${prefix}=}"; return 0 ;;
            --${prefix}) echo "$2"; return 0 ;;
        esac
        shift
    done
}

# ----- category 校验 -----
pg_validate_category() {
    local cat="$1"
    if [[ -z "$cat" ]]; then return 1; fi
    # 标准 category (从 yaml 解析)
    if grep -qE "^  ${cat}:" "$PG_ERROR_CATEGORIES" 2>/dev/null; then
        return 0
    fi
    # 允许 <project-prefix>.<sub-category> 形式
    if [[ "$cat" =~ ^[a-z][a-z0-9_-]*\.[a-z_]+$ ]]; then
        return 0
    fi
    return 1
}

# ----- pg-fail -----
pg_fail() {
    local category="" code="" message="" hint=""
    local severity="recoverable" recoverable="false"
    local fix_hook="" fix_args="" retry_after="" max_retries=""
    local related_log=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --category=*)        category="${1#*=}" ;;
            --code=*)            code="${1#*=}" ;;
            --message=*)         message="${1#*=}" ;;
            --hint=*)            hint="${1#*=}" ;;
            --severity=*)        severity="${1#*=}" ;;
            --agent-recoverable=*) recoverable="${1#*=}" ;;
            --fix-hook=*)        fix_hook="${1#*=}" ;;
            --fix-hook-args=*)   fix_args="${1#*=}" ;;
            --retry-after-seconds=*) retry_after="${1#*=}" ;;
            --max-retries=*)     max_retries="${1#*=}" ;;
            --related-log=*)     related_log="${1#*=}" ;;
            *) ;;
        esac
        shift
    done

    # 默认 category 兜底
    if [[ -z "$category" ]]; then
        category="unknown"
    fi
    if ! pg_validate_category "$category"; then
        # 不在枚举 + 不是 <project>.<sub> 形式, 降级到 unknown
        category="unknown"
    fi

    # 写 result.json (无外部依赖, 用 here-doc)
    {
        echo "{"
        echo "  \"status\": \"fail\","
        echo "  \"exit_code\": 1,"
        echo "  \"error\": {"
        echo "    \"category\": \"$category\","
        echo "    \"severity\": \"$severity\","
        echo "    \"code\": \"$code\","
        echo "    \"message\": \"$message\","
        echo "    \"hint\": \"$hint\","
        [[ -n "$related_log" ]] && echo "    \"related_log\": \"$related_log\","
        echo "    \"triggered_by\": \"${PG_FAIL_BY_TRAP:-explicit}\""
        echo "  },"
        echo "  \"metadata\": {"
        echo "    \"agent_recoverable\": $recoverable,"
        [[ -n "$fix_hook" ]]     && echo "    \"fix_hook\": \"$fix_hook\","
        [[ -n "$fix_args" ]]     && echo "    \"fix_hook_args\": \"$fix_args\","
        [[ -n "$retry_after" ]]  && echo "    \"retry_after_seconds\": $retry_after,"
        [[ -n "$max_retries" ]]  && echo "    \"max_retries\": $max_retries,"
        echo "    \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
        echo "  }"
        echo "}"
    } > "${PG_RESULT_FILE:-/tmp/pg-result.json}"

    exit 1
}

# ----- pg-exit -----
pg_exit() {
    local status="pass" duration="" metadata_kv=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --status=*)  status="${1#*=}" ;;
            --duration=*) duration="${1#*=}" ;;
            --metadata=*) metadata_kv="${1#*=}" ;;
            *) ;;
        esac
        shift
    done

    {
        echo "{"
        echo "  \"status\": \"$status\","
        echo "  \"exit_code\": 0,"
        [[ -n "$duration" ]] && echo "  \"duration_seconds\": $duration,"
        echo "  \"metadata\": {"
        [[ -n "$metadata_kv" ]] && echo "    $metadata_kv,"
        echo "    \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
        echo "  }"
        echo "}"
    } > "${PG_RESULT_FILE:-/tmp/pg-result.json}"

    exit 0
}

# ----- pg-fail-on-error (trap 兜底) -----
pg_fail_on_error() {
    local exit_code=$1 line_no=$2
    # 拿最近 20 行 stderr / 日志
    local last_output=""
    if [[ -n "${PG_LOG_FILE:-}" && -f "$PG_LOG_FILE" ]]; then
        last_output=$(tail -20 "$PG_LOG_FILE" 2>/dev/null || echo "")
    fi

    # 启发式分类
    local category="unknown"
    if echo "$last_output" | grep -qiE "address already in use|port.*already"; then
        category="port_in_use"
    elif echo "$last_output" | grep -qiE "command not found|No such file or directory"; then
        category="prereq_missing"
    elif echo "$last_output" | grep -qiE "Permission denied|EACCES"; then
        category="permission_denied"
    elif echo "$last_output" | grep -qiE "Connection refused|Could not resolve|no route to host"; then
        category="network"
    elif echo "$last_output" | grep -qiE "BUILD FAILURE|compilation failed"; then
        category="build_failure"
    elif echo "$last_output" | grep -qiE "FAILED|expected.*but was|AssertionError"; then
        category="test_failure"
    elif echo "$last_output" | grep -qiE "out of memory|disk full|No space left"; then
        category="resource_exhausted"
    fi

    PG_FAIL_BY_TRAP=1 \
    pg_fail \
        --category="$category" \
        --code=PG-E-0901 \
        --message="Hook failed at line $line_no with exit code $exit_code" \
        --hint="Check ${PG_LOG_FILE:-runtime log} for context" \
        --related-log="${PG_LOG_FILE:-}" \
        --agent-recoverable=true
}

# ----- pg-start-bg: 后台启动命令, setsid detach -----
#
# 用法: pg_start_bg <log_file> <pid_file> [env_kv ...] -- <cmd ...>
#
#   log_file   业务日志路径 (pg-run-hook.py 注入的 $LOG_DIR/<role>.log)
#   pid_file   PID 写出位置 (供 pg_stop_bg / health 复用)
#   env_kv     KEY=VALUE 形式的 env, 作为 env argv 传递 (无 shell 解析, 无注入风险)
#   --         分隔符
#   cmd        要后台执行的命令及其参数
#
# 返回: 启动成功 → echo PID (stdout), exit 0
#       启动失败 → stderr 报错, exit 1
#
# 副作用:
#   - 子进程用 setsid 进入新 session, 父 shell 退出不影响其存活 (避免 opencode
#     120s shell 超时杀掉服务)
#   - 使用 env -i 清空所有环境变量, 仅保留 env_kv + PATH (避免泄漏)
#   - 若 setsid 不可用 (eg. macOS), 降级 nohup + disown
#   - 写 PID 到 pid_file, 调用方可读
#   - 短暂 sleep 后检查子进程是否仍存活, 立即 crash 则报错
#
# 设计要点:
#   - 不用 bash sub-shell `export X; export Y; exec cmd` 的写法, 因为 env_kv
#     走 bash 解析会有空格/元字符注入风险. `env KEY=VALUE ...` argv 模式安全.
#   - `env -i` 显式清空, 防止父 hook 的环境变量污染子进程. 业务需要的 PATH 必须
#     在 env_kv 里或显式 PATH="$PATH" 续传 (本函数已保留 PATH).
pg_start_bg() {
    local log_file=$1 pid_file=$2
    shift 2

    local -a env_args=() cmd_args=()
    while [[ $# -gt 0 && "$1" != "--" ]]; do
        env_args+=("$1")
        shift
    done
    if [[ "${1:-}" != "--" ]]; then
        echo "pg_start_bg: missing '--' separator between env_kv and cmd" >&2
        return 1
    fi
    shift
    cmd_args=("$@")
    if [[ ${#cmd_args[@]} -eq 0 ]]; then
        echo "pg_start_bg: empty cmd" >&2
        return 1
    fi

    mkdir -p "$(dirname "$log_file")" "$(dirname "$pid_file")"

    local pid
    if command -v setsid >/dev/null 2>&1; then
        # setsid: 新 session, 父进程退出不影响.
        setsid env -i "${env_args[@]}" PATH="$PATH" "${cmd_args[@]}" \
            > "$log_file" 2>&1 &
        pid=$!
    else
        # 兜底: macOS 等缺 setsid 的环境.
        nohup env -i "${env_args[@]}" PATH="$PATH" "${cmd_args[@]}" \
            > "$log_file" 2>&1 &
        pid=$!
        disown 2>/dev/null || true
    fi

    echo "$pid" > "$pid_file"

    # 短暂确认子进程未立即 crash (eg. 命令找不到, 权限拒绝).
    sleep 0.1
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "pg_start_bg: child exited immediately (PID $pid); see $log_file" >&2
        return 1
    fi

    echo "$pid"
}

# ----- pg-stop-bg: 优雅关停 PID 文件指向的进程 -----
#
# 用法: pg_stop_bg <pid_file> <name> [<grace_seconds>]
#
#   pid_file      PID 文件路径
#   name          人类可读名 (日志输出用)
#   grace_seconds SIGTERM 后等待秒数, 0 立即 SIGKILL, 默认 5
#
# 行为:
#   1. PID 文件不存在 → 静默 skip, exit 0 (幂等)
#   2. PID 文件存在但进程已死 → 清理 stale PID, exit 0
#   3. 进程存活 → SIGTERM → 等 grace_seconds → SIGKILL
#
# 取代 lib/common.sh:kill_pid_file (该函数已弃用, 调用方改用 pg_stop_bg).
pg_stop_bg() {
    local pid_file=$1 name=$2
    local grace_seconds=${3:-5}

    if [[ ! -f "$pid_file" ]]; then
        echo "$name: no PID file, skip" >&2
        return 0
    fi
    local pid
    pid=$(cat "$pid_file" 2>/dev/null || echo "")
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
        echo "$name: not running (stale PID file)" >&2
        rm -f "$pid_file"
        return 0
    fi
    echo "Stopping $name (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    local count=0
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        count=$((count + 1))
        if [[ $count -ge "$grace_seconds" ]]; then
            echo "Force killing $name (PID $pid)..."
            kill -9 "$pid" 2>/dev/null || true
            break
        fi
    done
    rm -f "$pid_file"
}

# ----- pg-http-health-check: HTTP 探针 (用于 backend / frontend) -----
#
# 用法: pg_http_health_check <role> <instance_name> <host> <port> <path>
#
#   role           角色名 (仅用于日志)
#   instance_name  实例名 (仅用于日志)
#   host           目标 host
#   port           目标端口
#   path           HTTP path (e.g. /actuator/health, /)
#
# 行为:
#   用 curl 探测 http://${host}:${port}${path}, 期望收到任意 HTTP 状态码
#   (含 404/401, 只要不是 000=连接失败).
#
# 返回:
#   0 → 探针成功 (收到 HTTP 响应)
#   1 → 探针失败 (连接失败 / 超时 / DNS 错误)
#
# 注意:
#   - 不强制要求 200 OK, 因为 /actuator/health 可能因为权限/认证返回 401/403,
#     但只要服务进程在响应就算"就绪". 业务级健康检查由 backend 自己暴露.
#   - timeout 10s, 不挂起 health_check hook (hook 默认 30s 超时).
pg_http_health_check() {
    local role="$1" instance_name="$2" host="$3" port="$4" path="${5:-/}"
    local url="http://${host}:${port}${path}"
    local http_code

    echo "pg_http_health_check: probing $url (role=$role instance=$instance_name)" >&2
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || echo "000")
    # curl 在连不上时会输出 "000" 到 stdout, 但 || echo "000" 又叠加一次导致 "000000".
    # 取最后 3 位即可 (避免 OR-fallback 副作用).
    http_code="${http_code: -3}"
    if [[ "$http_code" == "000" ]]; then
        echo "pg_http_health_check: FAILED — cannot connect to $url" >&2
        return 1
    fi
    echo "pg_http_health_check: OK — $url returned HTTP $http_code" >&2
    return 0
}

# ----- pg-tcp-health-check: TCP 端口探针 (用于 agent / gRPC 服务) -----
#
# 用法: pg_tcp_health_check <role> <instance_name> <host> <port>
#
# 行为:
#   用 /dev/tcp 直接 connect, 不发任何 payload (适合 gRPC binary protocol).
#
# 返回:
#   0 → 端口 LISTEN
#   1 → 连接失败
#
# 注意:
#   - 仅检查 TCP 层可达, 不验证 gRPC 业务层. 业务级就绪由 agent 自行
#     通过 handshake 上报到 backend.
#   - timeout 5s (bash /dev/tcp 不支持原生 timeout, 用 timeout wrapper)
pg_tcp_health_check() {
    local role="$1" instance_name="$2" host="$3" port="$4"

    echo "pg_tcp_health_check: probing ${host}:${port} (role=$role instance=$instance_name)" >&2
    if timeout 5 bash -c "exec 3<>/dev/tcp/${host}/${port}" 2>/dev/null; then
        echo "pg_tcp_health_check: OK — ${host}:${port} is listening" >&2
        return 0
    fi
    echo "pg_tcp_health_check: FAILED — cannot connect to ${host}:${port}" >&2
    return 1
}
