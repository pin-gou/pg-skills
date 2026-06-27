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
  change          (optional) — change name; injected as PG_CHANGE_NAME
  stage           (optional) — stage name; injected as PG_STAGE
  env             (optional) — environment name (dev-local / dev-3tier);
                              injected as PG_ENV
  role            (optional) — role name; injected as PG_ROLE
  instance_name   (optional) — instance name; injected as PG_INSTANCE_NAME
  instance_host   (optional) — instance host; injected as PG_INSTANCE_HOST
  hook_type       (optional) — hook type label; injected as PG_HOOK_TYPE
  timeout_seconds (optional) — timeout in seconds (default: no timeout)
  log_path        (optional) — path to tee output to a log file
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
# v4 协议:
# - 新增 "session" -> PG_RUN_SESSION (与 caller 正交的 session 名).
# - 新增 "caller"  -> PG_RUN_CALLER  (语义更清晰的 caller 维度, 取代 PG_SKILL_NAME).
# - 保留 "change" / "skill" 作为 1 版本 alias (向下兼容老 hook).
# - 新增 "log_path" -> PG_LOG_FILE / "hook_result_path" -> PG_RESULT_FILE
#   (修 D5: pg-run-hook.py 历史上没注入这两个 var, hook 模板头部注释里写了但拿不到).
_PG_ENV_MAP = {
    "session": "PG_RUN_SESSION",
    "change": "PG_CHANGE_NAME",
    "caller": "PG_RUN_CALLER",
    "skill": "PG_SKILL_NAME",
    "stage": "PG_STAGE",
    "env": "PG_ENV",
    "module": "PG_MODULE",
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
                          resolved from $PG_RUNNER_ORIGIN (legacy) or "ad-hoc".
        PG_SKILL_NAME   — DEPRECATED alias of PG_RUN_CALLER (1 版本兼容).

    Spec-injected (caller-controlled):
        Each non-empty value in _PG_ENV_MAP is set as the corresponding
        PG_* env var.
    """
    env = os.environ.copy()
    env["PG_PROJECT_ROOT"] = PROJECT_ROOT
    env["PG_SKILLS_PATH"] = os.path.join(PROJECT_ROOT, ".pg", "skills")
    # v4: PG_RUN_CALLER 硬缺省 'ad-hoc' (历史兼容 PG_RUNNER_ORIGIN alias)
    env.setdefault("PG_RUN_CALLER", os.environ.get("PG_RUNNER_ORIGIN", "ad-hoc"))
    # DEPRECATED alias (1 版本), 后续删
    env.setdefault("PG_SKILL_NAME", env["PG_RUN_CALLER"])
    for spec_key, env_var in _PG_ENV_MAP.items():
        val = spec.get(spec_key)
        if val:
            env[env_var] = str(val)
    return env


def run_command(cmd, merged_env, timeout, log_path):
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
                for line in iter(stream.readline, ""):
                    if label:
                        dest_f.write(f"[{label}] {line}")
                    else:
                        dest_f.write(line)
                    dest_f.flush()
                    sys.stdout.write(line)
                    sys.stdout.flush()
                stream.close()

            threads = []
            for s, label in [(proc.stdout, ""), (proc.stderr, "stderr")]:
                t = threading.Thread(target=_tee, args=(s, log_f, label))
                t.daemon = True
                t.start()
                threads.append(t)

            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            for t in threads:
                t.join(timeout=10)

            ok = proc.returncode == 0
            log_f.write(f"--- exit: {'OK' if ok else 'FAILED'} (exit={proc.returncode}) ---\n\n")
            return ok, proc.returncode
    else:
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
    ok, exit_code = run_command(cmd, merged_env, timeout, log_path)

    result = {"ok": ok, "exit_code": exit_code, "log_path": log_path or ""}
    if not ok:
        if exit_code == -9:
            result["error"] = f"Timeout after {timeout}s" if timeout else "Process killed"
        else:
            result["error"] = f"exit={exit_code}"
    print(json.dumps(result))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
