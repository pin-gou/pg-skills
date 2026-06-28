#!/usr/bin/env bash
# test_pg_start_stop_bg.sh — bash 单元测试: hook-helpers.sh:pg_start_bg / pg_stop_bg
#
# 跑法: bash src/runtime/tests/test_pg_start_stop_bg.sh
# 退出码: 0 = 全 pass, 非 0 = 有失败

set -uo pipefail
# 不加 -e: hook-helpers.sh source 后会强制 set -e, 但本测试需要容忍 ((count++)) 返回 1
set +e

TESTS_PASS=0
TESTS_FAIL=0
FAILED_TESTS=()

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPERS="$THIS_DIR/../lib/hook-helpers.sh"

if [[ ! -f "$HELPERS" ]]; then
    echo "FATAL: hook-helpers.sh 不存在: $HELPERS" >&2
    exit 2
fi

# shellcheck source=/dev/null
source "$HELPERS"

# hook-helpers.sh 里强制 set -e, 关掉避免 ((count++)) 之类返回非零时中断
set +e

# 测试工作目录
TEST_TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TEST_TMPDIR"' EXIT

assert_eq() {
    local name=$1 expected=$2 actual=$3
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $name"
        ((TESTS_PASS++))
    else
        echo "  FAIL: $name (expected '$expected', got '$actual')"
        ((TESTS_FAIL++))
        FAILED_TESTS+=("$name")
    fi
}

assert_true() {
    local name=$1 actual=$2
    if [[ "$actual" == "true" || "$actual" == "0" ]]; then
        echo "  PASS: $name"
        ((TESTS_PASS++))
    else
        echo "  FAIL: $name (got '$actual')"
        ((TESTS_FAIL++))
        FAILED_TESTS+=("$name")
    fi
}

assert_false() {
    local name=$1 actual=$2
    if [[ "$actual" == "false" || "$actual" != "0" && "$actual" != "true" ]]; then
        echo "  PASS: $name"
        ((TESTS_PASS++))
    else
        echo "  FAIL: $name (got '$actual')"
        ((TESTS_FAIL++))
        FAILED_TESTS+=("$name")
    fi
}

assert_file_exists() {
    local name=$1 path=$2
    if [[ -f "$path" ]]; then
        echo "  PASS: $name"
        ((TESTS_PASS++))
    else
        echo "  FAIL: $name (file not found: $path)"
        ((TESTS_FAIL++))
        FAILED_TESTS+=("$name")
    fi
}

assert_file_contains() {
    local name=$1 path=$2 pattern=$3
    if [[ -f "$path" ]] && grep -qE "$pattern" "$path"; then
        echo "  PASS: $name"
        ((TESTS_PASS++))
    else
        echo "  FAIL: $name (pattern '$pattern' not in $path)"
        ((TESTS_FAIL++))
        FAILED_TESTS+=("$name")
    fi
}

echo "=== pg_start_bg 基础测试 ==="

# Test 1: pg_start_bg 启动 sleep 60 进程, 验证 PID 写出
LOG_FILE="$TEST_TMPDIR/test1.log"
PID_FILE="$TEST_TMPDIR/test1.pid"
SPAWNED_PID=$(pg_start_bg "$LOG_FILE" "$PID_FILE" -- sleep 60)
assert_eq "spawn 返回非空 PID" "true" "$([ -n "$SPAWNED_PID" ] && echo true || echo false)"
assert_file_exists "PID 文件存在" "$PID_FILE"
PID_IN_FILE=$(cat "$PID_FILE")
assert_eq "PID 文件内容 = spawn 返回值" "$SPAWNED_PID" "$PID_IN_FILE"
# 进程应仍在运行
if kill -0 "$SPAWNED_PID" 2>/dev/null; then
    assert_eq "进程仍在运行" "true" "true"
    # 清理
    kill "$SPAWNED_PID" 2>/dev/null || true
    wait "$SPAWNED_PID" 2>/dev/null || true
else
    assert_eq "进程仍在运行" "true" "false"
