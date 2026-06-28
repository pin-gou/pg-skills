#!/usr/bin/env python3
"""pg-run-hook.py — Unified hook command executor for pg-skills.

Reads a JSON command spec from stdin, injects consistent PG_* env vars
(hook protocol), runs the command, handles timeout and logging,
returns JSON result.

Replaces: pg-regression/scripts/pg-run-command.py (merged here).
Scope:    env hooks (prepare_env / clean_env) + role actions
          (start / stop / logs / tail) only. Module hooks (build / lint /
          test.unit / test.integration) stay as raw `timeout N bash -c '<cmd>'`
          strings — agents keep flexibility to run individual tests etc.

Usage:
  python3 pg-run-hook.py <<'EOF'
  {
    "cmd": "bash /abs/.pg/hooks/role-backend-start.sh backend backend-1 --grpc",
    "change": "add-host-memory-overview",
    "stage": "dev",
    "env": "dev-local",
    "role": "backend",
    "instance_name": "backend-1",
    "instance_host": "localhost",
    "hook_type": "start",
    "timeout_seconds": 300,
    "log_path": "/abs/.pg/changes/<change>/2-build/<env>/logs/role.backend.start@backend-1.log"
  }
  EOF

Input JSON fields:
  cmd             (required) — bash command to execute (typically
                              `bash /abs/.pg/hooks/<name>.sh [args...]`)
  session         (optional) — session name; injected as PG_RUN_SESSION
  stage           (optional) — stage name; injected as PG_STAGE
  env             (optional) — environment name (dev-local / dev-3tier);
                              injected as PG_ENV
  role            (optional) — role name; injected as PG_ROLE
  instance_name   (optional) — instance name; injected as PG_INSTANCE_NAME
  instance_host   (optional) — instance host; injected as PG_INSTANCE_HOST
  hook_type       (optional) — hook type label; injected as PG_HOOK_TYPE
  caller          (optional) — caller identity; overrides PG_RUN_CALLER
  hook_log_dir    (optional) — pre-resolved log dir; injected as PG_HOOK_LOG_DIR
  hook_result_path (optional) — result.json path; injected as PG_RESULT_FILE
  timeout_seconds (optional) — timeout in seconds (default: no timeout)
  log_path        (optional) — path to tee output to a log file
  wait_for_completion (optional) — bool, default True. False = fire-and-forget:
                              used by start action, hook 进程 spawn 后台服务
                              (via pg_start_bg setsid detach) 后立即返回 ok,
                              不等子进程完成. 上限 = min(timeout, 30)s.
  command         (alternative to cmd/timeout_seconds) — flat object
                  {"cmd": "...", "timeout_seconds": N}; overrides flat
                  fields if both are given (nested wins).
"""

import json
import os
import subprocess
import sys
import threading


def find_project_root():
    env_root = os.environ.get("PG_PROJECT_ROOT")
    if env_root and _has_config(env_root):
        return env_root
    cwd = os.getcwd()
    if _has_config(cwd):
        return cwd
    p = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if _has_config(p):
            return p
        p = os.path.dirname(p)
    return cwd


def _has_config(path):
    return (os.path.isfile(os.path.join(path, ".pg", "project.yaml"))
            or os.path.isfile(os.path.join(path, "pg-spec", "config.yaml")))


PROJECT_ROOT = find_project_root()

# Map spec fields to PG_* env var names. Each entry: spec_key -> env_var.
# All keys are optional; only non-empty values are injected.
#
# v5 SSOT: .pg/skills/src/runtime/spec/hook-env-vars.yaml
#   改动 _PG_ENV_MAP 必须同步 SSOT 文件 + README §7.1.5
#   测试校验: src/runtime/tests/test_hook_env_vars_ssot.py
#
# 协议范围: 仅 environments 维度. modules 维度不走 hook 协议 (pg-run 直接 cwd 调用).
# 历史 alias (PG_SKILL_NAME / PG_CHANGE_NAME / PG_MODULE) 不再注入.
_PG_ENV_MAP = {
    "session": "PG_RUN_SESSION",
    "caller": "PG_RUN_CALLER",
    "stage": "PG_STAGE",
    "env": "PG_ENV",
    "role": "PG_ROLE",
    "instance_name": "PG_INSTANCE_NAME",
    "instance_host": "PG_INSTANCE_HOST",
    "hook_type": "PG_HOOK_TYPE",
    "hook_log_dir": "PG_HOOK_LOG_DIR",
    "log_path": "PG_LOG_FILE",
    "hook_result_path": "PG_RESULT_FILE",
}


def build_env(spec):
    """Merge os.environ with required + spec-driven PG_* env vars.

    Always-injected (project-controlled):
        PG_PROJECT_ROOT — project root (find_project_root)
        PG_SKILLS_PATH  — pg-skills subtree path (computed from __file__)
        PG_RUN_CALLER   — caller identity (pg-build / pg-regression / pg-fix-issue / ad-hoc)
                          resolved from $PG_RUNNER_ORIGIN (legacy alias 仍兼容) or "ad-hoc".

    Spec-injected (caller-controlled):
        Each non-empty value in _PG_ENV_MAP is set as the corresponding
        PG_* env var.
    """
    env = os.environ.copy()
    env["PG_PROJECT_ROOT"] = PROJECT_ROOT
    env["PG_SKILLS_PATH"] = os.path.join(PROJECT_ROOT, ".pg", "skills")
    env.setdefault("PG_RUN_CALLER", os.environ.get("PG_RUNNER_ORIGIN", "ad-hoc"))
    for spec_key, env_var in _PG_ENV_MAP.items():
        val = spec.get(spec_key)
        if val:
            env[env_var] = str(val)
    return env


