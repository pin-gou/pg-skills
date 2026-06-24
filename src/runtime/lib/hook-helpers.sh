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