fi

echo ""
echo "=== pg_start_bg env_kv 注入测试 ==="

# Test 2: env_kv 注入到子进程, 验证子进程能读到
LOG_FILE="$TEST_TMPDIR/test2.log"
PID_FILE="$TEST_TMPDIR/test2.pid"
SPAWNED_PID=$(pg_start_bg "$LOG_FILE" "$PID_FILE" \
    "FOO=bar" "BAZ=qux" -- \
    sh -c 'echo "FOO=$FOO BAZ=$BAZ"; sleep 0.2')
assert_eq "spawn 成功" "true" "$([ -n "$SPAWNED_PID" ] && echo true || echo false)"
sleep 0.5  # 等子进程输出
assert_file_contains "env 注入: FOO=bar" "$LOG_FILE" "^FOO=bar BAZ=qux"
# 清理
if kill -0 "$SPAWNED_PID" 2>/dev/null; then
    wait "$SPAWNED_PID" 2>/dev/null || true
fi

echo ""
echo "=== pg_start_bg detach 测试 (setsid 新 session) ==="

# Test 3: 验证子进程在不同的 session (PPID 应为 1 或新 session leader)
LOG_FILE="$TEST_TMPDIR/test3.log"
PID_FILE="$TEST_TMPDIR/test3.pid"
SPAWNED_PID=$(pg_start_bg "$LOG_FILE" "$PID_FILE" -- sleep 30)
sleep 0.2
if [[ -n "$SPAWNED_PID" ]] && kill -0 "$SPAWNED_PID" 2>/dev/null; then
    PGID=$(ps -o pgid= -p "$SPAWNED_PID" 2>/dev/null | tr -d ' ')
    SID=$(ps -o sid= -p "$SPAWNED_PID" 2>/dev/null | tr -d ' ')
    PGID_OF_SPAWNED=$(ps -o pgid= -p "$SPAWNED_PID" 2>/dev/null | tr -d ' ')
    # session leader: PGID == PID
    if [[ "$PGID" == "$SPAWNED_PID" ]]; then
        assert_eq "子进程是 session leader (PGID=PID)" "true" "true"
    else
        assert_eq "子进程是 session leader (PGID=PID)" "true" "false (PGID=$PGID, PID=$SPAWNED_PID)"
    fi
    # 清理
    kill "$SPAWNED_PID" 2>/dev/null || true
    wait "$SPAWNED_PID" 2>/dev/null || true
else
    assert_eq "spawn 测试 3" "true" "false (进程未运行)"
fi

echo ""
echo "=== pg_start_bg 错误处理测试 ==="

# Test 4: 命令不存在 → 立即退出 → 返回 1
LOG_FILE="$TEST_TMPDIR/test4.log"
PID_FILE="$TEST_TMPDIR/test4.pid"
SPAWN_OUTPUT=$(pg_start_bg "$LOG_FILE" "$PID_FILE" -- /nonexistent/command/xyz 2>&1)
SPAWN_RC=$?
assert_eq "命令不存在 → 非零退出" "1" "$SPAWN_RC"

# Test 5: 缺 -- 分隔符
LOG_FILE="$TEST_TMPDIR/test5.log"
PID_FILE="$TEST_TMPDIR/test5.pid"
SPAWN_OUTPUT=$(pg_start_bg "$LOG_FILE" "$PID_FILE" "FOO=bar" echo hello 2>&1)
SPAWN_RC=$?
assert_eq "缺 -- 分隔符 → 非零退出" "1" "$SPAWN_RC"

# Test 6: 空命令
LOG_FILE="$TEST_TMPDIR/test6.log"
PID_FILE="$TEST_TMPDIR/test6.pid"
SPAWN_OUTPUT=$(pg_start_bg "$LOG_FILE" "$PID_FILE" -- 2>&1)
SPAWN_RC=$?
assert_eq "空命令 → 非零退出" "1" "$SPAWN_RC"

echo ""
echo "=== pg_stop_bg 测试 ==="