def run_command(cmd, merged_env, timeout, log_path, wait_for_completion=True):
    """Execute cmd and return (ok, exit_code).

    wait_for_completion (bool, default True):
        True  — 标准模式: 等子进程退出, 期间超时则 proc.kill().
        False — "fire-and-forget" 模式 (用于 start action):
                hook 进程一旦 spawn 出后台服务 (通常用 pg_start_bg setsid detach)
                就立即返回 ok. 子进程继续在 hook 退出后运行, 不受 pg-run-hook.py
                timeout 影响.
                等待上限 = min(timeout, 30) — 给 hook 30s 时间完成 spawn + 写 PID + 调用
                wait_for_port_with_monitor 等快速启动检查; 超过此时间仍 return ok,
                因为后台服务可能还在启动 (eg. mvn 编译慢).
    """
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log_f:
            header = f"=== pg-run-hook [{os.path.basename(log_path)}] ==="
            log_f.write(header + "\n")
            proc = subprocess.Popen(
                ["bash", "-c", cmd],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, cwd=PROJECT_ROOT, env=merged_env,
            )

            def _tee(stream, dest_f, label=""):
                try:
                    for line in iter(stream.readline, ""):
                        if label:
                            dest_f.write(f"[{label}] {line}")
                        else:
                            dest_f.write(line)
                        dest_f.flush()
                        sys.stdout.write(line)
                        sys.stdout.flush()
                except ValueError:
                    pass
                finally:
                    try:
                        stream.close()
                    except Exception:
                        pass

            threads = []
            for s, label in [(proc.stdout, ""), (proc.stderr, "stderr")]:
                t = threading.Thread(target=_tee, args=(s, log_f, label))
                t.daemon = True
                t.start()
                threads.append(t)

            if wait_for_completion:
                # 标准模式: 等到底, 超时杀子进程
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            else:
                # fire-and-forget: 等 hook 进程完成 spawn 后立即返回
                spawn_wait = min(timeout, 30)
                try:
                    proc.wait(timeout=spawn_wait)
                except subprocess.TimeoutExpired:
                    # hook 未在 spawn_wait 内退出. 杀掉 hook bash,
                    # 让 hook 内部 setsid detach 的孙子进程继续.
                    log_f.write(
                        f"--- fire-and-forget: hook not exited within {spawn_wait}s, "
                        f"killing hook but detached children survive ---\n"
                    )
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                # 关掉 pipe fd, 让 tee 线程收到 EOF 自然退出
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

            for t in threads:
                t.join(timeout=2)

            # fire-and-forget 模式: hook 是被我们主动 kill 的 (returncode == -9),
            # 不算 hook 失败. 后续 main() 会根据 wait_for_completion 区分.
            ok = proc.returncode == 0
            log_f.write(f"--- exit: {'OK' if ok else 'FAILED'} (exit={proc.returncode}) ---\n\n")
            return ok, proc.returncode
    else:
        if wait_for_completion:
            try:
                result = subprocess.run(
                    ["bash", "-c", cmd],
                    text=True,
                    cwd=PROJECT_ROOT,
                    env=merged_env,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return False, -9
            return result.returncode == 0, result.returncode
        else:
            # fire-and-forget 无 log_path
            proc = subprocess.Popen(
                ["bash", "-c", cmd],
                cwd=PROJECT_ROOT, env=merged_env,
            )
            spawn_wait = min(timeout, 30)
            try:
                proc.wait(timeout=spawn_wait)
                # hook 自然退出, 报告其 exit_code
                return proc.returncode == 0, proc.returncode
            except subprocess.TimeoutExpired:
                # hook 未退出. 杀掉 bash, 让孙子进程继续.
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            # main() 会把 -9 转为 ok=true
            return False, -9



def main():
    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"ok": False, "exit_code": -1, "error": "No input received on stdin"}))
        sys.exit(1)
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "exit_code": -1, "error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    cmd = spec.get("cmd", "").strip()
    timeout = spec.get("timeout_seconds")
    log_path = spec.get("log_path")
    wait_for_completion = spec.get("wait_for_completion", True)

    # Nested `command: {cmd, timeout_seconds}` form: overrides flat fields.
    nested = spec.get("command")
    if isinstance(nested, dict):
        nested_cmd = (nested.get("cmd") or "").strip()
        if nested_cmd:
            cmd = nested_cmd
        if "timeout_seconds" in nested and nested["timeout_seconds"] is not None:
            timeout = nested["timeout_seconds"]

    if not cmd:
        print(json.dumps({"ok": False, "exit_code": -1,
                          "error": "cmd is required (set `cmd` or `command.cmd`)"}))
        sys.exit(1)

    merged_env = build_env(spec)
    ok, exit_code = run_command(
        cmd, merged_env, timeout, log_path,
        wait_for_completion=wait_for_completion,
    )

    # fire-and-forget: hook 被我们主动 kill (-9), 不算失败.
    # 转为 ok=true, exit_code=0 表示"成功 spawn, 后台服务继续运行".
    if not wait_for_completion and exit_code == -9:
        ok = True
        exit_code = 0

    result = {
        "ok": ok,
        "exit_code": exit_code,
        "log_path": log_path or "",
        "wait_for_completion": wait_for_completion,
    }
    if not ok:
        if exit_code == -9:
            result["error"] = f"Timeout after {timeout}s" if timeout else "Process killed"
        else:
            result["error"] = f"exit={exit_code}"
    print(json.dumps(result))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