# Test 7: 关停运行中进程
LOG_FILE="$TEST_TMPDIR/test7.log"
PID_FILE="$TEST_TMPDIR/test7.pid"
SPAWNED_PID=$(pg_start_bg "$LOG_FILE" "$PID_FILE" -- sleep 30)
sleep 0.2
pg_stop_bg "$PID_FILE" "test7" 2 2>&1
STOP_RC=$?
assert_eq "pg_stop_bg 退出 0" "0" "$STOP_RC"
if kill -0 "$SPAWNED_PID" 2>/dev/null; then
    assert_eq "进程已停止" "true" "false"
    kill -9 "$SPAWNED_PID" 2>/dev/null || true
else
    assert_eq "进程已停止" "true" "true"
fi
# PID 文件应已清理
if [[ -f "$PID_FILE" ]]; then
    assert_eq "PID 文件已清理" "true" "false"
else
    assert_eq "PID 文件已清理" "true" "true"
fi

# Test 8: 幂等 - 重复 stop
pg_stop_bg "$PID_FILE" "test7-second" 2>&1
SECOND_STOP_RC=$?
assert_eq "重复 stop (无 PID 文件) → 0" "0" "$SECOND_STOP_RC"

# Test 9: 关停不存在的进程 (stale PID file)
echo "99999" > "$TEST_TMPDIR/stale.pid"
pg_stop_bg "$TEST_TMPDIR/stale.pid" "stale" 2>&1
STALE_RC=$?
assert_eq "stale PID → 0 (清理 PID 文件)" "0" "$STALE_RC"
if [[ -f "$TEST_TMPDIR/stale.pid" ]]; then
    assert_eq "stale PID 文件已清理" "true" "false"
else
    assert_eq "stale PID 文件已清理" "true" "true"
fi

# Test 10: PID 文件不存在
pg_stop_bg "$TEST_TMPDIR/nonexistent.pid" "missing" 2>&1
MISSING_RC=$?
assert_eq "PID 文件不存在 → 0 (幂等)" "0" "$MISSING_RC"

# Test 11: grace_seconds 强制 SIGKILL
LOG_FILE="$TEST_TMPDIR/test11.log"
PID_FILE="$TEST_TMPDIR/test11.pid"
# 启动一个会忽略 SIGTERM 的进程 (用 trap 捕获, 不退出)
SPAWNED_PID=$(pg_start_bg "$LOG_FILE" "$PID_FILE" -- \
    sh -c 'trap "" TERM; sleep 30')
sleep 0.2
pg_stop_bg "$PID_FILE" "ignores-term" 2 2>&1
# 验证进程已被 SIGKILL
sleep 0.3
if kill -0 "$SPAWNED_PID" 2>/dev/null; then
    assert_eq "SIGTERM-resistant 进程被 SIGKILL" "true" "false"
    kill -9 "$SPAWNED_PID" 2>/dev/null || true
else
    assert_eq "SIGTERM-resistant 进程被 SIGKILL" "true" "true"
fi

echo ""
echo "=== pg_start_bg 集成: log 重定向 ==="

# Test 12: stdout/stderr 都被重定向到 log_file
LOG_FILE="$TEST_TMPDIR/test12.log"
PID_FILE="$TEST_TMPDIR/test12.pid"
SPAWNED_PID=$(pg_start_bg "$LOG_FILE" "$PID_FILE" -- \
    sh -c 'echo to_stdout; echo to_stderr 1>&2; sleep 0.1')
sleep 0.5
assert_file_contains "stdout 重定向到 log" "$LOG_FILE" "^to_stdout$"
assert_file_contains "stderr 重定向到 log" "$LOG_FILE" "^to_stderr$"

echo ""
echo "============================================"
echo "测试总结: $TESTS_PASS passed, $TESTS_FAIL failed"
echo "============================================"

if [[ $TESTS_FAIL -gt 0 ]]; then
    echo ""
    echo "失败用例:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "  - $t"
    done
    exit 1
fi

exit 0