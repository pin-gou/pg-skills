#!/usr/bin/env python3
"""pg-pipeline-runner.py — Pipeline execution state machine for pg-build.

Replaces the LLM orchestrator's manual state management with a deterministic
runner. The LLM calls `next` to get the next action, dispatches sub-agents
when told, and calls `record` to report results.

Usage:
  python3 pg-pipeline-runner.py next <change>
    Advance pipeline, return next action JSON.

  python3 pg-pipeline-runner.py record <change> <status> [report_path]
    Record sub-agent result, advance, return next action JSON.

  python3 pg-pipeline-runner.py invoke-hook \
      --session <S> --env <ENV> --role <ROLE> --instance <I> --action <A> \
      [--stage <ST>] [--tail-lines <N>] [--skill <SKILL>]
    历史兼容入口 (thin wrapper, 转发到 .pg/skills/src/runtime/bin/pg-invoke-hook.py).
    新代码统一写新路径:
      python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook ...
    Resolves the action in project.yaml, builds the pg-run-hook.py spec,
    and spawns it. timeout_seconds is read from project.yaml (not a CLI
    flag). --tail-lines (logs/tail only) is appended to the hook args.

Status values for record:
  completed  — test/dev agent succeeded
  failed     — test/dev agent failed (runner handles retries)
  escalate   — verify agent needs fix cycle
  pass       — gate assessment passed
  fail       — gate assessment failed

Action JSON formats:

  # LLM must dispatch a sub-agent
  {"action": "dispatch", "item": "backend", "sub": "test",
   "agent": "pg-build/test", "context": {track config}}
  {"action": "dispatch", "item": "backend", "sub": "dev",
   "agent": "pg-build/dev", ...}
  {"action": "dispatch", "item": "backend", "sub": "verify",
   "agent": "pg-build/verify", ...}
  {"action": "dispatch", "item": "backend", "sub": "gate",
   "agent": "pg-build/gate", ...}
  {"action": "dispatch_fix", "item": "backend",
   "agent": "pg-build/fix", "cycle": 1}
  {"action": "dispatch_final_gate", "agent": "pg-build/gate"}

  # Runner executes a phase command directly
  {"action": "execute_phase", "item": "proto-compile",
   "command": "cd <module-name> && make proto"}

  # Terminal states
  {"action": "done", "status": "completed"}
  {"action": "workflow_failed", "fatal": True, "reason": "...", "item": "backend"}
"""

import json
import os
import re
import shlex
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print('{"error": "PyYAML required: pip install pyyaml"}', file=sys.stderr)
    sys.exit(1)

import pg_context_chain
from pg_pipeline_common import (
    get_track_type,
    load_config,
    normalize_simple_command,
    parse_tasks,
    parse_tasks_sections,
    pg_build_bootstrap,
    pg_build_dispatch_context,
)

# ============================================================
# Path resolution
# ============================================================


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
    return os.path.isfile(os.path.join(path, ".pg", "project.yaml")) or os.path.isfile(
        os.path.join(path, "pg-spec", "config.yaml")
    )


PROJECT_ROOT = find_project_root()
CONFIG_PATH = os.path.join(PROJECT_ROOT, ".pg/project.yaml")
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, ".opencode", "skills", "pg-build", "scripts")
PIPELINE_STATE_PY = os.path.join(SCRIPTS_DIR, "pg-pipeline-state.py")
VALIDATE_PROPOSAL_PY = os.path.join(SCRIPTS_DIR, "pg-validate-proposal.py")


def _pg_log_dir_for_skill(skill, change, env):
    """Return absolute log dir for a given skill. Mirrors pg-invoke-hook.py:pg_log_dir_for_skill
    and .pg/hooks/lib/common.sh:pg_resolve_paths. Keep all three in sync.
    """
    if skill == "pg-regression" and change and change.startswith("regression-"):
        suite = change[len("regression-") :]
        return os.path.join(PROJECT_ROOT, ".pg", "regression", suite, env, "logs")
    if skill == "pg-fix-issue":
        return os.path.join(PROJECT_ROOT, ".pg", "fix-issue", change, env, "logs")
    return os.path.join(PROJECT_ROOT, ".pg", "changes", change, "2-build", env, "logs")


CHANGES_DIR = os.path.join(PROJECT_ROOT, ".pg", "changes")
PG_ARCHIVE_PY = os.path.join(
    PROJECT_ROOT, ".opencode", "skills", "pg-archive", "scripts", "pg-archive.py"
)
# Unified hook command executor (env hooks + role actions).
# Lives in pg-skills subtree so manual scripts (up-dev-local.sh) can also
# reuse it. Module hooks (build/lint/test.<key>) stay as raw commands.
PG_HOOK_RUNNER = os.path.join(
    PROJECT_ROOT, ".pg", "skills", "src", "runtime", "lib", "pg-run-hook.py"
)

# pg-build 过程产物统一存放在此子目录下（与 1-propose-review/ 平行）。
# 核心交付物（proposal/design/tasks）仍保留在 change 根。
APPLY_DIR = "2-build"

# State / hidden files located in APPLY_DIR (relative to change dir).
APPLY_STATE_FILES = (
    ".context-chain.state",
    ".pipeline-state.json",
)

DEFAULT_FAIL_RETRIES = 3
MAX_FIX_CYCLES = 4
DEFAULT_GATE_FIX_RETRIES = 2

SUPPORTED_MANIFEST_VERSIONS = {"2026-06-30"}


def _read_manifest(change):
    """Read execution-manifest.yaml for the given change.

    Raises:
        FileNotFoundError: if manifest does not exist (hard fail).
        ValueError: if schema_version is not supported.
    """
    path = os.path.join(CHANGES_DIR, change, "execution-manifest.yaml")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"{path} 不存在. "
            f"请先执行 pg-propose 阶段生成: "
            f"python3 .opencode/skills/pg-build/scripts/pg-gen-manifest.py {change}"
        )
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        raise ValueError(f"{path} 为空或格式错误")
    sv = data.get("schema_version", "")
    if sv not in SUPPORTED_MANIFEST_VERSIONS:
        raise ValueError(
            f"manifest schema_version={sv!r} 不被支持. "
            f"支持: {sorted(SUPPORTED_MANIFEST_VERSIONS)}"
        )
    return data


def _validate_manifest(change):
    """Run pg-validate-proposal.py manifest against the given change.

    Returns (True, "") on success.
    Returns (False, stderr_output) on failure (validation issues found).
    """
    try:
        r = subprocess.run(
            [sys.executable, VALIDATE_PROPOSAL_PY, "manifest", change],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "manifest 校验超时 (60s)"
    except Exception as e:
        return False, f"manifest 校验调用失败: {e}"
    if r.returncode != 0:
        return False, r.stderr.strip() or r.stdout.strip()
    return True, ""


# ============================================================
# Helpers
# ============================================================


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _use_state_v2():
    """Return True if the runner should route next/record through the v2 entry points.

    Precedence:
      1. PG_USE_STATE_V2 env var (if set to "true"/"1"/"yes"): explicit override.
      2. project.yaml: state_v2.enabled (default false for backward compat).
    """
    env_val = os.environ.get("PG_USE_STATE_V2", "").strip().lower()
    if env_val in ("true", "1", "yes"):
        return True
    if env_val in ("false", "0", "no"):
        return False
    try:
        cfg = load_config()
        return bool((cfg.get("state_v2") or {}).get("enabled", False))
    except Exception:
        return False


def get_pipeline_order(config, change=None):
    if change:
        return get_pipeline_order_from_manifest(change)
    stages = config.get("stages") or []
    order = []
    for stage in stages:
        stage_name = stage.get("name", "")
        for t in stage.get("tracks") or []:
            qualified = f"{stage_name}.{t}" if stage_name else t
            order.append(qualified)
    return order


def get_pipeline_order_from_manifest(change):
    manifest = _read_manifest(change)
    order = []
    config = load_config()
    for stage in manifest.get("stages", []):
        stage_name = stage["name"]
        env_name = stage.get("environment", "")
        if env_name:
            env_cfg = (config.get("environments") or {}).get(env_name, {})
            if env_cfg.get("prepare_env"):
                order.append(f"{stage_name}.prepare_env")
        for track in stage.get("tracks", []):
            order.append(f"{stage_name}.{track['id']}")
        if env_name:
            env_cfg = (config.get("environments") or {}).get(env_name, {})
            if env_cfg.get("clean_env"):
                order.append(f"{stage_name}.clean_env")
    if "final_gate" in manifest:
        order.append("final-gate")
    return order


def get_track_config(config, item):
    # v3.0: tracks live at top level (config["tracks"]), not under config["pipeline"].
    bare = _bare_track(item)
    return (config.get("tracks") or {}).get(bare, {})


def get_state_path(change):
    return os.path.join(CHANGES_DIR, change, APPLY_DIR, ".pipeline-state.json")


def get_apply_dir(change):
    """Return absolute path to 2-build/ subdir under change root."""
    return os.path.join(CHANGES_DIR, change, APPLY_DIR)


def migrate_legacy_state_files(change):
    """One-shot migration of state files from change root → 2-build/.

    Runs idempotently: if 2-build/ already contains the file, the legacy
    file at change root is removed. Returns a list of filenames that were moved.
    """
    change_root = os.path.join(CHANGES_DIR, change)
    apply_dir = get_apply_dir(change)
    if not os.path.isdir(change_root):
        return []

    os.makedirs(apply_dir, exist_ok=True)

    # Cleanup legacy .pg-spec.yaml at change root (no longer generated)
    legacy_pg_spec = os.path.join(change_root, ".pg-spec.yaml")
    if os.path.isfile(legacy_pg_spec):
        os.remove(legacy_pg_spec)

    moved = []
    for fname in APPLY_STATE_FILES:
        legacy = os.path.join(change_root, fname)
        target = os.path.join(apply_dir, fname)
        if not os.path.isfile(legacy):
            continue
        if os.path.isfile(target):
            # Target already exists — legacy is stale, just remove it.
            os.remove(legacy)
            moved.append(f"{fname} (legacy removed, target existed)")
        else:
            os.rename(legacy, target)
            moved.append(fname)
    return moved


def run_script(script_path, *args, change=None, track_id=None):
    env = os.environ.copy()
    if change:
        env["PG_CHANGE_DIR"] = os.path.join(CHANGES_DIR, change)
    if track_id:
        env["PG_TRACK_ID"] = track_id
    result = subprocess.run(
        [sys.executable if script_path.endswith(".py") else "bash", script_path, *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=env,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip()}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": f"Invalid JSON from {script_path}: {result.stdout[:200]}"}


def run_bash(
    command, timeout_seconds=None, log_path=None, header="", change=None, track_id=None
):
    # Convert timeout_seconds to int if it is a string
    if timeout_seconds is not None and isinstance(timeout_seconds, str):
        timeout_seconds = int(timeout_seconds)
    """Execute a bash command, optionally teeing output to a log file in real time.

    When log_path is provided, opens the file in append mode and writes each
    line of stdout/stderr to both the log file and the parent process's
    stdout/stderr as the command executes. The header (if given) is written
    at open time for context.

    Returns (ok, stdout_summary, stderr_summary). In streaming mode the
    summary is empty — the full output is in the log file.
    """
    env = os.environ.copy()
    if change:
        env["PG_CHANGE_DIR"] = os.path.join(CHANGES_DIR, change)
    if track_id:
        env["PG_TRACK_ID"] = track_id
    # pg-skills hook protocol: inject env vars that hook scripts expect
    # (see .pg/skills/README.md § Hook 协议 > env 变量)
    env.setdefault("PG_PROJECT_ROOT", PROJECT_ROOT)
    env.setdefault("PG_SKILLS_PATH", os.path.join(PROJECT_ROOT, ".pg", "skills"))
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log_f:
            if header:
                log_f.write(header + "\n")
            proc = subprocess.Popen(
                ["bash", "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=PROJECT_ROOT,
                env=env,
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
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            for t in threads:
                t.join(timeout=10)

            if proc.returncode == 0:
                log_f.write(f"--- exit: OK ---\n\n")
                return True, "", ""
            else:
                reason = (
                    f"Timeout after {timeout_seconds}s"
                    if proc.returncode == -9
                    else f"exit={proc.returncode}"
                )
                log_f.write(f"--- exit: FAILED ({reason}) ---\n\n")
                return False, "", reason
    else:
        kwargs = dict(capture_output=True, text=True, cwd=PROJECT_ROOT, env=env)
        if timeout_seconds is not None:
            kwargs["timeout"] = timeout_seconds
        try:
            result = subprocess.run(["bash", "-c", command], **kwargs)
        except subprocess.TimeoutExpired:
            return False, "", f"Timeout after {timeout_seconds}s"
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()


_TIMEOUT_CACHE = None


def _get_next_call_timeout(config):
    """Scan all environments for the maximum prepare_env/clean_env timeout,
    add a 30s safety margin, and return the recommended timeout for the
    LLM's next `next` or `record` call via bash tool."""
    global _TIMEOUT_CACHE
    if _TIMEOUT_CACHE is not None:
        return _TIMEOUT_CACHE
    max_to = 120
    for env_name, env_cfg in config.get("environments", {}).items():
        for hook in ("prepare_env", "clean_env"):
            to = env_cfg.get(hook, {}).get("timeout_seconds")
            if to and int(to) > max_to:
                max_to = int(to)
    _TIMEOUT_CACHE = max_to + 30
    return _TIMEOUT_CACHE


def _inject_next_call_timeout(result, config):
    """Inject next_call_timeout_seconds into any result dict."""
    if isinstance(result, dict):
        result["next_call_timeout_seconds"] = _get_next_call_timeout(config)
    return result


def _phase_log_path(change, item_id):
    """Determine the next log file path for a phase execution.
    Naming: {item_id.replace('.', '-')}-{N}.log where N increments on each run.
    Scans 2-build/ for existing logs and picks max N + 1."""
    apply_dir = get_apply_dir(change)
    os.makedirs(apply_dir, exist_ok=True)
    safe_name = item_id.replace(".", "-")
    pattern = re.compile(rf"^{re.escape(safe_name)}-(\d+)\.log$")
    max_n = 0
    try:
        for fname in os.listdir(apply_dir):
            m = pattern.match(fname)
            if m:
                max_n = max(max_n, int(m.group(1)))
    except FileNotFoundError:
        pass
    return os.path.join(apply_dir, f"{safe_name}-{max_n + 1}.log")


def _phase_log_path_latest(change, item_id):
    """Return the path of the most recent {item_id}-{N}.log in 2-build/, or ''.

    Unlike _phase_log_path (which returns the *next* N+1 path), this returns
    the *latest existing* log file path so agents can inspect prepare_env output
    without having to know the file naming convention.
    """
    apply_dir = get_apply_dir(change)
    if not os.path.isdir(apply_dir):
        return ""
    safe_name = item_id.replace(".", "-")
    pattern = re.compile(rf"^{re.escape(safe_name)}-(\d+)\.log$")
    max_n = 0
    latest = ""
    for fname in os.listdir(apply_dir):
        m = pattern.match(fname)
        if m and int(m.group(1)) >= max_n:
            max_n = int(m.group(1))
            latest = os.path.join(apply_dir, fname)
    return latest


def _build_prepare_status(change, stage_name):
    """Return prepare_env status dict for stage.environment.prepare.

    Returns:
      {"status": "skipped", "log_path": "", "message": ""}    — change=None OR stage not required
      {"status": "ok",       "log_path": "<abs>", "message": ""}    — prepare_env 已完成
      {"status": "error",    "log_path": "<abs>", "message": "<stderr 摘要>"}  — 失败
    """
    skipped = {"status": "skipped", "log_path": "", "message": ""}
    if not change:
        return skipped
    stage_cfg = None
    for s in load_config().get("stages") or []:
        if s.get("name") == stage_name:
            stage_cfg = s
            break
    if not stage_cfg or not bool(
        (stage_cfg.get("environment") or {}).get("required", False)
    ):
        return skipped
    item_id = f"{stage_name}.prepare_env"
    log_path = _phase_log_path_latest(change, item_id)
    try:
        state = load_state(change)
        completed = state.get("completed_items", []) or []
    except Exception:
        completed = []
    if item_id in completed:
        return {"status": "ok", "log_path": log_path or "", "message": ""}
    msg = ""
    if log_path and os.path.isfile(log_path):
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            msg = content[-500:].strip()
        except Exception:
            msg = ""
    return {"status": "error", "log_path": log_path or "", "message": msg}


# ============================================================
# State management
# ============================================================


def load_state(change):
    path = get_state_path(change)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "version": 1,
        "change": change,
        "failed": False,
        "current": None,
    }


def save_state(state):
    path = get_state_path(state["change"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ============================================================
# Track config helpers
# ============================================================

SUB_PHASES = ["test", "dev", "verify", "gate", "simple"]
SUB_AGENTS = {
    "test": "pg-build/test",
    "dev": "pg-build/dev",
    "verify": "pg-build/verify",
    "gate": "pg-build/gate",
    "simple": "pg-build/simple",
}

# Per-sub allowed record status set. Enforced at cmd_record entry to prevent
# LLM from using the wrong status for the current sub-phase (e.g. calling
# `record pass` while sub=verify, which would silently advance to gate and
# mark the wrong tasks.md section complete — see _advance_from_gate).
ALLOWED_STATUS = {
    "test": {"completed", "failed"},
    "dev": {"completed", "failed"},
    "verify": {"completed", "escalate", "failed"},
    "fix": {"completed", "failed"},
    "fix-gate": {"completed", "failed"},
    "gate": {"pass", "fail"},
    "simple": {"completed", "failed"},
    "final-gate": {"pass", "fail"},
    # Env-hook phase items (prepare_env / clean_env) leave current.sub=None
    # because they are executed inline by _execute_phase rather than
    # dispatched to a sub-agent. The "phase" key is consulted only when
    # _is_env_hook_phase() short-circuits the guard; the values are also
    # referenced by the record/sub-forced-table in SKILL.md.
    "phase": {"completed", "failed"},
}

# Statuses that mean "this sub-phase is done, advance to the next sub".
ADVANCING_STATUSES = {"completed", "escalate", "pass"}
FIX_AGENT = "pg-build/fix"
FIX_GATE_AGENT = "pg-build/fix-gate"
FINAL_GATE_AGENT = "pg-build/gate"
SIMPLE_AGENT = "pg-build/simple"


def _is_env_hook_phase(state):
    """Return True iff current points to an env-hook phase item (prepare_env/clean_env).

    Env-hook phases (prepare_env / clean_env) are executed inline by
    `_execute_phase` rather than dispatched to a sub-agent. After a
    successful run, the runner returns `{action: "phase_result", ...}` and
    the orchestrator (LLM) is expected to call `record completed` to
    advance. At that point `state["current"]["sub"]` is None — which the
    `ALLOWED_STATUS` guard at line 3318 would otherwise reject.

    This helper is consulted at the top of `cmd_record` (Guard 0) to
    short-circuit the sub-status guard and route to `_handle_env_hook_record`.

    Args:
      state: loaded state dict (v1 schema — keys: `current`, `completed`).

    Returns:
      bool — True if `state["current"]` represents an env-hook phase.
    """
    cur = state.get("current") or {}
    if cur.get("sub") is not None:
        return False
    item = cur.get("item", "")
    bare = item.rsplit(".", 1)[-1] if "." in item else item
    return bare in ("prepare_env", "clean_env")


def _bare_track(qualified):
    """Strip stage prefix from qualified item name.
    'dev-isolated.backend' -> 'backend', 'real-integration' -> 'real-integration'
    """
    return qualified.rsplit(".", 1)[1] if "." in qualified else qualified


# Per-sub track field allowlist — each agent type only gets what it needs.
# v3.0 schema: a track references modules[] (resolved by _build_module_context)
# and binds to an environment (resolved by _build_stage_context). Sub-agents
# never see raw `root`/`port`/`rebuild_and_restart` fields — those are
# derived per-module / per-role by the helper functions below.
_SUB_TRACK_FIELDS = {
    "test": [
        "id",
        "review_level",
        "modules",
        "module_details",
        "stage",
        "module_roots",
        "module_names",
        "max_fix_retries",
        "fix_routing",
        "tasks_preformatted",
        "tasks_validation",
        "tasks_noop",
    ],
    "dev": [
        "id",
        "review_level",
        "modules",
        "module_details",
        "stage",
        "module_roots",
        "module_names",
        "max_fix_retries",
        "fix_routing",
        "tasks_preformatted",
        "tasks_validation",
        "tasks_noop",
    ],
    "verify": [
        "id",
        "review_level",
        "modules",
        "module_details",
        "stage",
        "module_roots",
        "module_names",
        "max_fix_retries",
        "fix_routing",
        "tasks_preformatted",
        "tasks_validation",
        "tasks_noop",
        "dispatch_seq",
        "report_seq",
    ],
    "fix": [
        "id",
        "review_level",
        "modules",
        "module_details",
        "stage",
        "module_roots",
        "module_names",
        "max_fix_retries",
        "fix_routing",
        "source_track",
        "source_phase",
        "design_doc_path",
        "tasks_path",
        "fix_cycle",
        "verify_report_path",
        "fix_report_filename",
        "dispatch_seq",
        "report_seq",
        "tasks_preformatted",
    ],
    "fix-gate": [
        "id",
        "review_level",
        "modules",
        "module_details",
        "stage",
        "module_roots",
        "module_names",
        "max_gate_fix_retries",
        "fix_routing",
        "source_track",
        "source_phase",
        "design_doc_path",
        "tasks_path",
        "fix_cycle",
        "gate_cycles",
        "cycles_remaining",
        "gate_report_path",
        "fix_report_filename",
        "dispatch_seq",
        "report_seq",
        "tasks_preformatted",
    ],
    "gate": [
        "id",
        "review_level",
        "modules",
        "module_details",
        "stage",
        "module_roots",
        "module_names",
        "max_fix_retries",
        "fix_routing",
        "tasks_preformatted",
        "dispatch_seq",
        "report_seq",
    ],
    "simple": [
        "id",
        "review_level",
        "label",
        "modules",
        "module_details",
        "module_roots",
        "module_names",
        "max_fix_retries",
        "fix_routing",
        "tasks_preformatted",
        "tasks_validation",
        "tasks_noop",
        "stage",
        "rollback_context",
        "track_type",
        "track_timeout",
        "track_on_failure",
        "commands_normalized",
        "dispatch_seq",
        "report_seq",
    ],
    "final-gate": [
        "_change",
        "proposal_path",
        "tasks_path",
        "design_doc_path",
        "design_doc_paths",
        "report_paths",
        "dispatch_seq",
        "report_seq",
        "tasks_preformatted",
    ],
}

# Subs that get tasks_validation / tasks_noop from _enrich_context_with_tasks
_TASKS_META_SUBS = {"test", "dev", "verify"}


def _track_meta(config, track_id):
    """Return the v3.0 track-level metadata (id / label / review_level / etc.).
    Uses bare track name for config lookup: 'dev-isolated.backend' -> 'backend'."""
    bare = _bare_track(track_id)
    tc = get_track_config(config, track_id) or {}
    return {
        "id": track_id,
        "label": tc.get("label", bare),
        "review_level": tc.get("review_level", "none"),
        "modules": list(tc.get("modules") or []),
        "max_fix_retries": tc.get("max_fix_retries", 5),
        "fix_routing": tc.get("fix_routing", "source"),
        "description": tc.get("description", ""),
    }


# ============================================================
# Prompt template renderer (Jinja-compatible syntax, stdlib-only)
# ============================================================
#
# A minimal regex-based renderer that supports the subset of Jinja syntax
# actually used by the prompt templates in this file (and SKILL.md reference):
#
#   {{var}}                       — value lookup (dotted paths allowed)
#   {{context.field.sub}}         — dotted lookup with "context." prefix fallback
#                                  to top-level ctx key (LLM templates historically
#                                  prefix everything with "context." but the
#                                  flat ctx dict stores them at top level)
#   {{var | filter(arg=N)}}        — filter; "tojson(indent=N)" / "toyaml" supported
#   {#if cond}...{/if}            — conditional block (cond: truthy expr,
#                                  "X in [...]" membership, "this.X" loop var)
#   {#each list}...{/each}        — loop block; binds 'this' to each item
#
# Missing values render as empty string (not template literal), so LLMs never
# see unfilled placeholders in the final prompt.
#
# Pure stdlib (re + json), no jinja2 dependency.

import json as _json_prompt
import re as _re_prompt

import yaml as _yaml_prompt

_VAR_RE = _re_prompt.compile(r"\{\{([^{}]+?)\}\}")
_BLOCK_RE = _re_prompt.compile(
    r"\{#(each|if)\s+([^}]+?)\}(.*?)\{/\1\}", _re_prompt.DOTALL
)


def _walk(d, path):
    cur = d
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def _resolve_dotted(ctx, dotted):
    """Resolve a dotted path. Special-case 'context.X' → fallback to top-level X."""
    if dotted.startswith("context."):
        path = dotted[len("context.") :]
        if isinstance(ctx.get("context"), dict):
            v = _walk(ctx["context"], path)
            if v is not None:
                return v
        return _walk(ctx, path)
    return _walk(ctx, dotted)


def _eval_condition(cond, ctx):
    cond = cond.strip()
    m = _re_prompt.match(r"(\w+)\s+in\s+\[(.*?)\]", cond)
    if m:
        var = m.group(1)
        items = [s.strip().strip('"').strip("'") for s in m.group(2).split(",")]
        return ctx.get(var) in items
    # For 'this.X.Y.Z' we use resolve_dotted which walks the dotted path
    # via ctx["this"] (so all `this.X` lookups are scoped to the loop var).
    return bool(_resolve_dotted(ctx, cond))


def _sub_vars(text, ctx):
    def repl(m):
        expr = m.group(1).strip()
        if "|" in expr:
            name, filt = expr.split("|", 1)
            name = name.strip()
            value = _resolve_dotted(ctx, name)
            filt = filt.strip()
            if filt.startswith("tojson"):
                mm = _re_prompt.search(r"indent=(\d+)", filt)
                indent = int(mm.group(1)) if mm else 2
                if value is None:
                    return "null"
                return _json_prompt.dumps(value, indent=indent, ensure_ascii=False)
            elif filt == "toyaml":
                if value is None:
                    return "null"
                # PyYAML 3.13 不支持 sort_keys=False 关键字（该参数在
                # 5.1+ 才加入）；此版本默认按 key 字母序 dump，对
                # LLM 阅读无影响（层级结构才是关键，字段顺序无关）。
                return _yaml_prompt.safe_dump(
                    value,
                    allow_unicode=True,
                    default_flow_style=False,
                    width=200,
                )
        else:
            value = _resolve_dotted(ctx, expr)
            if value is None:
                return ""
        return str(value)

    return _VAR_RE.sub(repl, text)


def _render_prompt_template(template, ctx):
    """Render a Jinja-style prompt template against ctx.

    Recursively expands {#if} / {#each} blocks and substitutes {{var}} /
    {{var|filter}} expressions. Loop block binds `this` to each item dict.
    """
    out = []
    i = 0
    while i < len(template):
        m = _BLOCK_RE.search(template, i)
        if not m:
            out.append(_sub_vars(template[i:], ctx))
            break
        out.append(_sub_vars(template[i : m.start()], ctx))
        kind = m.group(1)
        cond_or_list = m.group(2)
        body = m.group(3)
        if kind == "if":
            if _eval_condition(cond_or_list, ctx):
                out.append(_render_prompt_template(body, ctx))
        elif kind == "each":
            items = _resolve_dotted(ctx, cond_or_list) or []
            for item in items:
                inner = dict(ctx)
                inner["this"] = item
                out.append(_render_prompt_template(body, inner))
        i = m.end()
    return "".join(out)


# ============================================================
# Prompt templates (Jinja-compatible syntax)
# ============================================================
#
# Each sub-agent type has its own template. Templates use {{var}}, {#if},
# {#each}, {this.X} syntax. Renderer is _render_prompt_template (stdlib-only).
# These are the single source of truth (SSOT) for what sub-agent prompts look
# like. The runner does the actual rendering AND writes the rendered+merged
# content to a dispatch file under 2-build/ — so the LLM orchestrator simply
# forwards the dispatch_file path to the sub-agent, never sees the prompt
# content, and cannot accidentally rewrite it.

_PROMPT_TEMPLATE_BASE = """\
## 任务：{{context.id}} - {{context.label}}

### 变更名称
{{context._change}}

### Track 配置
- track.id: {{context.id}}
- track.review_level: {{context.review_level}}
- track.modules: {{context.modules}}
- track.max_fix_retries: {{context.max_fix_retries}}
- track.fix_routing: {{context.fix_routing}}

### Module 配置
{#each context.module_details}
- module: {{this.name}}
  - root: {{this.root}}
  - language: {{this.language}}
  - build: {{this.build}}
  - lint: {{this.lint}}
  - test.unit: {{this.test.unit}}
  - test.integration: {{this.test.integration}}
  {#if this.test.e2e}- test.e2e: {{this.test.e2e}}{/if}
{/each}

### Stage 配置
- stage.name: {{context.stage.name}}
- stage.test_key: {{context.stage.test_key}}  # unit / integration / e2e
- stage.gate: {{context.stage.gate}}  # all_pass / any_pass / no_gate
- stage.environment.required: {{context.stage.environment.required}}
- stage.environment.prepare.status: {{context.stage.environment.prepare.status}}
- stage.environment.prepare.log_path: {{context.stage.environment.prepare.log_path}}
- stage.environment.prepare.message: {{context.stage.environment.prepare.message}}
- stage.environment.name: {{context.stage.environment.name}}
{#if context.stage.environment.instances}
- stage.environment.instances:
```yaml
{{context.stage.environment.instances | toyaml}}
```
  每个 instance 是 project.yaml 原样 dict，包含 name/host/(可选)port/(可选)libvirt_uri。
{/if}
- stage.test_commands: {{context.stage.test_commands}}

{{context.sub_specific_block}}

### 运行时环境查询

如需在运行时查询 prepare_env 状态，用 runner 子命令（避免硬编码日志路径）：

```bash
python3 .opencode/skills/pg-build/scripts/pg-pipeline-runner.py prepare-env-status {{context._change}} [stage_name]
```

服务启停由 LLM 自行判断时机：runner 不替你启停任何 role 服务。

### 产物路径
- proposal: .pg/changes/{{context._change}}/proposal.md
- design: .pg/changes/{{context._change}}/design.md
- tasks: .pg/changes/{{context._change}}/tasks.md

### 模块路径约束（硬约束）

本 track 只允许修改以下模块根目录：
{{context.module_roots | toyaml}}
track 名称 `{{context.id}}` 拥有模块：{{context.module_names}}，各模块根目录已去重合并。

硬规则：
1. **只能**在 {{context.module_roots}} + `.pg/` 下创建/修改文件
2. 写入其他模块目录（如本 track 是 `backend` 时写入 `<other-module-dir>/`）或项目根目录 → 严重违规
3. `real-integration` track（modules=[]）跳过此约束

### 执行要求

执行 {{context.id}} 阶段，任务如下：

{#each context.tasks_preformatted}
{{this}}
{/each}

**验证要求**：
{{context.tasks_validation}}

{#if context.tasks_noop}
（此 sub 的任务是 noop，跳过任务执行。）
{/if}

### 返回格式

- summary: 一句话总结执行结果
- outputs: 产物文件列表（具体文件名）
- tasks_updated: 是否已更新 tasks.md 复选框（true/false）
- status: SUCCESS / FAILED

**build-r Step 5 标记任务完成的方式**：

⚠️ 标记任务完成的正确方式:
   python3 .opencode/skills/pg-build/scripts/pg_pipeline_state_v2.py mark-task \\
     {{context._change}} {{context.id}} {{context.sub}} <task_id>

   （其中 task_id 是 X.Y 中的 Y，例如 `- [ ] 1.1 编写单元测试` 对应 task_id=1）

⚠️ 禁止直接 Edit tasks.md（lint 会在 CI 拒绝合并不带 mark-task 的 checkbox 改动）。

⚠️ 你可以在 `outputs` 字段中传 `task 1.1, task 2.3` 这样的描述，runner 会自动调 mark-task。

{#if context.rollback_context}
[ROLLBACK CONTEXT]
- failed_at: {{context.rollback_context.failed_at}}
- reason: {{context.rollback_context.reason}}
- source: {{context.rollback_context.source}}

你必须优先审查该根因是否已修复，再执行本阶段的正常任务。
{/if}

{#if context._duplicate_dispatch}
> **⚠️ 重复派遣检测：当前 sub-phase（{{context.sub}}）已被连续派遣两次**
>
> {{context._duplicate_dispatch_tip}}
{/if}
"""

_PROMPT_BLOCK_TEST = """\
### 测试要求

TDD 红 Phase：本阶段只写测试代码，绝不创建或修改任何生产代码。
运行 `{{context.stage.test_commands.0}}` 后预期结果是编译失败
（找不到符号/类/方法/模块）。任何测试通过都视为 TDD_VIOLATION。
"""

_PROMPT_BLOCK_DEV = """\
### Hooks 调用约定 (LLM 触发 role action 的唯一入口)
runner **不**预渲染 cmd 字典；LLM 通过 `runner invoke-hook` CLI 触发 hook，
runner 内部从 project.yaml 反查 action 元数据、拼 spec、调 pg-run-hook.py。

**必填参数**：`--session` `--env` `--role` `--instance` `--action`
**可选参数**：`--stage` (默认 manual) `--tail-lines` (仅 logs/tail 生效)

```yaml
{{context.stage.environment.hooks | toyaml}}
```

调用示例：
```bash
# 启动 backend (runner 自动读 actions.backend.start.timeout_seconds=300)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action start \\
  --skill pg-build

# 看 100 行日志 (runner 把 --tail-lines 100 追加到 hook args 末尾)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action logs \\
  --tail-lines 100 --skill pg-build
```

**重要不变量**：
- `timeout_seconds` 是 INFORMATION（来自 project.yaml 的 `actions.<action>.timeout_seconds`）。
  LLM **不**传 `--timeout` flag（不存在）；runner 内部读取并通过 `pg-run-hook.py` 强制执行。
- `--host` / `--port` 也不是 CLI flag；runner 从 `environment.instances[role][].host` 自动反查。
- LLM **不**自己拼 spec / 不解析 PG_* env vars / 不算 log_path；这些都由 runner 完成。
"""

_PROMPT_BLOCK_VERIFY = """\
### Hooks 调用约定 (LLM 触发 role action 的唯一入口)
runner **不**预渲染 cmd 字典；LLM 通过 `runner invoke-hook` CLI 触发 hook，
runner 内部从 project.yaml 反查 action 元数据、拼 spec、调 pg-run-hook.py。

**必填参数**：`--session` `--env` `--role` `--instance` `--action`
**可选参数**：`--stage` (默认 manual) `--tail-lines` (仅 logs/tail 生效)

```yaml
{{context.stage.environment.hooks | toyaml}}
```

调用示例：
```bash
# 启动 backend (runner 自动读 actions.backend.start.timeout_seconds=300)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action start \\
  --skill pg-build

# 看 100 行日志 (runner 把 --tail-lines 100 追加到 hook args 末尾)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action logs \\
  --tail-lines 100 --skill pg-build
```

**重要不变量**：
- `timeout_seconds` 是 INFORMATION（来自 project.yaml 的 `actions.<action>.timeout_seconds`）。
  LLM **不**传 `--timeout` flag（不存在）；runner 内部读取并通过 `pg-run-hook.py` 强制执行。
- `--host` / `--port` 也不是 CLI flag；runner 从 `environment.instances[role][].host` 自动反查。
- LLM **不**自己拼 spec / 不解析 PG_* env vars / 不算 log_path；这些都由 runner 完成。

### 写盘要求
**完成后用 `cat >` 自行写盘**到
`2-build/{{context.report_seq}}-{{context.id}}-verify.md`，
包含每个 V-* 项的原始证据。

> 关于 seq 编号：dispatch 文件 (`{{context.dispatch_seq}}`) 与本报告
> (`{{context.report_seq}}`) 共享全局递增序列；本报告的 seq 由
> runner 预分配，禁止更改。
"""

_PROMPT_BLOCK_GATE = """\
### Gate 审计要求
- `stage.gate` 已写入 Track 配置（见上）。
- **只读不写**源码；**完成后用 `cat >` 自行写盘**到
  `2-build/{{context.report_seq}}-{{context.id}}-gate-verify.md`，
  不要把 markdown 全文塞进返回里。
- 按 design.md 列 P-N 审计项逐项核对 evidence。

> 关于 seq 编号：dispatch 文件 (`{{context.dispatch_seq}}`) 与本报告
> (`{{context.report_seq}}`) 共享全局递增序列；本报告的 seq 由
> runner 预分配，禁止更改。
"""

_PROMPT_BLOCK_FIX = """\
### Hooks 调用约定 (LLM 触发 role action 的唯一入口)
runner **不**预渲染 cmd 字典；LLM 通过 `runner invoke-hook` CLI 触发 hook，
runner 内部从 project.yaml 反查 action 元数据、拼 spec、调 pg-run-hook.py。

**必填参数**：`--session` `--env` `--role` `--instance` `--action`
**可选参数**：`--stage` (默认 manual) `--tail-lines` (仅 logs/tail 生效)

```yaml
{{context.stage.environment.hooks | toyaml}}
```

调用示例：
```bash
# 启动 backend (runner 自动读 actions.backend.start.timeout_seconds=300)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action start \\
  --skill pg-build

# 看 100 行日志
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action logs \\
  --tail-lines 100 --skill pg-build
```

**重要不变量**：
- `timeout_seconds` 是 INFORMATION（来自 project.yaml）。
  LLM **不**传 `--timeout` flag（不存在）；runner 内部读取并通过 `pg-run-hook.py` 强制执行。
- `--host` / `--port` 也不是 CLI flag；runner 从 `environment.instances[role][].host` 自动反查。

### 必读源报告（verify ESCALATE 派发）

- **源 verify 报告**: `{{context.verify_report_path}}`

请用 Read 工具**逐字**读取该文件。报告包含 verify agent 记录的
ESCALATE Issue 详情、失败证据（HTTP 响应 / 日志片段 / stack trace）、
V-* 验证项的逐项结果等**完整上下文**。runner **不**对报告做结构化抽取，
所有修复决策必须基于报告原文。

- change_name: {{context._change}}
- source_track: {{context.source_track}}
- source_phase: {{context.source_phase}}
- design_doc_path: {{context.design_doc_path}}
- tasks_path: {{context.tasks_path}}

fix_cycle: {{context.fix_cycle}} / {{context.max_fix_retries}}

**修复后必跑流程**（fix agent 必须自检通过才能返回 SUCCESS）：

1. 修改源码
2. 跑 `{{context.stage.test_commands.0}}` 单元测试（必须通过）
3. 跑模块 lint（必须 0 警告）
4. 启动 `runner invoke-hook --action start` 服务（如需）
5. 跑 tasks.md verify 章节的所有 V-* 验证项（curl 等）
6. 抓 `runner invoke-hook --action logs --tail-lines 100` 日志确认无 ERROR
7. 停止 `runner invoke-hook --action stop` 服务（如启动过）
8. 用 `cat > 2-build/{{context.report_seq}}-{{context.id}}-fix-verify-{{context.fix_cycle}}.md << 'EOF' ... EOF` 自行写盘

> 关于 seq 编号：dispatch 文件 (`{{context.dispatch_seq}}`) 与本报告
> (`{{context.report_seq}}`) 共享全局递增序列；本报告的 seq 由
> runner 预分配，禁止更改。fix_cycle 嵌入文件名以区分多次循环的修复记录。

返回格式同 base dispatch（summary / outputs / tasks_updated / status）。
"""


_PROMPT_BLOCK_FIX_GATE = """\
### Hooks 调用约定 (LLM 触发 role action 的唯一入口)
runner **不**预渲染 cmd 字典；LLM 通过 `runner invoke-hook` CLI 触发 hook，
runner 内部从 project.yaml 反查 action 元数据、拼 spec、调 pg-run-hook.py。

**必填参数**：`--session` `--env` `--role` `--instance` `--action`
**可选参数**：`--stage` (默认 manual) `--tail-lines` (仅 logs/tail 生效)

```yaml
{{context.stage.environment.hooks | toyaml}}
```

调用示例：
```bash
# 启动 backend (runner 自动读 actions.backend.start.timeout_seconds=300)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action start \\
  --skill pg-build

# 看 100 行日志
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action logs \\
  --tail-lines 100 --skill pg-build
```

**重要不变量**：
- `timeout_seconds` 是 INFORMATION（来自 project.yaml）。
  LLM **不**传 `--timeout` flag（不存在）；runner 内部读取并通过 `pg-run-hook.py` 强制执行。
- `--host` / `--port` 也不是 CLI flag；runner 从 `environment.instances[role][].host` 自动反查。

### 必读源报告（gate FAIL 派发）

- **源 gate 报告**: `{{context.gate_report_path}}`

请用 Read 工具**逐字**读取该文件。报告包含 gate agent 记录的
`### {track}:G-N` 章节（每个 gap 形如 `### backend:G-1 — {gap title}`）、
审计证据（design.md P-N 审计项 vs 实现）、PASS/FAIL 判定依据等**完整上下文**。
runner **不**对报告做结构化抽取（不解析 G-N 块、不提取 gate_gap_id / file_pos / fix_hint 等字段），
所有修复决策必须基于报告原文——同一份报告可能含多个 G-N gap，fix-gate agent
需要自行通读整份 gate 报告、识别**全部**未修复的 gap 一次性修复。

- change_name: {{context._change}}
- source_track: {{context.source_track}}
- source_phase: {{context.source_phase}}
- design_doc_path: {{context.design_doc_path}}
- tasks_path: {{context.tasks_path}}

fix_cycle: {{context.fix_cycle}} / {{context.max_gate_fix_retries}}
cycles_remaining: {{context.cycles_remaining}}

**修复后必跑流程**（fix-gate agent 必须自检通过才能返回 SUCCESS）：

1. 修改源码
2. 跑 `{{context.stage.test_commands.0}}` 单元测试（必须通过）
3. 跑模块 lint（必须 0 警告）
4. 启动 `runner invoke-hook --action start` 服务（如需）
5. 跑 design.md 中 P-N 审计项对应的验证项（curl 等）—— 自行从 gate 报告章节中识别待审计项
6. 抓 `runner invoke-hook --action logs --tail-lines 100` 日志确认无 ERROR
7. 停止 `runner invoke-hook --action stop` 服务（如启动过）
8. 用 `cat > 2-build/{{context.report_seq}}-{{context.id}}-fix-gate-verify-{{context.fix_cycle}}.md << 'EOF' ... EOF` 自行写盘

> 关于 seq 编号：dispatch 文件 (`{{context.dispatch_seq}}`) 与本报告
> (`{{context.report_seq}}`) 共享全局递增序列；本报告的 seq 由
> runner 预分配，禁止更改。fix_cycle 嵌入文件名以区分多次循环的修复记录。

返回格式同 base dispatch（summary / outputs / tasks_updated / status）。
"""

_PROMPT_BLOCK_SIMPLE = """\
### Simple Track 命令执行要求

你是 simple track 命令执行 agent。SSOT 是 `tracks.{{context.id}}.commands` 列表（已标准化为 `commands_normalized`），**不要**读 tasks.md（其章节已被 runner 改写为 noop form，无信息量）。

#### Track 配置
- track.id: {{context.id}}
- track.type: {{context.track_type}}
- track.label: {{context.label}}
- track.timeout_seconds: {{context.track_timeout}}        # 全局默认
- track.on_failure: {{context.track_on_failure}}          # fail / continue_all

#### 待执行命令（顺序执行，逐条决策）

{#each context.commands_normalized}
**Command #{{this.idx}}**  (timeout={{this.timeout_seconds}}s, on_failure={{this.on_failure}}, retry_max={{this.retry_max}})
```bash
{{this.cmd}}
```
{#if this.is_retry}- 失败后自动重试最多 {{this.retry_max}} 次，每次 timeout {{this.retry_timeout_seconds}}s；仍失败按 track.on_failure 处理{/if}
{#if this.is_continue}- 失败时记 warning 继续下一条{/if}
{#if this.is_fail}- 失败时立即返回 status=FAILED 终止 track{/if}
{/each}

#### 失败处理决策表

| per-cmd on_failure | 单条行为 | track.on_failure=fail 时 | track.on_failure=continue_all 时 |
|---|---|---|---|
| `fail` (默认) | 失败即终止 | workflow_failed | warning + 继续 |
| `continue` | 失败 warning 后继续 | 继续下一条 | 继续下一条 |
| `retry` | 重试 retry_max 次再判定 | workflow_failed | warning + 继续 |

**重要**：track.on_failure=continue_all **仅在 runner record 阶段**生效——你本人直接返回
status=SUCCESS 或 status=FAILED，由 runner 根据 track.on_failure 决定后续动作。

{#if context.stage.environment}
#### 环境与 Hooks 调用约定

LLM 自行判断是否需要启动服务；runner 不替你启停。

- env.name: {{context.stage.environment.name}}
- env.hooks:

```yaml
{{context.stage.environment.hooks | toyaml}}
```

```bash
# 启动 backend (runner 自动从 action_metadata 读 timeout_seconds)
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action start \\
  --skill pg-build

# 看 100 行日志
python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook \\
  --session {{context._change}} --env {{context.stage.environment.name}} --role backend --instance backend-1 --action logs \\
  --tail-lines 100 --skill pg-build
```
{/if}

#### 必跑流程

1. 依次执行上面 **Command #1..#N** 列表
2. 对每条命令：
   a. （可选）环境准备：缺依赖时 `apt install` / `pip install` / `npm install -g` 等
   b. 用 `bash -c '<cmd>'` 执行（runner 在编排器侧用 `timeout N` 包裹时遵守）
   c. 失败时按决策表处理
3. 全部完成或按决策表终止后：用 `cat > 2-build/{{context.report_seq}}-{{context.id}}-simple-verify.md <<'EOF' ... EOF` 写执行报告
   （包含每条命令的摘要：cmd / 退出码 / stdout 末尾 ~50 行 / stderr 末尾 ~50 行 / 耗时）
4. 返回结果

#### 返回格式

- summary: 一句话总结（如 "执行 3/3 条命令成功" 或 "Command #2 失败: <err>，按 on_failure=fail 终止"）
- outputs: 产物文件列表（如 `2-build/{{context.report_seq}}-{{context.id}}-simple-verify.md`）
- tasks_updated: false（simple track 不更新 tasks.md 复选框）
- status: SUCCESS / FAILED

**红线**：
1. 禁止加载 pg-build / pg-propose 等 SKILL
2. 禁止修改 tasks.md / proposal.md / design.md
3. 禁止修改源码（simple track 不属于任何 module；如命令产生文件输出，那是 commands 自己的事）
4. 失败时**必须**先尝试自动修复（缺依赖、命令拼写错误等）再返回 FAILED
"""

_PROMPT_TEMPLATE_FINAL_GATE = """\
## 任务：Final Gate — 跨 track 依赖审查

### 变更名称
{{context._change}}

### Track 配置
- track.id: final（特殊标记，runner 内部 marker，不在 config.yaml 中）
- track.review_level: standard

### 产物路径
- proposal: {{context.proposal_path}}
- tasks: {{context.tasks_path}}
- design_doc_path（首个）: {{context.design_doc_path}}
- design_doc_paths:
{{context.design_doc_paths | toyaml}}
- report_paths:
{{context.report_paths | toyaml}}

### 必读上下文清单

final-gate agent 必须读取以下 4 类文件才能做完整审计：

1. **所有 design.md**（`context.design_doc_paths`）—— 找 🆕 标记的跨 track 验证项
2. **所有 track 的 gate assessment 报告**（`context.report_paths`）—— 路径模式 `2-build/{track.id}-{N}-gate-assessment.md`
3. **context-chain.md**（`.pg/changes/{{context._change}}/2-build/context-chain.md`）—— 了解 sub-agent 执行历史与已知问题
4. **2-build/known-issues.md**（如存在）—— 累积的 gate-fix 兜底问题

### 执行要求

**🆕 标记语义**：design.md 中以 `🆕` 开头的验证项表示**跨 track 依赖**（如「V-backend-1 → frontend 必须能用」）。每个 🆕 项必须找到至少一个其他 track 的 gate-assessment.md 证明已实现。

**审计步骤**：

1. 遍历所有 `context.design_doc_paths`，提取所有 🆕 标记的跨 track 验证项
2. 对每条 🆕 项，确认目标 track 的 `gate-assessment.md` 里有对应实现证据
3. 检查所有 `context.report_paths` 都是 PASS 状态
4. 检查 `context-chain.md` 没有未解决的 error
5. 列出跨 track 不一致 / 缺失项（如有）

**写盘要求（必须）**：完成所有审计后，用 `cat > .pg/changes/{{context._change}}/2-build/{{context.report_seq}}-final-gate-gate-verify.md << 'EOF' ... EOF` 自行写盘。**不要**把 markdown 全文塞进返回里——编排器不会替你落盘。

> 关于 seq 编号：dispatch 文件 (`{{context.dispatch_seq}}`) 与本报告
> (`{{context.report_seq}}`) 共享全局递增序列；本报告的 seq 由
> runner 预分配，禁止更改。

### 返回格式

- summary: 一句话总结整体判定（PASS / FAIL）
- **不要**返回 markdown 全文（已落盘到 `{report_seq}-final-gate-gate-verify.md`）
"""


def _build_prompt_template(item_id, sub):
    """Return the prompt template string for this (item_id, sub) pair.

    item_id may be 'final-gate' (special-cased) or any track id matching a
    track in config.yaml (frontend / backend / agent / etc.).
    """
    if item_id == "final-gate":
        return _PROMPT_TEMPLATE_FINAL_GATE

    sub_blocks = {
        "test": _PROMPT_BLOCK_TEST,
        "dev": _PROMPT_BLOCK_DEV,
        "verify": _PROMPT_BLOCK_VERIFY,
        "gate": _PROMPT_BLOCK_GATE,
        "fix": _PROMPT_BLOCK_FIX,
        "fix-gate": _PROMPT_BLOCK_FIX_GATE,
        "simple": _PROMPT_BLOCK_SIMPLE,
    }
    block = sub_blocks.get(sub, "")
    return _PROMPT_TEMPLATE_BASE.replace("{{context.sub_specific_block}}", block)


def _render_role_action(
    act_cfg, *, role, instance_name, instance_host, change, stage_name, env_name
):
    """Pre-render a role action (start / stop / logs / tail) as a complete
    pg-run-hook.py invocation.

    The returned dict's `cmd` field is a heredoc-style bash command. Sub-agents
    invoke it via `bash {cmd}` (no further assembly needed). All PG_* env
    variables that hook scripts depend on (PG_CHANGE_NAME / PG_ENV / PG_ROLE /
    PG_INSTANCE_NAME / PG_INSTANCE_HOST / PG_SKILLS_PATH / PG_PROJECT_ROOT)
    are baked into the spec, so the LLM cannot accidentally omit them.

    Args:
        act_cfg: the action config (script / args / timeout_seconds / etc.)
        role: role name (backend / frontend / agent)
        instance_name: target instance name (e.g. backend-1)
        instance_host: target instance host (e.g. localhost)
        change: change name; injected as PG_CHANGE_NAME
        stage_name: stage name; injected as PG_STAGE
        env_name: environment name; injected as PG_ENV

    Returns:
        dict with keys: host, cmd, timeout_seconds, hook_type, description.
        Sub-agents use `cmd` directly; the other fields are for context.
    """
    script = act_cfg.get("script")
    args = act_cfg.get("args") or []
    timeout = act_cfg.get("timeout_seconds")

    # Template substitution: {role} / {instance.name} / {instance.host} /
    # {lines:100} etc.
    rendered_args = []
    for raw in args:
        a = str(raw)
        a = a.replace("{role}", role)
        a = a.replace("{instance.name}", instance_name)
        a = a.replace("{instance.host}", instance_host)
        rendered_args.append(a)

    inner_cmd = (
        "bash "
        + shlex.quote(script)
        + (
            " " + " ".join(shlex.quote(a) for a in rendered_args)
            if rendered_args
            else ""
        )
    )

    # log_path: prefer runner-side path; hook scripts read it from $LOG_DIR
    # (parent dir of $BACKEND_LOG / $FRONTEND_LOG / etc.). Use
    # 2-build/<env>/logs for log aggregation, matching the env hooks.
    # pg-build keeps legacy .pg/changes/<change>/2-build/<env>/logs path.
    log_dir_abs = _pg_log_dir_for_skill("pg-build", change, env_name)
    log_name = f"role.{role}.{act_cfg.get('name', 'action')}@{instance_name}.log"
    log_path = os.path.join(log_dir_abs, log_name)

    spec = {
        "cmd": inner_cmd,
        "change": change,
        "stage": stage_name,
        "env": env_name,
        "role": role,
        "instance_name": instance_name,
        "instance_host": instance_host,
        "hook_type": act_cfg.get("name", ""),
        "timeout_seconds": timeout,
        "log_path": log_path,
        "skill": "pg-build",
    }
    cmd = (
        f"python3 {shlex.quote(PG_HOOK_RUNNER)}"
        f" <<'EOF'\n{json.dumps(spec, indent=2)}\nEOF"
    )

    return {
        "host": instance_host or act_cfg.get("host", "localhost"),
        "cmd": cmd,
        "timeout_seconds": timeout,
        "hook_type": act_cfg.get("name", ""),
        "description": act_cfg.get("description"),
    }


def _build_module_context(config, modules):
    """Resolve modules[] from v3.0 schema into per-module context dicts.

    Each entry carries: name, root, language, timeout_seconds, build, lint,
    test.{unit,integration,e2e}. All build/lint/test.<key> values are
    pre-rendered as `timeout N bash -c '<cmd>'` strings so sub-agents see a
    plain shell command and the timeout is enforced by GNU `timeout` rather
    than relying on the LLM agent's own timeout.

    Missing keys are left out (not blank-filled) so the agent sees the SSOT
    shape.
    """
    from pg_pipeline_common import normalize_module_command, render_module_command

    out = []
    for mod_name in modules or []:
        mod = (config.get("modules") or {}).get(mod_name) or {}
        entry = {"name": mod_name}
        for k in ("root", "language", "review_level"):
            if k in mod:
                entry[k] = mod[k]
        if "timeout_seconds" in mod:
            entry["timeout_seconds"] = mod["timeout_seconds"]
        module_default = mod.get("timeout_seconds")

        for cmd_key in ("build", "lint"):
            if cmd_key in mod and mod[cmd_key]:
                normalized = normalize_module_command(mod[cmd_key], module_default)
                entry[cmd_key] = render_module_command(normalized)

        if "test" in mod and isinstance(mod["test"], dict):
            tests = {}
            for tk, tv in mod["test"].items():
                if not tv:
                    continue
                normalized = normalize_module_command(tv, module_default)
                tests[tk] = render_module_command(normalized)
            if tests:
                entry["test"] = tests
        out.append(entry)
    return out


def _build_stage_context(config, item, change=None):
    """Resolve the v3.0 stage that owns this track item.

    `item` can be qualified (dev-isolated.backend) or bare (backend).
    Uses qualified name to find the correct stage; falls back to bare name
    for backward compatibility.

    Returns a dict with: name, test_key, gate, test_commands,
    environment.{required, prepare.{status, log_path, message}, name,
    instances, actions}. Falls back to safe defaults if the item is not
    bound to any stage (e.g. final-gate).
    """
    stage_name, stage = _find_stage_for_track(config, item)
    if not stage:
        bare = _bare_track(item)
        if bare != item:
            stage_name, stage = _find_stage_for_track(config, bare)
    if not stage:
        return {
            "name": None,
            "test_key": "unit",
            "gate": "all_pass",
            "test_commands": [],
            "environment": {
                "required": True,
                "prepare": {"status": "skipped", "log_path": "", "message": ""},
                "name": None,
                "instances": None,
                "actions": None,
            },
        }

    requires_environment = bool((stage.get("environment") or {}).get("required", True))
    test_key = stage.get("test_key", "unit")
    track_cfg = get_track_config(config, item) or {}
    test_commands = _resolve_test_commands(config, track_cfg, test_key)

    # Environment resolution priority:
    #   1. environment.yaml (per-change decision; SSOT) — when change is given
    #   2. config.yaml `track.environment` — deprecated fallback (no SSOT)
    # env_name is resolved by _resolve_stage_env which raises on error.
    env_name = None
    if change:
        if requires_environment:
            env_name = _resolve_stage_env(change, stage_name)
        else:
            env_name = "__skip__"
    elif requires_environment:
        env_name = track_cfg.get("environment")
    prepare_status = _build_prepare_status(change, stage_name)
    if env_name == "__skip__":
        return {
            "name": stage.get("name"),
            "test_key": test_key,
            "gate": stage.get("gate", "all_pass"),
            "test_commands": test_commands,
            "environment": {
                "required": requires_environment,
                "prepare": prepare_status,
                "name": "__skip__",
                "instances": None,
                "actions": None,
            },
        }

    hooks_payload = None
    env_summary = None
    if requires_environment and env_name:
        env_cfg = (config.get("environments") or {}).get(env_name) or {}
        instances = {}
        action_metadata = {}
        for role_name, role_cfg in env_cfg.get("roles", {}).items():
            # Pass-through: copy each instance dict as-is so LLM sees every
            # schema-allowed field (name/host/port/libvirt_uri/...).
            instances[role_name] = [
                dict(inst) for inst in (role_cfg.get("instances") or [])
            ]
            # Per-action metadata: action name -> timeout + description.
            # timeout_seconds is INFORMATION only (LLM does not pass it to
            # invoke-hook); runner reads it from project.yaml at call time.
            for act_name, act_cfg in (role_cfg.get("actions") or {}).items():
                meta = {}
                if "timeout_seconds" in act_cfg:
                    meta["timeout_seconds"] = act_cfg["timeout_seconds"]
                if act_cfg.get("description"):
                    meta["description"] = act_cfg["description"]
                action_metadata.setdefault(role_name, {})[act_name] = meta
        env_summary = {"name": env_name, "instances": instances}

        # invoke-hook CLI template — the only LLM-facing entry for triggering
        # role actions. timeout_seconds is NOT exposed as a flag; LLM only
        # learns it via action_metadata above.
        #
        # 历史: v3.1 之前, 该模板指向 pg-pipeline-runner.py invoke-hook 子命令.
        # v3.2 抽出到 runtime 层独立 CLI pg-invoke-hook.py 后, 这里改为新路径.
        # pg-pipeline-runner.py 仍保留同名子命令 (thin wrapper) 保持向后兼容,
        # 但所有 LLM 面向的 prompt template / SKILL.md 都用新路径.
        command_template = (
            "python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py "
            "invoke-hook "
            "--session <SESSION> --env <ENV> --role <ROLE> "
            "--instance <INSTANCE> --action <ACTION> "
            "[--stage <STAGE>] [--tail-lines <N>] [--skill <SKILL>]"
        )
        hooks_payload = {
            "supported_actions": sorted(
                {act for r_meta in action_metadata.values() for act in r_meta.keys()}
            ),
            "action_metadata": action_metadata,
            "invocation": {
                "command_template": command_template,
                "required_args": [
                    "--session",
                    "--env",
                    "--role",
                    "--instance",
                    "--action",
                ],
                "optional_args": ["--stage", "--tail-lines"],
                "notes": [
                    "timeout_seconds is INFORMATION (read from project.yaml "
                    "via action_metadata). LLM does NOT pass it.",
                    "--tail-lines only applies to --action logs|tail; runner "
                    "appends it to the hook's args list as the last two "
                    "elements.",
                    "host / port are NOT CLI flags; runner resolves them "
                    "from instances[] in project.yaml by instance name.",
                ],
            },
        }

    return {
        "name": stage.get("name"),
        "test_key": test_key,
        "gate": stage.get("gate", "all_pass"),
        "test_commands": test_commands,
        "environment": {
            "required": requires_environment,
            "prepare": prepare_status,
            "name": env_name,
            "instances": env_summary["instances"] if env_summary else None,
            "hooks": hooks_payload,
        },
    }


def _resolve_stage_env(change, stage_name):
    manifest = _read_manifest(change)
    for stage in manifest.get("stages", []):
        if stage["name"] == stage_name:
            return stage.get("environment", "__skip__")
    return "__skip__"


def filter_track_context(config, track_id, sub=None, change=None):
    """Return the v3.0 track context that the given sub-agent type needs.

    Output shape (filtered by sub via _SUB_TRACK_FIELDS):
      id                str                  — track id (e.g. "agent")
      review_level      str                  — none | standard | security
      modules           [str]                — module names from tracks.<id>.modules
      module_details    [dict]               — resolved per-module context
                                              ({name, root, language, build,
                                                lint, test: {...}})
      module_roots      [str]                — unique root paths for module path
                                              constraint (from module_details)
      module_names      [str]                — module names (same as modules)
      stage             dict                 — resolved stage context
                                              ({name, test_key, gate,
                                                environment, test_commands})
      rollback_context  dict | None          — nested rollback info when present
      issue_*           str                  — only populated for fix subs
      proposal_path     str                  — only populated for final-gate
      ...

    `change` (optional) is forwarded to _build_stage_context so the
    per-change environment decision (.pg/changes/<change>/environment.yaml)
    is reflected in the stage context — not the config.yaml default.
    """
    meta = _track_meta(config, track_id)
    if not meta["modules"] and not get_track_config(config, track_id):
        return {}
    ctx = dict(meta)
    ctx["module_details"] = _build_module_context(config, meta["modules"])
    # Derive module_roots / module_names from module_details (single SSOT).
    ctx["module_roots"] = list(
        dict.fromkeys(m.get("root") for m in ctx["module_details"] if m.get("root"))
    )
    ctx["module_names"] = list(
        ctx["module_details"][i]["name"] for i in range(len(ctx["module_details"]))
    )
    ctx["stage"] = _build_stage_context(config, track_id, change=change)
    if sub is None:
        return ctx
    allowed = _SUB_TRACK_FIELDS.get(sub)
    if allowed is None:
        return ctx
    return {k: ctx[k] for k in allowed if k in ctx}


# ============================================================
# Dispatch action builders
# ============================================================

_TASKS_SECTION_HEADING_RE = re.compile(
    r"^##\s+\d+\.\s+([a-zA-Z0-9_.-]+:[a-zA-Z0-9_-]+)\s*-\s*(.+)$"
)
_TASKS_CHECKBOX_RE = re.compile(r"^- \[[ x]\]\s+(\d+\.\d+\s+.+)$")
_TASKS_NOOP_RE = re.compile(r"^- 无$")
_TASKS_VALIDATION_END_RE = re.compile(r"^##\s+\d+\.")
_TASKS_CHANGE_NAME = None  # set at call time


def _extract_task_prompt(change, item, sub):
    """Extract and reformat tasks.md section for item:sub into actionable instructions.

    Returns dict with:
      preformatted_tasks  — list of "**N.M title**\ncommand" strings
      validation_block    — validation requirement paragraph (or empty string)
      noop                — True if section is all "- 无"
    """
    tasks_path = os.path.join(CHANGES_DIR, change, "tasks.md")
    if not os.path.isfile(tasks_path):
        return {"preformatted_tasks": [], "validation_block": "", "noop": False}

    with open(tasks_path, encoding="utf-8") as f:
        lines = f.readlines()

    # Find target section: "## N. item:sub - label"
    section_start = None
    section_end = None
    target_prefix = f"{item}:{sub}"
    for i, line in enumerate(lines):
        m = _TASKS_SECTION_HEADING_RE.match(line.strip())
        if m and m.group(1) == target_prefix:
            section_start = i
            continue
        if section_start is not None and _TASKS_SECTION_HEADING_RE.match(line.strip()):
            section_end = i
            break

    if section_start is None:
        return {"preformatted_tasks": [], "validation_block": "", "noop": False}

    if section_end is None:
        section_end = len(lines)

    section_lines = lines[section_start + 1 : section_end]

    # Extract checkboxes and validation block
    tasks = []
    validation_block = ""
    in_validation = False
    validation_parts = []
    all_noop = True

    for line in section_lines:
        stripped = line.strip()

        # Check for noop
        if _TASKS_NOOP_RE.match(stripped):
            continue  # skip " - 无" line, all_noop stays true

        # Checkbox line
        cm = _TASKS_CHECKBOX_RE.match(stripped)
        if cm:
            all_noop = False
            tasks.append(f"**{cm.group(1)}**")
            continue

        # Validation block
        if stripped.startswith("**验证要求**"):
            in_validation = True
            continue
        if in_validation:
            if _TASKS_VALIDATION_END_RE.match(stripped):
                in_validation = False
                continue
            if stripped:
                validation_parts.append(stripped)

    if validation_parts:
        validation_block = "\n".join(validation_parts)

    return {
        "preformatted_tasks": tasks,
        "validation_block": validation_block,
        "noop": all_noop and len(tasks) == 0,
    }


def _enrich_context_with_rollback(ctx, rb):
    """Populate ctx["rollback_context"] as a nested dict (prompt-template friendly).

    Older schema put `rollback_reason` / `rollback_source` flat at ctx top
    level. The prompt template expects `ctx["rollback_context"]["failed_at"]`
    / `["reason"]` / `["source"]` (nested), so we normalize on write.
    """
    if not rb or not rb.get("found"):
        return
    ctx["rollback_context"] = {
        "failed_at": rb.get("failed_at", ""),
        "reason": rb.get("reason", ""),
        "source": rb.get("source", ""),
    }


def _build_final_gate_context(change):
    """Collect paths for final-gate agent (proposal/tasks/designs/reports)."""
    from pathlib import Path

    base = Path(CHANGES_DIR) / change
    design_paths = sorted(
        str(p.relative_to(PROJECT_ROOT)) for p in base.glob("design*.md")
    )
    proposal_path = str(base / "proposal.md")
    tasks_path = str(base / "tasks.md")
    # report_paths: collect all *-gate-assessment.md under 2-build/
    build_dir = base / APPLY_DIR
    report_paths = sorted(
        str(p.relative_to(PROJECT_ROOT))
        for p in build_dir.glob("*-gate-assessment.md")
        if p.is_file()
    )
    design_doc_path = design_paths[0] if design_paths else ""
    return {
        "_change": change,
        "proposal_path": proposal_path,
        "tasks_path": tasks_path,
        "design_doc_path": design_doc_path,
        "design_doc_paths": design_paths,
        "report_paths": report_paths,
    }


def _enrich_context_with_stage(ctx, config, item, change=None):
    """Ensure ctx["stage"] reflects the v3.0 schema.

    Always re-resolves stage context so that `change`-dependent environment
    selection (.pg/changes/<change>/environment.yaml) is always honored.
    Previous idempotent no-op behavior was a bug: if filter_track_context was
    called without `change`, the stage was resolved against config.yaml's
    track.environment default and could not be re-resolved here.

    deployment_actions lives inside ctx["stage"]["environment"]["actions"] and is
    surfaced to dev/verify/fix agents via _SUB_TRACK_FIELDS["stage"] (the
    nested key is enough — no separate top-level key needed).
    """
    ctx["stage"] = _build_stage_context(config, item, change=change)
    return ctx


def _resolve_test_commands(config, track_cfg, test_key):
    """Collect test commands for a track's modules matching the given test_key."""
    commands = []
    for mod_name in track_cfg.get("modules") or []:
        mod = config.get("modules", {}).get(mod_name, {})
        cmd = mod.get("test", {}).get(test_key)
        if cmd:
            commands.append(cmd)
    return commands


def _find_stage_for_track(config, track_id):
    """Return (stage_name, stage_dict) for the stage containing track_id.
    Supports qualified names (dev-isolated.backend) — matches by stage prefix.
    Falls back to bare track name matching."""
    if "." in track_id:
        stage_name, bare = track_id.split(".", 1)
        for stage in config.get("stages") or []:
            if stage.get("name") == stage_name and bare in (stage.get("tracks") or []):
                return stage.get("name"), stage
    # Fallback: match by bare track name
    for stage in config.get("stages") or []:
        if track_id in (stage.get("tracks") or []):
            return stage.get("name"), stage
    return None, None


def _enrich_context_with_tasks(ctx, change, item, sub):
    """Add preformatted tasks to context dict.

    tasks_validation and tasks_noop are only injected for subs in _TASKS_META_SUBS
    (test, dev, verify) — fix and gate agents don't need them.
    """
    task_info = _extract_task_prompt(change, item, sub)
    ctx["tasks_preformatted"] = task_info["preformatted_tasks"]
    if sub in _TASKS_META_SUBS:
        ctx["tasks_validation"] = task_info["validation_block"]
        ctx["tasks_noop"] = task_info["noop"]
    return ctx


def _enrich_context_with_prompt_injection(ctx, config, item, sub):
    """Build the pre-assembled prompt injection for the dispatch action.

    Reads `build_rules` from config.yaml and, for the current
    (item, sub), assembles the prepend / append fragments that the runner
    itself splices into the sub-agent prompt (via _merge_prompt_injection)
    before writing the dispatch file.

    The LLM orchestrator does NOT see the rendered prompt at all — it
    only receives the dispatch_file path. The LLM does NOT need to know
    how build_rules works:

        content = _merge_prompt_injection(rendered, ctx)
        # == prepend + "\n\n" + rendered + "\n\n" + append

    Field reference (ctx["prompt_injection"]):
      target_agent: "pg-build/{sub}"  (which agent this targets)
      prepend:     ""  | <assembled block>
      append:      ""  | <assembled block>
      rules_applied: list[rule_id]  (for traceability / debug)

    Rules with type != "inject-prompt" or target_agent mismatch are
    silently skipped. Position "prepend" goes to prepend; "append"
    (default) goes to append. Multiple rules in the same position are
    concatenated in config order, separated by two newlines.
    """
    target = f"pg-build/{sub}"
    rules = config.get("build_rules") or []

    prepend_parts = []
    append_parts = []
    applied = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rtype = rule.get("type")
        rtarget = rule.get("target_agent")
        rtemplate = rule.get("template", "")
        if rtype != "inject-prompt":
            continue
        if rtarget != target:
            continue
        if not rtemplate:
            continue
        rposition = rule.get("position", "append")
        if rposition == "prepend":
            prepend_parts.append(rtemplate)
        else:
            append_parts.append(rtemplate)
        rid = rule.get("id", "<no-id>")
        applied.append(rid)

    ctx["prompt_injection"] = {
        "target_agent": target,
        "prepend": "\n\n".join(prepend_parts),
        "append": "\n\n".join(append_parts),
        "rules_applied": applied,
    }
    return ctx


def dispatch_action(agent, item, sub, context, attempt, init_commit=None):
    # Inject `sub` into context so prompt templates can use {{context.sub}}
    # in {#if sub in [...]} blocks (sub is the dispatched sub type).
    if "sub" not in context:
        context["sub"] = sub
    change = context.get("_change", "")
    # Pre-allocate dispatch + report seq so the rendered prompt can include
    # them in the report filename (e.g. `cat > 2-build/{report_seq}-{item}-...`).
    seq = _allocate_seq(change)
    dispatch_seq = _format_seq(seq)
    report_seq = _format_seq(seq + 1)
    context["dispatch_seq"] = dispatch_seq
    context["report_seq"] = report_seq
    # Render the prompt template, then merge build_rules prepend/append
    # fragments. Both steps happen here in the runner so the LLM orchestrator
    # never sees the rendered prompt content — it only receives the path of
    # the dispatch file and tells the sub-agent to read it.
    template_str = _build_prompt_template(item, sub)
    rendered = _render_prompt_template(template_str, context)
    content = _merge_prompt_injection(rendered, context)
    dispatch_path = _write_dispatch_file_with_seq(
        change,
        item,
        sub,
        content,
        seq=seq,
        cycle=None,
        agent=agent,
    )
    result = {
        "action": "dispatch",
        "item": item,
        "sub": sub,
        "agent": agent,
        "attempt": attempt,
        "dispatch_file": dispatch_path,
        "dispatch_seq": dispatch_seq,
        "report_seq": report_seq,
    }
    # Surface bootstrap-init commit result only when it actually ran
    # (first-dispatch path). Subsequent re-entries pass init_commit=None
    # so the field is omitted and we don't clutter every dispatch response.
    if init_commit is not None:
        result["init_commit"] = init_commit
    return result


def dispatch_fix_action(item, cycle, context, config=None):
    if config is not None:
        _enrich_context_with_prompt_injection(context, config, item, "fix")
    context["sub"] = "fix"
    context["fix_cycle"] = cycle
    # 报告文件名走 ctx 字段，让 BLOCK 末尾 cat > 命令与 sub 解耦
    context.setdefault("fix_report_filename", "fix-verify.md")
    change = context.get("_change", "")
    # Inject verify_report_path so the fix agent can read the source report
    # directly — runner does NOT parse or pre-process the report contents.
    if change:
        context.setdefault(
            "verify_report_path",
            track_latest_report_path(change, item, "verify")
            or verify_report_path_for(change, item),
        )
    # Pre-allocate seqs before rendering so the template can include them.
    seq = _allocate_seq(change)
    dispatch_seq = _format_seq(seq)
    report_seq = _format_seq(seq + 1)
    context["dispatch_seq"] = dispatch_seq
    context["report_seq"] = report_seq
    template_str = _build_prompt_template(item, "fix")
    rendered = _render_prompt_template(template_str, context)
    content = _merge_prompt_injection(rendered, context)
    dispatch_path = _write_dispatch_file_with_seq(
        change,
        item,
        "fix",
        content,
        seq=seq,
        cycle=cycle,
        agent=FIX_AGENT,
    )
    return {
        "action": "dispatch_fix",
        "item": item,
        "sub": "fix",
        "agent": FIX_AGENT,
        "fix_cycle": cycle,
        "dispatch_file": dispatch_path,
        "dispatch_seq": dispatch_seq,
        "report_seq": report_seq,
    }


def dispatch_fix_gate_action(item, gate_cycle, context, config=None):
    """Dispatch a fix-gate agent with a gate-view prompt.

    Mirrors dispatch_fix_action but points the fix-gate agent at the most
    recent gate-assessment report (containing `### {track}:G-N` sections)
    instead of a verify report. As with dispatch_fix_action, the report
    path is injected but the report contents are NOT parsed or pre-extracted.
    """
    if config is not None:
        _enrich_context_with_prompt_injection(context, config, item, "fix-gate")
    context["sub"] = "fix-gate"
    context["fix_cycle"] = gate_cycle  # 用 gate_cycle 当 fix_cycle 喂 prompt
    context.setdefault("fix_report_filename", "fix-gate-verify.md")
    change = context.get("_change", "")
    # Inject gate_report_path so the fix-gate agent can read the source
    # report directly — runner does NOT parse G-N sections.
    if change:
        context.setdefault(
            "gate_report_path",
            track_latest_report_path(change, item, "gate-assessment")
            or gate_report_path_for(change, item),
        )
    # Pre-allocate seqs before rendering.
    seq = _allocate_seq(change)
    dispatch_seq = _format_seq(seq)
    report_seq = _format_seq(seq + 1)
    context["dispatch_seq"] = dispatch_seq
    context["report_seq"] = report_seq
    template_str = _build_prompt_template(item, "fix-gate")
    rendered = _render_prompt_template(template_str, context)
    content = _merge_prompt_injection(rendered, context)
    dispatch_path = _write_dispatch_file_with_seq(
        change,
        item,
        "fix-gate",
        content,
        seq=seq,
        cycle=gate_cycle,
        agent=FIX_GATE_AGENT,
    )
    return {
        "action": "dispatch_fix_gate",
        "item": item,
        "sub": "fix-gate",
        "agent": FIX_GATE_AGENT,
        "gate_cycle": gate_cycle,
        "dispatch_file": dispatch_path,
        "dispatch_seq": dispatch_seq,
        "report_seq": report_seq,
    }


# ============================================================
# Context chain
# ============================================================
# 已迁移至 pg_context_chain.py — 直接调用 pg_context_chain.*


# ============================================================
# Pipeline state operations
# ============================================================


def pipeline_detect(change):
    return run_script(PIPELINE_STATE_PY, "detect", change, change=change)


def pipeline_mark(change, item, sub=None):
    args = ["mark", change, item]
    if sub:
        args.append(sub)
    return run_script(PIPELINE_STATE_PY, *args, change=change, track_id=item)


def pipeline_rollback(change, track):
    return run_script(
        PIPELINE_STATE_PY, "rollback", change, track, change=change, track_id=track
    )


def pipeline_gate_rollback(change, track, gate_report_path):
    return run_script(
        PIPELINE_STATE_PY,
        "gate-rollback",
        change,
        track,
        gate_report_path,
        change=change,
        track_id=track,
    )


def pipeline_progress(change):
    return run_script(PIPELINE_STATE_PY, "progress", change, change=change)


def gate_report_path_for(change, track):
    """Infer next gate-assessment file path by scanning track's existing reports.

    Naming pattern: {track}-{N}-gate-assessment.md where N is inferred as
    max(existing track files) + 1. Returns the *next* path that will be used.
    """
    apply_dir = get_apply_dir(change)
    if not os.path.isdir(apply_dir):
        return os.path.join(apply_dir, f"{track}-1-gate-assessment.md")
    pattern = re.compile(rf"^{re.escape(track)}-(\d+)-gate-assessment\.md$")
    max_n = 0
    for fname in os.listdir(apply_dir):
        m = pattern.match(fname)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return os.path.join(apply_dir, f"{track}-{max_n + 1}-gate-assessment.md")


def verify_report_path_for(change, track):
    """Infer next verify-report file path by scanning track's existing reports.

    Mirror of `gate_report_path_for` for verify reports. Naming pattern:
    {track}-{N}-verify.md where N is inferred as max(existing track files) + 1.
    Returns the *next* path that will be used.
    """
    apply_dir = get_apply_dir(change)
    if not os.path.isdir(apply_dir):
        return os.path.join(apply_dir, f"{track}-1-verify.md")
    pattern = re.compile(rf"^{re.escape(track)}-(\d+)-verify\.md$")
    max_n = 0
    for fname in os.listdir(apply_dir):
        m = pattern.match(fname)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return os.path.join(apply_dir, f"{track}-{max_n + 1}-verify.md")


def track_latest_report_path(change, track, kind):
    """Return path of the latest (highest-N) {track}-{N}-{kind}.md in 2-build/.

    Used by fix-gate / verify-fix cycles to locate the most recent input report.
    """
    apply_dir = get_apply_dir(change)
    if not os.path.isdir(apply_dir):
        return None
    pattern = re.compile(rf"^{re.escape(track)}-(\d+)-{re.escape(kind)}\.md$")
    max_n = 0
    latest = None
    for fname in os.listdir(apply_dir):
        m = pattern.match(fname)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
                latest = fname
    return os.path.join(apply_dir, latest) if latest else None


def _record_accepted_gaps(change, track, gate_report_path, max_gate_fix):
    """Append all unfixed gaps from gate report to known-issues.md."""
    known_issues_path = os.path.join(CHANGES_DIR, change, APPLY_DIR, "known-issues.md")
    if not os.path.isfile(gate_report_path):
        return

    with open(gate_report_path, encoding="utf-8") as f:
        report = f.read()

    gap_pattern = re.compile(
        r"###\s+"
        + re.escape(track)
        + r":G-\d+.*?(?=###\s+"
        + re.escape(track)
        + r":G-|\Z)",
        re.DOTALL,
    )
    gaps = gap_pattern.findall(report)

    if not gaps:
        return

    os.makedirs(os.path.dirname(known_issues_path), exist_ok=True)
    if not os.path.isfile(known_issues_path):
        with open(known_issues_path, "w", encoding="utf-8") as f:
            f.write(f"# Known Issues - {change}\n\n")
            f.write("_此文件由 gate-fix 循环耗尽时自动记录_\n\n")

    from datetime import datetime, timedelta, timezone

    _SHANGHAI = timezone(timedelta(hours=8))
    ts = datetime.now(_SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    with open(known_issues_path, "a", encoding="utf-8") as f:
        f.write(f"\n## {ts} - {track} gate FAIL 耗尽后被接受的 gap\n\n")
        f.write(f"源: `{gate_report_path}`\n\n")
        f.write(f"经过 {max_gate_fix} 轮 fix-gate 循环仍未修复, 已被接受继续推进:\n\n")
        for gap in gaps:
            f.write(gap.strip() + "\n\n")


# ============================================================
# Helpers — context chain init
# ============================================================

_CHANGE_DIR = os.path.join(CHANGES_DIR, "__placeholder__")


def _auto_archive(change):
    """Move .pg/changes/<change>/ to archive/ via shared pg-archive.py.

    Returns dict with keys: ok, target_name (str|None), src, target, reason (on failure).
    Never raises — caller decides how to react to failure.
    """
    if not os.path.isfile(PG_ARCHIVE_PY):
        return {
            "ok": False,
            "reason": f"找不到归档脚本: {PG_ARCHIVE_PY}",
            "src": os.path.relpath(os.path.join(CHANGES_DIR, change), PROJECT_ROOT),
        }
    src = os.path.join(CHANGES_DIR, change)
    if not os.path.isdir(src):
        return {
            "ok": False,
            "reason": f"源目录不存在: {src}",
            "src": os.path.relpath(src, PROJECT_ROOT),
        }
    result = run_script(PG_ARCHIVE_PY, "move", change, change=change)
    if result.get("error"):
        return {
            "ok": False,
            "reason": result["error"],
            "src": os.path.relpath(src, PROJECT_ROOT),
        }
    return result


def _git_commit_archive(archive_result):
    """Commit the archive move on the current feature branch.

    Best-effort: returns a dict describing what happened. Never raises.
    Does NOT push — pushing happens in pg-verify-and-merge.
    """
    if not archive_result.get("ok"):
        return {
            "attempted": False,
            "committed": False,
            "reason": "归档未成功，跳过 commit",
        }

    src_rel = archive_result.get("src")
    target_rel = archive_result.get("target")
    target_name = archive_result.get("target_name", "")

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()

    status_r = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    porcelain = status_r.stdout.strip()

    git_rm = subprocess.run(
        ["git", "rm", "-r", "--cached", "--ignore-unmatch", src_rel],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )

    git_add = subprocess.run(
        ["git", "add", "--", target_rel],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if git_add.returncode != 0:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "reason": f"git add 失败: {git_add.stderr.strip() or git_add.stdout.strip()}",
        }

    staged_after = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()
    if not staged_after:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "reason": "无 staged 变更（src/target 均未在 git 跟踪中），无需 commit",
            "porcelain": porcelain,
        }

    msg = f"archive change {target_name}"
    commit = subprocess.run(
        ["git", "commit", "-m", msg],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if commit.returncode != 0:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "reason": f"git commit 失败: {commit.stderr.strip() or commit.stdout.strip()}",
        }

    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()

    return {
        "attempted": True,
        "committed": True,
        "branch": branch,
        "sha": sha,
        "message": msg,
    }


def _auto_commit_on_init(change):
    """Auto-commit proposal artifacts before the first dispatch.

    Behavior:
      - Runs synchronously in cmd_next before the first sub-agent dispatch,
        AFTER `_ensure_feature_branch` and AFTER `save_state({init_committed: True})`
        (so the commit lands on feat/pg/<change> and includes the freshly
        written `.pipeline-state.json`).
      - Uses `git add -A` (full-tree add), matching the record-path decision.
      - Skips (records reason) when `git status --porcelain` is empty.
      - Never raises; never pushes.
      - Returns dict: {attempted, committed, branch, sha, message, reason}.
    """
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()

    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()

    if not porcelain:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "reason": "工作区干净，无可提交内容（init 阶段）",
        }

    add = subprocess.run(
        ["git", "add", "-A"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if add.returncode != 0:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "reason": f"git add 失败: {add.stderr.strip() or add.stdout.strip()}",
        }

    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()
    if not staged:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "reason": "无 staged 变更可提交",
        }

    msg = f"chore({change}): bootstrap pg-build"
    commit = subprocess.run(
        ["git", "commit", "-m", msg],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if commit.returncode != 0:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "message": msg,
            "reason": f"git commit 失败: {commit.stderr.strip() or commit.stdout.strip()}",
        }

    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()

    return {
        "attempted": True,
        "committed": True,
        "branch": branch,
        "sha": sha,
        "message": msg,
        "reason": None,
    }


def _auto_commit_on_record(change, item, sub, status):
    """Auto-commit working-tree changes after every record call.

    Behavior:
      - Runs synchronously in the record path so the next `next` sees the commit.
      - Uses `git add -A` (full-tree add), matching the "git add -A 全量提交" decision.
      - Skips (records reason) when `git status --porcelain` is empty.
      - Never raises; never pushes.
      - Returns dict: {attempted, committed, branch, sha, message, reason}.
    """
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()

    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()

    if not porcelain:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "reason": "工作区干净，无可提交内容",
        }

    add = subprocess.run(
        ["git", "add", "-A"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if add.returncode != 0:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "reason": f"git add 失败: {add.stderr.strip() or add.stdout.strip()}",
        }

    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()
    if not staged:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "reason": "无 staged 变更可提交",
        }

    msg = f"chore({change}): auto-record {item}:{sub} {status}"
    commit = subprocess.run(
        ["git", "commit", "-m", msg],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if commit.returncode != 0:
        return {
            "attempted": True,
            "committed": False,
            "branch": branch,
            "message": msg,
            "reason": f"git commit 失败: {commit.stderr.strip() or commit.stdout.strip()}",
        }

    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()

    return {
        "attempted": True,
        "committed": True,
        "branch": branch,
        "sha": sha,
        "message": msg,
        "reason": None,
    }


def _inject_commit(result, change, item, sub, status):
    """Inject auto-commit result into a record-returned dict.

    No-op when result is not a dict (e.g. workflow_failed paths still
    return dicts, but defensive for any future non-dict returns).
    """
    if not isinstance(result, dict):
        return result
    result["commit"] = _auto_commit_on_record(change, item, sub, status)
    return result


def _ensure_context_chain(change):
    pg_context_chain.ensure(change)


def _ensure_feature_branch(change):
    """Create feat/pg/{change} branch if not already on it."""
    expected = f"feat/pg/{change}"
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()
    if branch == expected:
        return
    subprocess.run(["git", "stash"], capture_output=True, cwd=PROJECT_ROOT)
    r = subprocess.run(
        ["git", "rev-parse", "--verify", expected],
        capture_output=True,
        cwd=PROJECT_ROOT,
    )
    if r.returncode == 0:
        subprocess.run(
            ["git", "checkout", expected], capture_output=True, cwd=PROJECT_ROOT
        )
    else:
        subprocess.run(
            ["git", "checkout", "-b", expected, branch],
            capture_output=True,
            cwd=PROJECT_ROOT,
        )


def _maybe_bootstrap_init_commit(change, state):
    """Run the bootstrap commit exactly once per change, on first dispatch.

    Args:
        change: change name.
        state: the in-memory state dict owned by cmd_next. The function
            mutates this dict in place to set `init_committed=True` so
            the caller's subsequent `save_state(state)` calls persist the
            marker (otherwise a later save_state would overwrite the
            marker the helper just wrote to disk with a version that
            lacks the field).

    Returns:
        dict | None — the auto-commit result dict when this call actually ran
        the bootstrap commit (so callers can surface it to the LLM); None when
        the commit was already performed on a previous invocation.

    Mechanics:
      - If the caller's state already has `init_committed=True` → skip.
      - Else mark the field True in-place, persist state to disk BEFORE
        calling `_auto_commit_on_init(change)` so that the bootstrap commit
        includes the freshly written `.pipeline-state.json` (otherwise the
        git commit would land BEFORE the file is staged and `git add -A`
        would silently miss it). After persisting, call
        `_auto_commit_on_init(change)` and return its result. Failures are
        non-fatal: dispatch still proceeds, and the marker is set so we do
        not retry on the next invocation.

    Note: the explicit save_state/commit call here supersedes the previous
    "implicit save_state by caller" contract. The caller (cmd_next /
    cmd_next_v2) still calls save_state later as part of the dispatch flow,
    but the bootstrap commit is now guaranteed to include state.json on
    disk regardless of subsequent ordering.
    """
    if state.get("init_committed"):
        return None

    state["init_committed"] = True
    # Persist state to disk BEFORE the init commit so that .pipeline-state.json
    # is present on the filesystem when `_auto_commit_on_init` runs
    # `git add -A` + `git commit`. Without this, the bootstrap commit would
    # land with state.json missing (regression seen on
    # `instance-detail-host-versions`: commit 6c7eb87a contained only
    # context-chain.md, not the freshly-created .pipeline-state.json).
    try:
        if hasattr(state, "commit"):
            # v2 PipelineState instance — call its commit() to flush.
            state.commit()
        elif "change" in state:
            # v1 plain dict with "change" key — call save_state.
            save_state(state)
        # else: v2 context dict without "change" key (returned by
        # _normalize_state_for_bootstrap). Skip early persist here;
        # the caller (_persist_state_mutation) will persist right
        # after this function returns.  Without this guard,
        # save_state(state) raises KeyError('change') because the
        # context dict lacks the top-level "change" key.
    except Exception as e:
        print(
            f"[_maybe_bootstrap_init_commit] state persist failed: {e}", file=sys.stderr
        )
        # Fall through and run the init commit anyway; the marker is already
        # set on the in-memory state so the next save_state() call will
        # eventually persist it.
    return _auto_commit_on_init(change)


# ============================================================
# Simple track section normalization
# ============================================================

# Simple track dispatch — pg-build/simple sub-agent
# ============================================================
# Simple tracks (`type: simple`) are dispatched to a sub-agent rather than
# executed by the runner in-process. The sub-agent receives the normalized
# command list in its prompt and reports back SUCCESS / FAILED, allowing
# LLM-driven auto-recovery (e.g. apt install missing deps) before failing.


def _infer_next_report_n(change, item_id):
    """Scan 2-build/ for existing `{item_id}-N-*.md` files; return max+1.

    Mirrors the per-track N inference algorithm documented in SKILL.md
    (each track starts at 1 and increments per report cycle).
    """
    apply_dir = os.path.join(CHANGES_DIR, change, "2-build")
    if not os.path.isdir(apply_dir):
        return 1
    pattern = re.compile(rf"^{re.escape(item_id)}-(\d+)-.*\.md$")
    max_n = 0
    for fname in os.listdir(apply_dir):
        m = pattern.match(fname)
        if m:
            try:
                max_n = max(max_n, int(m.group(1)))
            except ValueError:
                continue
    return max_n + 1


# ============================================================
# Global sequential numbering for 2-build/ files
# ============================================================
#
# All markdown files produced during a pg-build run get a globally
# monotonically-increasing 3-digit sequence number (001, 002, ...) so that
# `ls 2-build/` reflects event order. Two file classes share the same
# number space:
#
#   * Dispatch instruction files (written by runner, read by sub-agent)
#   * Sub-agent report files (written by sub-agent, read by gate/next-pass)
#
# `dispatch_file = "2-build/{seq}-{item}-{sub}-dispatch[-{cycle}].md"`
#   - `{cycle}` present only for fix / fix-gate cycles
#
# `report_file  = "2-build/{seq}-{item}-{kind}[-N].md"`
#   - `{kind}` ∈ {test-verify, dev-verify, verify, gate-assessment,
#                  fix-verify-N, fix-gate-verify-N, simple-verify,
#                  final-gate-assessment}
#   - sub-agent learns the report seq from the dispatch file (it's
#     `dispatch_seq + 1`), so the sub-agent does NOT need a separate
#     allocation call.
#
# `_allocate_seq(change)` returns the next integer. Counter is held in
# module-level state (reset each runner process) but bootstrapped from the
# filesystem at first call to be crash-safe. The counter is shared across
# all dispatch types in a single runner process.

_SEQ_COUNTERS = {}


def _allocate_seq(change):
    """Allocate next global sequence number for a 2-build/ file.

    On first call for a given `change`, scans 2-build/ to find the highest
    existing seq number and seeds the in-memory counter above that value.
    Subsequent calls increment without scanning (synchronous; runner
    serializes dispatches).
    """
    if not change:
        # Defensive: never produce 0/None for a missing change name.
        return 1
    if change not in _SEQ_COUNTERS:
        apply_dir = get_apply_dir(change)
        max_seen = 0
        if os.path.isdir(apply_dir):
            for fname in os.listdir(apply_dir):
                if not fname.endswith(".md"):
                    continue
                # Accept both old style "{item}-{N}-*.md" and new "{NNN}-*.md"
                # but only the new style contributes to global seq.
                m = re.match(r"^(\d{3})-", fname)
                if m:
                    try:
                        max_seen = max(max_seen, int(m.group(1)))
                    except ValueError:
                        continue
        _SEQ_COUNTERS[change] = max_seen
    _SEQ_COUNTERS[change] += 1
    return _SEQ_COUNTERS[change]


def _format_seq(seq):
    """Zero-pad sequence number to 3 digits for filename consistency."""
    return f"{int(seq):03d}"


def _write_manifest(change, entry):
    """Append one record to 2-build/manifest.yaml (best-effort audit log).

    `entry` is a dict with keys: seq, file, item, sub, kind, cycle (|None),
    agent, role (dispatch | report). Failures are non-fatal; the manifest is
    a convenience for human review, not a critical-state file.
    """
    try:
        apply_dir = get_apply_dir(change)
        os.makedirs(apply_dir, exist_ok=True)
        manifest_path = os.path.join(apply_dir, "manifest.yaml")
        with open(manifest_path, "a", encoding="utf-8") as f:
            f.write(f"- seq: {entry.get('seq')}\n")
            f.write(f"  file: {entry.get('file')}\n")
            f.write(f"  item: {entry.get('item')}\n")
            f.write(f"  sub: {entry.get('sub')}\n")
            f.write(f"  kind: {entry.get('kind')}\n")
            cycle = entry.get("cycle")
            f.write(f"  cycle: {cycle if cycle is not None else 'null'}\n")
            f.write(f"  role: {entry.get('role')}\n")
            f.write(f"  agent: {entry.get('agent')}\n")
            f.write(f"  timestamp: {datetime.now().isoformat()}\n")
    except Exception as e:
        # Best-effort; do not fail the dispatch because of a manifest write.
        print(f"[warn] manifest write failed for {change}: {e}", file=sys.stderr)


def _dispatch_file_name(change, item, sub, cycle=None):
    """Build the dispatch instruction file name: {seq}-{item}-{sub}-dispatch[-{cycle}].md.

    `seq` is allocated at call time (global counter). The full path is
    returned; the file is NOT yet written (caller decides content).
    """
    seq = _allocate_seq(change)
    name = f"{_format_seq(seq)}-{item}-{sub}-dispatch"
    if cycle is not None:
        name += f"-{int(cycle)}"
    name += ".md"
    return seq, os.path.join(get_apply_dir(change), name)


def _write_dispatch_file(change, item, sub, content, cycle=None, agent=None):
    """Allocate seq, write the dispatch instruction file, append manifest.

    `content` is the fully-rendered + prompt_injection-merged prompt string.
    Returns (seq, absolute_path).
    """
    seq, path = _dispatch_file_name(change, item, sub, cycle=cycle)
    apply_dir = get_apply_dir(change)
    os.makedirs(apply_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    _write_manifest(
        change,
        {
            "seq": _format_seq(seq),
            "file": os.path.basename(path),
            "item": item,
            "sub": sub,
            "kind": "dispatch",
            "cycle": int(cycle) if cycle is not None else None,
            "role": "dispatch",
            "agent": agent or f"pg-build/{sub}",
        },
    )
    return seq, path


def _write_dispatch_file_with_seq(
    change, item, sub, content, seq, cycle=None, agent=None
):
    """Write dispatch file with a pre-allocated seq (caller manages counter).

    Used by dispatch functions that need to inject `dispatch_seq` /
    `report_seq` into the context BEFORE rendering the prompt template.
    Returns the absolute path.
    """
    name = f"{_format_seq(seq)}-{item}-{sub}-dispatch"
    if cycle is not None:
        name += f"-{int(cycle)}"
    name += ".md"
    path = os.path.join(get_apply_dir(change), name)
    apply_dir = get_apply_dir(change)
    os.makedirs(apply_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    _write_manifest(
        change,
        {
            "seq": _format_seq(seq),
            "file": os.path.basename(path),
            "item": item,
            "sub": sub,
            "kind": "dispatch",
            "cycle": int(cycle) if cycle is not None else None,
            "role": "dispatch",
            "agent": agent or f"pg-build/{sub}",
        },
    )
    return path


def _merge_prompt_injection(content, context):
    """Apply `build_rules` prepend/append fragments to rendered prompt.

    Replaces the orchestrator's old in-line merge. The runner now does this
    before writing the dispatch file, so the orchestrator never sees the
    `prompt_injection` field at all.
    """
    pi = context.get("prompt_injection") or {}
    prepend = pi.get("prepend") or ""
    append = pi.get("append") or ""
    if prepend:
        content = prepend + "\n\n" + content
    if append:
        content = content + "\n\n" + append
    return content


def _compute_simple_timeout(commands_normalized):
    """next_call_timeout_seconds = sum(cmd.timeout_seconds) + N*30 余量.

    Per user decision: sum of all command timeouts plus a 30s overhead per
    command to cover shell start/exit + simple-track LLM agent reasoning
    time. Falls back to 600s when the list is empty.
    """
    if not commands_normalized:
        return 600
    total = sum((c.get("timeout_seconds") or 1800) for c in commands_normalized)
    n = len(commands_normalized)
    return total + n * 30


def _build_simple_context(config, change, item_id):
    """Build the ctx dict used to render the pg-build/simple prompt.

    Mirrors the field shape produced by filter_track_context (so that the
    base _PROMPT_TEMPLATE_BASE can render without if/else noise), but fills
    the TDVG-specific fields with zero-values since simple tracks do not
    participate in test/dev/verify/gate.

    Returns ctx dict. If the track is misconfigured (no commands), returns
    a ctx with the sentinel `_commands_missing=True`; callers should map
    that to workflow_failed.
    """
    tc = get_track_config(config, item_id)
    if not tc or tc.get("type") != "simple":
        raise ValueError(f"{item_id} is not a simple track")

    track_default_timeout = tc.get("timeout_seconds", 1800)
    track_on_failure = tc.get("on_failure", "workflow_failed")
    raw_cmds = tc.get("commands") or []

    if not raw_cmds:
        return {"_commands_missing": True, "_change": change, "id": item_id}

    commands_normalized = []
    for idx, entry in enumerate(raw_cmds, 1):
        per_cmd = normalize_simple_command(
            entry, track_default_timeout=track_default_timeout
        )
        of = per_cmd["on_failure"]
        commands_normalized.append(
            {
                "idx": idx,
                "cmd": per_cmd["cmd"],
                "timeout_seconds": per_cmd["timeout_seconds"],
                "on_failure": of,
                "retry_max": per_cmd["retry_max"],
                "retry_timeout_seconds": per_cmd["retry_timeout_seconds"],
                # Pre-computed booleans for the Jinja-style {#if this.X}
                # template renderer (which doesn't support `==`).
                "is_retry": of == "retry",
                "is_continue": of == "continue",
                "is_fail": of == "fail",
            }
        )

    ctx = {
        # ---- base template compatible fields ----
        "id": item_id,
        "label": tc.get("label", item_id),
        "review_level": "none",
        "modules": [],
        "module_details": [],
        "module_roots": [],
        "module_names": [],
        "max_fix_retries": 0,
        "fix_routing": "none",
        "tasks_preformatted": [],
        "tasks_validation": "",
        "tasks_noop": True,
        # ---- change & rollback ----
        "_change": change,
        "rollback_context": None,
        # ---- simple track specific ----
        "track_type": "simple",
        "track_timeout": track_default_timeout,
        "track_on_failure": track_on_failure,
        "commands_normalized": commands_normalized,
        # Report seq is set by _build_simple_dispatch via _allocate_seq (global,
        # not per-track). Kept unset here so we can detect overrides if needed.
        "report_seq": None,
        # ---- stage (optional; only when simple track is bound to an env) ----
        "stage": {},
    }
    # Attach stage if the simple track is associated with a stage (env-aware).
    try:
        stage = _build_stage_context(config, item_id, change=change)
        if stage:
            ctx["stage"] = stage
    except Exception:
        # If stage resolution fails (no environment.yaml, etc.), leave empty.
        pass

    return ctx


def _build_simple_dispatch(config, change, item_id):
    """Build the dispatch action for a simple track.

    Returns a dict with the same shape as dispatch_action (action=dispatch,
    agent=pg-build/simple, dispatch_file, dispatch_seq, report_seq,
    next_call_timeout_seconds) so the LLM orchestrator can treat it
    uniformly with the TDVG dispatch path.
    """
    ctx = _build_simple_context(config, change, item_id)
    if ctx.get("_commands_missing"):
        return {
            "action": "workflow_failed",
            "fatal": True,
            "reason": f"Simple track {item_id} 缺少 commands 配置",
        }

    # Honor build_rules targeting simple agent (previously hardcoded empty).
    _enrich_context_with_prompt_injection(ctx, config, item_id, "simple")

    # Pre-allocate dispatch + report seq so the rendered prompt can include
    # them in the report filename (e.g. `cat > 2-build/{report_seq}-{item}-...`).
    seq = _allocate_seq(change)
    dispatch_seq = _format_seq(seq)
    report_seq = _format_seq(seq + 1)
    ctx["dispatch_seq"] = dispatch_seq
    ctx["report_seq"] = report_seq

    template_str = _build_prompt_template(item_id, "simple")
    rendered = _render_prompt_template(template_str, ctx)
    content = _merge_prompt_injection(rendered, ctx)
    dispatch_path = _write_dispatch_file_with_seq(
        change,
        item_id,
        "simple",
        content,
        seq=seq,
        cycle=None,
        agent=SIMPLE_AGENT,
    )
    return {
        "action": "dispatch",
        "agent": SIMPLE_AGENT,
        "item": item_id,
        "sub": "simple",
        "attempt": 1,
        "dispatch_file": dispatch_path,
        "dispatch_seq": dispatch_seq,
        "report_seq": report_seq,
        "next_call_timeout_seconds": _compute_simple_timeout(
            ctx["commands_normalized"]
        ),
    }


# ============================================================
# Core logic — next
# ============================================================

# Items that are always appended after pipeline.order
ALWAYS_ITEMS = ["final-gate"]


def cmd_next(change):
    # Defensive: validate manifest ↔ tasks.md consistency at every entry
    valid, msg = _validate_manifest(change)
    if not valid:
        return {
            "action": "error",
            "fatal": True,
            "reason": f"manifest 校验失败: {msg}",
            "fix_hint": f"请先修复 manifest 一致性后重试: "
            f"python3 {VALIDATE_PROPOSAL_PY} manifest {change}",
        }

    config = load_config()
    order = get_pipeline_order(config, change)
    state = load_state(change)

    # If already terminal, return immediately
    if state.get("completed"):
        return {"action": "done", "status": "completed"}
    if state.get("failed"):
        return {
            "action": "workflow_failed",
            "fatal": True,
            "reason": _last_fail_reason(state),
        }

    # One-shot migration: legacy state files at change root → 2-build/.
    # Idempotent; safe to run every invocation.
    # NB: now delegated to pg_build_bootstrap (shared with v2 path) to
    # avoid drift between v1 and v2. See pg_pipeline_common.pg_build_bootstrap.
    init_commit = pg_build_bootstrap(change, state)

    # Simple-track routing is handled by cmd_detect via get_track_type() —
    # no need to rewrite tasks.md sections to noop markers anymore.
    # (Removed in v3.2: _noopify_simple_track_sections was redundant
    # because cmd_detect routes simple tracks to _execute_phase BEFORE the
    # all_noop short-circuit.)

    # NOTE (build-r Step 3): The v1 drift-detection block (lines that
    # called _validate_state_consistency / _any_open_section /
    # _last_dispatch_key / _duplicate_warning) has been removed. State is
    # now exclusively v2 PipelineState, which is the sole source of truth.
    # Without a double source of truth, drift detection is impossible —
    # and the v1 drift check was the root cause of the
    # `fix-upgrade-download-url-libvirt-missing` infinite-verify-dispatch
    # bug. See temp/build-r.md §0 and §3 Step 3 for the rationale.

    cur = state.get("current")

    # If we have a current item in waiting state, return the same action (idempotent)
    if cur and cur.get("waiting"):
        return _resume_waiting(config, change, state, cur, init_commit=init_commit)

    # No current item — detect the next one
    detect_result = pipeline_detect(change)

    if detect_result.get("error"):
        return {
            "action": "workflow_failed",
            "fatal": True,
            "reason": detect_result["error"],
        }

    item_id = detect_result.get("item")

    # All completed
    if item_id is None:
        state["current"] = {"item": "final-gate", "sub": None, "waiting": False}
        save_state(state)
        return _enter_final_gate(config, change, state)

    # Determine if track or phase
    item_type = detect_result.get("type", "track")

    if item_type == "phase":
        return _execute_phase(config, change, state, item_id)

    # Track: determine which sub-phase
    sub = detect_result.get("subPhase", "test")

    # Check for rollback context
    rb = pg_context_chain.rollback_get(change, item_id)
    has_rollback = rb.get("found", False)

    # Build dispatch context (with stage info for v3.0)
    # NB: now delegated to pg_build_dispatch_context (shared with v2 path) to
    # avoid drift between v1 and v2. See pg_pipeline_common.pg_build_dispatch_context.
    ctx, has_rollback = pg_build_dispatch_context(change, item_id, sub, config)

    # Persist state (including the init_committed marker mutated by
    # _maybe_bootstrap_init_commit above) BEFORE returning. This is the
    # earliest save_state call in the dispatch path; subsequent
    # sub_phase transitions (line 1218+ below) update current/waiting
    # flags for resume idempotency, but the init marker must land on
    # disk before the first dispatch returns to the LLM, otherwise a
    # second `cmd_next` would re-enter the bootstrap branch.

    # NOTE (build-r Step 3): Duplicate-dispatch detection via
    # _last_dispatch_key has been removed. State.json's dispatch_history
    # (in v2) is the SSOT for dispatched phases; consecutive duplicate
    # detection now derives from dispatch_history[-1] if needed.

    state["current"] = {
        "item": item_id,
        "sub": sub,
        "attempt": 1,
        "fix_cycles": 0,
        "waiting": True,
        "has_rollback": has_rollback,
    }
    save_state(state)

    # Dispatch — no auto-record commit here. The init commit (if any) has
    # already been produced by _maybe_bootstrap_init_commit above; the
    # "started" status carries no state change worth a separate commit.
    # Subsequent `record completed/failed/...` calls drive auto-record.
    return dispatch_action(
        agent=SUB_AGENTS[sub],
        item=item_id,
        sub=sub,
        context=ctx,
        attempt=1,
        init_commit=init_commit,
    )


def _resume_waiting(config, change, state, cur, init_commit=None):
    """Return the same waiting action (idempotent on next()."""
    item_id = cur["item"]
    sub = cur["sub"]
    attempt = cur.get("attempt", 1)
    has_rollback = cur.get("has_rollback", False)

    # Fix: when in fix cycle, dispatch fix agent. cur["sub"] now distinguishes
    # verify-fix ("fix") from gate-fix ("fix-gate") so we route accordingly.
    if cur.get("in_fix_cycle"):
        fix_sub = cur.get("sub", "fix")
        if fix_sub == "fix-gate":
            ctx = filter_track_context(config, item_id, "fix-gate", change=change)
            ctx["_change"] = change
            ctx["max_gate_fix_retries"] = get_track_config(config, item_id).get(
                "max_gate_fix_retries", DEFAULT_GATE_FIX_RETRIES
            )
            gate_cycles = cur.get("gate_cycles", 1)
            ctx["gate_cycles"] = gate_cycles
            ctx["cycles_remaining"] = ctx["max_gate_fix_retries"] - gate_cycles
            return dispatch_fix_gate_action(item_id, gate_cycles, ctx, config=config)
        ctx = filter_track_context(config, item_id, "fix", change=change)
        ctx["_change"] = change
        return dispatch_fix_action(item_id, cur.get("fix_cycles", 1), ctx)

    # Build dispatch context (resume path) — delegate to shared helper to
    # avoid drift with v2 path. has_rollback is reused from cur (the helper's
    # returned has_rollback reflects a fresh rollback_get; for resume we
    # honor the prior cur["has_rollback"] marker).
    ctx, _ = pg_build_dispatch_context(change, item_id, sub, config)
    # If the saved state says there's a rollback but the helper didn't find
    # one, restore the marker so downstream agents see rollback_context.
    if has_rollback and "rollback_context" not in ctx:
        rb = pg_context_chain.rollback_get(change, item_id)
        if rb and rb.get("found"):
            _enrich_context_with_rollback(ctx, rb)

    # NOTE (build-r Step 3): _duplicate_warning injection removed.
    # State.json's dispatch_history (v2) is the SSOT.

    return dispatch_action(
        agent=SUB_AGENTS.get(sub, FINAL_GATE_AGENT),
        item=item_id,
        sub=sub,
        context=ctx,
        attempt=attempt,
        init_commit=init_commit,
    )


def _execute_phase(config, change, state, item_id):
    """Execute a phase directly, or dispatch it to a sub-agent.

    Three kinds of phase items are supported:
      1. prepare_env / clean_env — environment lifecycle hooks. The runner
         resolves the environment from the stage's first track's deployment
         override (tasks.md ## Deployments) and runs the corresponding
         `script` from config.yaml's environment definition.
      2. Simple tracks (type=simple) — **dispatched to the pg-build/simple
         sub-agent** via _build_simple_dispatch. The agent executes the
         command list, with LLM-driven auto-recovery for missing deps etc.
      3. Legacy track-level phases — read `commands` from the track config
         and run them sequentially. Kept for backward compatibility.
    """
    bare = _bare_track(item_id)
    is_env_hook = bare in ("prepare_env", "clean_env")
    is_simple_track = (not is_env_hook) and get_track_type(config, item_id) == "phase"

    # Simple track path: dispatch to pg-build/simple agent. The agent is
    # responsible for executing commands and reporting SUCCESS / FAILED;
    # runner does NOT execute commands in-process anymore.
    if is_simple_track:
        # Initialize state for the simple sub (mirrors cmd_next track path).
        state["current"] = {
            "item": item_id,
            "sub": "simple",
            "attempt": 1,
            "fix_cycles": 0,
            "waiting": False,
        }
        save_state(state)
        pg_context_chain.sub_start(change, item_id, "simple")
        state["current"]["waiting"] = True
        save_state(state)
        return _build_simple_dispatch(config, change, item_id)

    if is_env_hook:
        # Resolve the stage's environment from the first track's deployment override.
        stage_name = item_id.rsplit(".", 1)[0] if "." in item_id else None
        stage_cfg = None
        for s in config.get("stages") or []:
            if s.get("name") == stage_name:
                stage_cfg = s
                break
        if stage_cfg is None:
            return {
                "action": "workflow_failed",
                "fatal": True,
                "reason": f"Cannot find stage {stage_name} for {item_id}",
            }

        stage_tracks = stage_cfg.get("tracks") or []
        if not stage_tracks:
            return {
                "action": "workflow_failed",
                "fatal": True,
                "reason": f"Stage {stage_name} has no tracks for {item_id}",
            }

        qualified_first = f"{stage_name}.{stage_tracks[0]}"
        try:
            env_name = _resolve_stage_env(change, stage_name)
        except FileNotFoundError as e:
            return {"action": "workflow_failed", "fatal": True, "reason": str(e)}
        except (KeyError, ValueError) as e:
            return {"action": "workflow_failed", "fatal": True, "reason": str(e)}
        if env_name == "__skip__":
            return {
                "action": "phase_result",
                "phase_item": item_id,
                "terminate": False,
                "environment": None,
            }

        env_cfg = (config.get("environments") or {}).get(env_name, {})
        action = env_cfg.get(bare)
        if not action:
            return {
                "action": "workflow_failed",
                "fatal": True,
                "reason": f"Environment {env_name} has no {bare} action",
            }

        script_path = action.get("script")
        if not script_path:
            return {
                "action": "workflow_failed",
                "fatal": True,
                "reason": f"Environment {env_name}.{bare} has no script",
            }

        timeout_seconds = action.get("timeout_seconds")

        args = action.get("args") or []
        # Env hooks go through pg-run-hook.py so PG_CHANGE_NAME / PG_ENV /
        # PG_STAGE / PG_SKILLS_PATH / PG_PROJECT_ROOT are guaranteed to be
        # injected. The script itself handles its own SSH / sub-orchestration
        # as needed (e.g. dev-3tier scripts handle SSH to box-1/box-2
        # internally).
        inner_cmd = (
            "bash "
            + shlex.quote(script_path)
            + (" " + " ".join(shlex.quote(str(a)) for a in args) if args else "")
        )
        spec = {
            "cmd": inner_cmd,
            "change": change,
            "stage": stage_name,
            "env": env_name,
            "hook_type": bare,  # prepare_env or clean_env
            "timeout_seconds": timeout_seconds,
            "log_path": str(_phase_log_path(change, item_id)),
            "hook_log_dir": str(
                Path(PROJECT_ROOT)
                / ".pg"
                / "changes"
                / change
                / "2-build"
                / env_name
                / "logs"
            ),
            "caller": "pg-build",
            "skill": "pg-build",
        }
        cmd = (
            f"python3 {shlex.quote(PG_HOOK_RUNNER)}"
            f" <<'EOF'\n{json.dumps(spec, indent=2)}\nEOF"
        )
        commands = [(cmd, timeout_seconds)]
        label = f"{item_id} ({env_name})"
    else:
        tc = get_track_config(config, item_id)
        commands = tc.get("commands") or []
        label = tc.get("label", item_id)
        # Simple tracks are dispatched to pg-build/simple agent above; the
        # empty-commands check is performed inside _build_simple_context.

    state["current"] = {"item": item_id, "sub": None, "waiting": False}
    save_state(state)

    pg_context_chain.phase_start(change, item_id)

    log_path = _phase_log_path(change, item_id)
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    # Track-level default timeout for simple tracks. Read from track config
    # (schema default 1800s); falls back to 1800 if config field is missing
    # entirely. None means "no timeout".
    track_default_timeout = None
    track_on_failure = "workflow_failed"  # track-level failure policy
    if is_simple_track:
        tc_for_defaults = get_track_config(config, item_id)
        track_default_timeout = tc_for_defaults.get("timeout_seconds", 1800)
        track_on_failure = tc_for_defaults.get("on_failure", "workflow_failed")

    ok = True
    last_err = ""
    for i, entry in enumerate(commands, 1):
        # Env hooks produce (cmd, timeout) tuples; simple tracks / legacy
        # tracks produce strings or dicts. Normalize to a uniform dict.
        if is_env_hook:
            # env hook path — entry is always a (cmd, timeout) tuple built
            # above (line ~1776). timeout may be None.
            cmd_str, timeout_val = entry[0], entry[1]
            per_cmd = {
                "cmd": cmd_str,
                "timeout_seconds": timeout_val,
                "on_failure": "fail",  # env hooks have no retry/continue
                "retry_max": 0,
                "retry_timeout_seconds": timeout_val,
            }
        else:
            try:
                per_cmd = normalize_simple_command(
                    entry, track_default_timeout=track_default_timeout
                )
            except ValueError as ve:
                ok = False
                last_err = f"Simple track {item_id} 命令 #{i} 配置错误: {ve}"
                break
            cmd_str = per_cmd["cmd"]
            timeout_val = per_cmd["timeout_seconds"]
        if not cmd_str or not cmd_str.strip():
            continue
        timeout_msg = f" --timeout {timeout_val}s" if timeout_val else ""
        header = f"=== {item_id} ({label}) [{ts}] #exec ===\n--- command ---\n{cmd_str}{timeout_msg}"
        # Run with retry loop if on_failure=retry.
        attempts_remaining = 1
        if is_simple_track and per_cmd["on_failure"] == "retry":
            attempts_remaining = max(1, int(per_cmd["retry_max"])) + 1
        ok_i = False
        err_i = ""
        for attempt in range(1, attempts_remaining + 1):
            attempt_timeout = (
                per_cmd["retry_timeout_seconds"]
                if (
                    is_simple_track and attempt > 1 and per_cmd["on_failure"] == "retry"
                )
                else timeout_val
            )
            attempt_header = (
                header
                if attempt == 1
                else (header + f"\n[retry {attempt - 1}/{attempts_remaining - 1}]")
            )
            ok_i, _out_i, err_i = run_bash(
                cmd_str,
                timeout_seconds=attempt_timeout,
                log_path=log_path,
                header=attempt_header,
                change=change,
                track_id=item_id,
            )
            if ok_i:
                break
            if attempt < attempts_remaining:
                print(
                    f"[warn] simple track {item_id} cmd #{i} attempt {attempt} "
                    f"failed; retrying ({attempt + 1}/{attempts_remaining}): "
                    f"{err_i}",
                    file=sys.stderr,
                )
        if not ok_i:
            # Per-command failure policy.
            policy = per_cmd["on_failure"]
            if is_simple_track and policy == "continue":
                print(
                    f"[warn] simple track {item_id} cmd #{i} failed but "
                    f"on_failure=continue; advancing: {err_i}",
                    file=sys.stderr,
                )
                continue
            # Else: 'fail' or 'retry' (after retries exhausted) — abort.
            ok = False
            last_err = err_i or f"Command #{i} failed"
            break

    summary = f"{label}: {'OK' if ok else 'FAILED'}"
    pg_context_chain.phase_end(change, item_id, summary)

    # Build phase_result for env hooks — return to LLM instead of recursing cmd_next.
    # The LLM reads the result and calls `record completed` to advance.
    phase_status = "completed" if ok else "failed"
    terminate = is_env_hook and bare == "prepare_env" and not ok
    is_cleanup = is_env_hook and bare == "clean_env" and not ok

    if ok:
        pipeline_mark(change, item_id)
        # env-hook phase_result: keep `state["current"]` populated so
        # the orchestrator's subsequent `record completed` (handled by
        # `_handle_env_hook_record`) can identify the env-hook via
        # `_is_env_hook_phase(state)` and advance the pipeline.
        #
        # We DO also write `state["completed_items"]` and
        # `state["tracks"][item_id].status = "completed"` here so that:
        #   1) If the orchestrator skips `record` and calls `next`
        #      directly, `_resume_waiting` returns the same phase_result
        #      and the next record attempt still finds the env-hook via
        #      `_is_env_hook_phase(state)` (current.waiting=True is the
        #      resume signal).
        #   2) v2's `is_track_completed(item_id)` returns True for the
        #      walk that `cmd_next` does after `_handle_env_hook_record`
        #      recurses.
        if is_env_hook:
            completed = state.get("completed_items") or []
            if item_id not in completed:
                state["completed_items"] = completed + [item_id]
            # v2 schema: also mark the v2 tracks entry as completed.
            tracks = state.get("tracks") or {}
            track_data = tracks.get(item_id)
            if track_data is None:
                tracks[item_id] = {
                    "track_id": item_id,
                    "bare": (item_id.rsplit(".", 1)[-1] if "." in item_id else item_id),
                    "label": None,
                    "status": "completed",
                    "modules": [],
                    "config_snapshot": {},
                    "phases": {},
                }
            else:
                track_data["status"] = "completed"
            state["tracks"] = tracks
        state["current"]["waiting"] = True
        save_state(state)
    else:
        state["current"] = None
        if terminate:
            state["failed"] = True
            state["fail_reason"] = f"Phase {item_id} failed: {last_err}"
        elif is_cleanup:
            print(
                f"[warn] {item_id} failed but is cleanup; advancing: {last_err}",
                file=sys.stderr,
            )
            state["completed_items"] = state.get("completed_items", []) + [item_id]
            # clean_env failure: non-blocking, mark as done and let LLM record
            return cmd_next(change)
        elif is_simple_track and track_on_failure == "continue_all":
            print(
                f"[warn] simple track {item_id} failed but on_failure="
                f"continue_all; advancing: {last_err}",
                file=sys.stderr,
            )
            state["completed_items"] = state.get("completed_items", []) + [item_id]
            # Mark as completed for downstream and continue pipeline.
            return cmd_next(change)
        else:
            return {
                "action": "workflow_failed",
                "fatal": True,
                "reason": f"Phase {item_id} failed: {last_err}",
            }
        save_state(state)

    # Build environment struct
    env_struct = None
    if is_env_hook:
        env_struct = {
            "name": env_name,
            "prepare_env_log_path": log_path
            if is_env_hook and bare == "prepare_env"
            else None,
            "prepare_env_status": phase_status if bare == "prepare_env" else None,
            "config": env_cfg,
        }

    return {
        "action": "phase_result",
        "phase_item": item_id,
        "terminate": terminate,
        "environment": env_struct,
    }


def _enter_final_gate(config, change, state):
    """Enter the final-gate phase."""
    state["current"] = {"item": "final-gate", "sub": None, "waiting": True}
    save_state(state)
    # Build final-gate context (paths to proposal/tasks/designs/reports).
    fg_ctx = _build_final_gate_context(change)
    # Pre-allocate seqs before rendering.
    seq = _allocate_seq(change)
    dispatch_seq = _format_seq(seq)
    report_seq = _format_seq(seq + 1)
    fg_ctx["dispatch_seq"] = dispatch_seq
    fg_ctx["report_seq"] = report_seq
    template_str = _build_prompt_template("final-gate", "gate")
    rendered = _render_prompt_template(template_str, fg_ctx)
    content = _merge_prompt_injection(rendered, fg_ctx)
    # final-gate is special: there is no `sub` field, but we use "gate" in
    # the dispatch file name to keep the naming rule consistent.
    dispatch_path = _write_dispatch_file_with_seq(
        change,
        "final-gate",
        "gate",
        content,
        seq=seq,
        cycle=None,
        agent=FINAL_GATE_AGENT,
    )
    return {
        "action": "dispatch_final_gate",
        "agent": FINAL_GATE_AGENT,
        "item": "final-gate",
        "dispatch_file": dispatch_path,
        "dispatch_seq": dispatch_seq,
        "report_seq": report_seq,
    }


# ============================================================
# Core logic — record
# ============================================================


def _handle_env_hook_record(config, change, state, status, summary, outputs, issues):
    """Handle `record` for an env-hook phase item (prepare_env/clean_env).

    Env-hook phases are executed inline by `_execute_phase` and leave
    `state["current"]` populated with `sub=None` after a successful run.
    The orchestrator (LLM) responds to `{action: "phase_result"}` by
    calling `record completed` (or `record failed`); this handler
    advances the pipeline from that point.

    Behavior:
      - prepare_env completed  → release current, recurse cmd_next
      - prepare_env failed     → terminal workflow_failed
      - clean_env completed    → release current, recurse cmd_next
      - clean_env failed       → warn, recurse cmd_next (non-blocking,
                                 matches `_execute_phase` is_cleanup path)

    The phase's `state["completed_items"]` and v2
    `state["tracks"][item_id].status` are written by `_execute_phase`'s
    `if ok:` branch, BEFORE this handler runs. This handler therefore
    only needs to:
      1. release `state["current"]` (so cmd_next doesn't re-execute)
      2. recurse into cmd_next(change) to fetch the next dispatch.
         The recursion honors `_use_state_v2()` so the same handler
         works under both v1 and v2 state machines.

    State writes:
      - `state["current"] = None`     (releases the in-flight slot)
      - `state["context"]["current_stage_idx"]` is advanced by cmd_next
        recursion, not here.
    """
    cur = state["current"]
    item_id = cur["item"]
    bare = item_id.rsplit(".", 1)[-1] if "." in item_id else item_id
    try:
        pg_context_chain.phase_end(
            change, item_id, f"env-hook record: {status} ({summary or ''})"
        )
    except Exception:
        # phase_end is best-effort; don't block on logging failure.
        pass

    state["current"] = None

    if status == "failed":
        if bare == "prepare_env":
            state["failed"] = True
            state["fail_reason"] = f"Phase {item_id} failed: {summary or 'unknown'}"
            save_state(state)
            return {
                "action": "workflow_failed",
                "fatal": True,
                "reason": state["fail_reason"],
            }
        # clean_env failed: non-blocking, advance
        save_state(state)
        return _dispatch_next_for_active_state(change)

    # status == "completed"
    save_state(state)
    return _dispatch_next_for_active_state(change)


def _dispatch_next_for_active_state(change):
    """Invoke cmd_next for the active state machine (v1 or v2).

    Centralizes the v1/v2 routing decision so that all internal
    recursion points (env-hook record, simple track, etc.) call the
    right next-dispatch function. Mirrors `main()`'s routing at
    line 3941-3946.
    """
    if _use_state_v2():
        from pg_runner_v2 import cmd_next_v2

        return cmd_next_v2(change)
    return cmd_next(change)


def cmd_record(change, status, report_path="", summary="", outputs="", issues=""):
    config = load_config()
    state = load_state(change)
    cur = state.get("current")

    if not cur:
        return _inject_commit(
            {
                "action": "workflow_failed",
                "fatal": True,
                "reason": "No active item to record",
            },
            change,
            "<none>",
            "<none>",
            status,
        )

    item_id = cur["item"]
    sub = cur.get("sub")
    attempt = cur.get("attempt", 1)

    # Guard 0: env-hook phase (prepare_env/clean_env).
    # Must be checked BEFORE the sub-status guard at line 3351/3357,
    # because env-hook phases leave cur.sub=None after _execute_phase and
    # would otherwise be rejected by `sub not in ALLOWED_STATUS`.
    if _is_env_hook_phase(state):
        if status not in ALLOWED_STATUS["phase"]:
            return _inject_commit(
                {
                    "action": "error",
                    "fatal": False,
                    "reason": (
                        f"record status 与 env-hook phase 不匹配: "
                        f"item={item_id!r} 不允许 status={status!r}。"
                        f"env-hook phase 仅支持: completed | failed。"
                    ),
                    "fix_hint": (
                        "env-hook phase_result 后, 编排器应使用 "
                        "`record <change> completed` 或 "
                        "`record <change> failed`。"
                    ),
                    "sub": "phase",
                    "item_id": item_id,
                },
                change,
                item_id,
                "phase",
                status,
            )
        return _inject_commit(
            _handle_env_hook_record(
                config, change, state, status, summary, outputs, issues
            ),
            change,
            item_id,
            "phase",
            status,
        )

    # Guard 1: sub-status semantic compatibility.
    # Prevents LLM from using the wrong record command for the current
    # sub-phase (e.g. `record pass` while sub=verify would silently advance
    # to gate and mark the wrong tasks.md section complete — see the
    # `fix-upgrade-download-url-libvirt-missing` regression where this
    # caused an infinite verify dispatch loop).
    if sub is not None and sub not in ALLOWED_STATUS:
        return _inject_commit(
            {
                "action": "workflow_failed",
                "fatal": True,
                "reason": f"未知 sub={sub!r}, 期望 {sorted(ALLOWED_STATUS.keys())}",
            },
            change,
            item_id,
            sub,
            status,
        )
    if sub is not None and status not in ALLOWED_STATUS.get(sub, set()):
        valid = " | ".join(sorted(ALLOWED_STATUS.get(sub, set())))
        # final-gate has no sub, use item id in message
        label = sub if sub is not None else item_id
        return _inject_commit(
            {
                "action": "error",
                "fatal": False,
                "reason": (
                    f"record status 与 sub 不匹配: sub={label!r} 不允许 status={status!r}。"
                    f"该 sub 仅支持: {valid}。"
                ),
                "fix_hint": (
                    f"请检查 tasks.md §{_track_section_label(change, item_id, sub)} "
                    f"({sub!r}) 当前状态——可能上一步用了错误的 record 命令。"
                    f"verify 子阶段完成后应使用 'record completed', "
                    f"gate 子阶段完成后应使用 'record pass'。"
                ),
                "sub": sub,
                "item_id": item_id,
            },
            change,
            item_id,
            sub,
            status,
        )

    # NOTE (build-r Step 3): Drift check + duplicate dispatch tracking
    # removed. State.json (v2) is the sole SSOT, so drift is impossible.
    # duplicate-dispatch detection derives from dispatch_history[-1] if
    # needed (see pg_runner_v2.cmd_next_v2).

    if status == "completed":
        in_fix_cycle = cur.get("in_fix_cycle", False)

        # Fix cycle completed — re-dispatch verify (don't mark tasks)
        if in_fix_cycle:
            cur["in_fix_cycle"] = False
            cur["attempt"] = cur.get("attempt", 1) + 1
            cur["waiting"] = False
            save_state(state)

            # Cycle number: gate_cycles for gate-fix path, fix_cycles for verify-fix path
            fix_cycle = cur.get("gate_cycles") or cur.get("fix_cycles", 0)
            sub_label = cur.get("sub", "fix")
            pg_context_chain.sub_end(
                change,
                item_id,
                sub_label,
                "COMPLETED",
                summary=summary,
                outputs=outputs,
                issues=issues,
                fix_cycle=fix_cycle,
            )
            pg_context_chain.sub_start(change, item_id, "verify", fix_cycle=fix_cycle)
            cur["waiting"] = True
            save_state(state)

            ctx = filter_track_context(config, item_id, "verify", change=change)
            ctx["_change"] = change
            _enrich_context_with_tasks(ctx, change, item_id, "verify")
            return _inject_commit(
                dispatch_action(
                    agent=SUB_AGENTS["verify"],
                    item=item_id,
                    sub="verify",
                    context=ctx,
                    attempt=cur["attempt"],
                ),
                change,
                item_id,
                sub,
                status,
            )

        # Normal sub-agent completed
        # Simple tracks have no :sub in tasks.md heading (sec["sub"]=None),
        # so mark without sub to match all sections for the item.
        if sub == "simple":
            pipeline_mark(change, item_id)
        else:
            pipeline_mark(change, item_id, sub)
        pg_context_chain.sub_end(
            change, item_id, sub, "COMPLETED", summary, outputs, issues
        )

        if sub in ("test", "dev"):
            return _inject_commit(
                _advance_to_next_sub(config, change, state, item_id, sub),
                change,
                item_id,
                sub,
                status,
            )
        elif sub == "verify":
            return _inject_commit(
                _advance_from_verify(config, change, state, item_id, report_path),
                change,
                item_id,
                sub,
                status,
            )
        elif sub == "gate":
            return _inject_commit(
                _advance_from_gate(config, change, state, item_id, "pass"),
                change,
                item_id,
                sub,
                status,
            )
        else:
            return _inject_commit(
                _advance_track_done(config, change, state, item_id),
                change,
                item_id,
                sub,
                status,
            )

    elif status == "failed":
        pg_context_chain.sub_end(change, item_id, sub, "FAILED", "", "", issues)

        # Fix: when in fix cycle, re-dispatch fix agent. cur["sub"] now
        # distinguishes verify-fix ("fix") from gate-fix ("fix-gate").
        if cur.get("in_fix_cycle"):
            cur["attempt"] = attempt + 1
            cur["waiting"] = False
            save_state(state)
            fix_sub = cur.get("sub", "fix")
            if fix_sub == "fix-gate":
                gate_cycle = cur.get("gate_cycles", 1)
                pg_context_chain.sub_start(
                    change, item_id, "fix-gate", fix_cycle=gate_cycle
                )
                cur["waiting"] = True
                save_state(state)
                ctx = filter_track_context(config, item_id, "fix-gate", change=change)
                ctx["_change"] = change
                ctx["gate_cycles"] = gate_cycle
                max_gate_fix = get_track_config(config, item_id).get(
                    "max_gate_fix_retries", DEFAULT_GATE_FIX_RETRIES
                )
                ctx["max_gate_fix_retries"] = max_gate_fix
                ctx["cycles_remaining"] = max_gate_fix - gate_cycle
                ctx["gate_report_path"] = track_latest_report_path(
                    change, item_id, "gate-assessment"
                ) or gate_report_path_for(change, item_id)
                return _inject_commit(
                    dispatch_fix_gate_action(item_id, gate_cycle, ctx, config=config),
                    change,
                    item_id,
                    "fix-gate",
                    status,
                )
            pg_context_chain.sub_start(change, item_id, "fix")
            cur["waiting"] = True
            save_state(state)
            fix_cycle = cur.get("fix_cycles", 1)
            ctx = filter_track_context(config, item_id, "fix", change=change)
            ctx["_change"] = change
            return _inject_commit(
                dispatch_fix_action(item_id, fix_cycle, ctx),
                change,
                item_id,
                sub,
                status,
            )

        track_cfg = get_track_config(config, item_id)
        max_retries = track_cfg.get("max_fail_retries", DEFAULT_FAIL_RETRIES)
        if attempt >= max_retries:
            state["failed"] = True
            state["fail_reason"] = (
                f"{item_id}:{sub} failed after {max_retries} attempts"
            )
            save_state(state)
            return _inject_commit(
                {
                    "action": "workflow_failed",
                    "fatal": True,
                    "reason": state["fail_reason"],
                },
                change,
                item_id,
                sub,
                status,
            )

        cur["attempt"] = attempt + 1
        cur["waiting"] = False
        save_state(state)
        pg_context_chain.sub_start(change, item_id, sub)
        cur["waiting"] = True
        save_state(state)

        ctx = filter_track_context(config, item_id, sub, change=change)
        _enrich_context_with_tasks(ctx, change, item_id, sub)
        return _inject_commit(
            dispatch_action(
                agent=SUB_AGENTS[sub],
                item=item_id,
                sub=sub,
                context=ctx,
                attempt=attempt + 1,
            ),
            change,
            item_id,
            sub,
            status,
        )

    elif status == "escalate":
        # verify requests fix
        fix_cycles = cur.get("fix_cycles", 0)
        if fix_cycles >= MAX_FIX_CYCLES:
            # Force gate with last report
            cur["waiting"] = False
            cur["sub"] = "gate"
            save_state(state)
            pg_context_chain.sub_start(change, item_id, "gate")
            cur["waiting"] = True
            save_state(state)

            ctx = filter_track_context(config, item_id, "gate", change=change)
            _enrich_context_with_tasks(ctx, change, item_id, "gate")
            return _inject_commit(
                dispatch_action(
                    agent=SUB_AGENTS["gate"],
                    item=item_id,
                    sub="gate",
                    context=ctx,
                    attempt=1,
                ),
                change,
                item_id,
                sub,
                status,
            )

        cur["in_fix_cycle"] = True
        cur["fix_cycles"] = fix_cycles + 1
        cur["sub"] = "fix"  # 区分 verify-fix (sub="fix") / gate-fix (sub="fix-gate")
        cur["waiting"] = False
        save_state(state)
        pg_context_chain.sub_start(change, item_id, "fix")
        cur["waiting"] = True
        save_state(state)

        ctx = filter_track_context(config, item_id, "fix", change=change)
        ctx["_change"] = change
        return _inject_commit(
            dispatch_fix_action(item_id, fix_cycles + 1, ctx),
            change,
            item_id,
            sub,
            status,
        )

    elif status == "pass":
        if item_id == "final-gate":
            pipeline_mark(change, "final-gate")
            pg_context_chain.sub_end(change, "final-gate", "gate", "PASS", summary)

            # Persist final state BEFORE archive: the state file at
            # `.pg/changes/<change>/2-build/.pipeline-state.json`
            # must be written into the change dir *before* archive moves it,
            # otherwise `save_state` would silently recreate the source dir
            # and `_auto_commit_on_record` would stage a second commit that
            # re-introduces the orphan `.pipeline-state.json` at the old path.
            state["current"] = None
            state["completed"] = True
            save_state(state)

            # Auto-archive: move change dir to archive/ + commit on feature branch.
            # Failures are non-fatal — done still returns, but archive_failed is
            # surfaced so the LLM/manager can decide whether to retry manually.
            archive_result = _auto_archive(change)
            commit_result = _git_commit_archive(archive_result)
            archive_status = {
                "ok": archive_result.get("ok", False),
                "target_name": archive_result.get("target_name"),
                "src": archive_result.get("src"),
                "target": archive_result.get("target"),
                "reason": archive_result.get("reason"),
                "commit": commit_result,
            }

            done_result = {
                "action": "done",
                "status": "completed",
                "archive": archive_status,
            }
            return _inject_commit(done_result, change, item_id, sub, status)
        return _inject_commit(
            _advance_from_gate(config, change, state, item_id, "pass"),
            change,
            item_id,
            sub,
            status,
        )

    elif status == "fail":
        if item_id == "final-gate":
            state["failed"] = True
            state["fail_reason"] = "Final gate assessment failed"
            save_state(state)
            return _inject_commit(
                {
                    "action": "workflow_failed",
                    "fatal": True,
                    "reason": "Final gate assessment failed",
                },
                change,
                item_id,
                sub,
                status,
            )
        return _inject_commit(
            _advance_from_gate(config, change, state, item_id, "fail"),
            change,
            item_id,
            sub,
            status,
        )

    return _inject_commit(
        {
            "action": "workflow_failed",
            "fatal": True,
            "reason": f"Unknown status: {status}",
        },
        change,
        item_id,
        sub,
        status,
    )


def _advance_to_next_sub(config, change, state, item_id, current_sub):
    """After test or dev completes, determine next sub-phase for this track."""
    cur_idx = SUB_PHASES.index(current_sub)
    if cur_idx + 1 >= len(SUB_PHASES):
        return _advance_track_done(config, change, state, item_id)

    next_sub = SUB_PHASES[cur_idx + 1]
    # Check if next sub-phase section exists and has tasks
    check_result = run_script(
        PIPELINE_STATE_PY, "check", change, item_id, change=change, track_id=item_id
    )
    # Filter to just the next sub
    next_section = None
    if not check_result.get("error"):
        for sec in check_result.get("sections", []):
            if sec.get("sub") == next_sub:
                next_section = sec
                break

    if next_section:
        if next_section.get("noop"):
            # All `- 无` — skip
            return _advance_to_next_sub(config, change, state, item_id, next_sub)
        if next_section.get("unchecked", 0) == 0 and next_section.get("checked", 0) > 0:
            # Already completed — skip
            return _advance_to_next_sub(config, change, state, item_id, next_sub)

    # Reset attempt for new sub-phase
    state["current"] = {
        "item": item_id,
        "sub": next_sub,
        "attempt": 1,
        "fix_cycles": 0,
        "waiting": False,
        "has_rollback": False,
    }
    save_state(state)

    pg_context_chain.sub_start(change, item_id, next_sub)
    state["current"]["waiting"] = True
    save_state(state)

    ctx = filter_track_context(config, item_id, next_sub, change=change)
    ctx["_change"] = change
    _enrich_context_with_tasks(ctx, change, item_id, next_sub)
    return dispatch_action(
        agent=SUB_AGENTS[next_sub],
        item=item_id,
        sub=next_sub,
        context=ctx,
        attempt=1,
    )


def _track_section_label(change, item_id, sub=None):
    """Return a human-friendly label for a tasks.md section.

    If `sub` is given, return that sub's label (e.g. '3 (verify)').
    Otherwise return the first section's label. Falls back to bare track id
    if parse fails.
    """
    sections, _, module = _load_tasks_sections(change)
    if sections:
        secs = _find_track_sections(sections, item_id)
        if secs:
            target = secs[0]
            if sub is not None:
                for s in secs:
                    if s.get("sub") == sub:
                        target = s
                        break
            return f"{target.get('order', '?')} ({target.get('sub', '?')})"
    return _bare_track(item_id)


def _load_pipeline_state_module():
    """Lazy-load pg-pipeline-state.py via importlib (file has a hyphen).

    Returns the loaded module, or None if it can't be loaded.
    """
    if getattr(_load_pipeline_state_module, "_cached", None) is not None:
        return _load_pipeline_state_module._cached
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pg_pipeline_state", PIPELINE_STATE_PY
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    _load_pipeline_state_module._cached = module
    return module


def _load_tasks_sections(change):
    """Wrapper around pg_pipeline_state.parse_tasks with error suppression.

    Returns (sections, lines, module) or (None, None, None) on error.
    """
    module = _load_pipeline_state_module()
    if module is None:
        return None, None, None
    try:
        tasks_path = module.get_tasks_path(change)
        sections, lines = module.parse_tasks(tasks_path)
        return sections, lines, module
    except Exception:
        return None, None, None


def _find_track_sections(sections, item_id):
    """Wrapper around pg_pipeline_state.find_sections_for_item."""
    module = _load_pipeline_state_module()
    if module is None:
        return []
    try:
        return module.find_sections_for_item(sections, item_id)
    except Exception:
        return []


# NOTE (build-r Step 3): _validate_state_consistency and _any_open_section
# were deleted. v2 state.json is the sole SSOT; without double source of
# truth, drift detection is impossible and unnecessary. The v1 functions
# are preserved here as a comment for historical reference — see git
# history (commit 5d1a742) for the full implementation.


def _advance_from_verify(config, change, state, item_id, report_path):
    """After verify PROCEED, move to gate."""
    cur = state["current"]
    cur["waiting"] = False
    cur["sub"] = "gate"
    cur["attempt"] = 1
    cur["gate_cycles"] = 0
    save_state(state)

    pg_context_chain.sub_start(change, item_id, "gate")
    cur["waiting"] = True
    save_state(state)

    ctx = filter_track_context(config, item_id, "gate", change=change)
    ctx["_change"] = change
    if report_path:
        ctx["report_path"] = report_path
    _enrich_context_with_tasks(ctx, change, item_id, "gate")

    return dispatch_action(
        agent=SUB_AGENTS["gate"],
        item=item_id,
        sub="gate",
        context=ctx,
        attempt=1,
    )


def _advance_from_gate(config, change, state, item_id, verdict):
    """Handle gate PASS or FAIL."""
    cur = state["current"]

    if verdict == "pass":
        pipeline_mark(change, item_id, "gate")
        pg_context_chain.sub_end(
            change, item_id, "gate", "PASS", f"Gate pass for {item_id}"
        )
        cur["waiting"] = False
        state["current"] = None
        state["completed_items"] = state.get("completed_items", []) + [item_id]
        save_state(state)
        # Advance to next pipeline item
        return cmd_next(change)

    else:
        # gate FAIL — enter gate-fix loop
        track_cfg = get_track_config(config, item_id)
        max_gate_fix = track_cfg.get("max_gate_fix_retries", DEFAULT_GATE_FIX_RETRIES)
        gate_cycles = cur.get("gate_cycles", 0) + 1
        cur["gate_cycles"] = gate_cycles

        # Locate the just-written gate report for rollback (latest {track}-{N}-gate-assessment.md)
        gate_report_path = track_latest_report_path(change, item_id, "gate-assessment")
        if gate_report_path is None:
            gate_report_path = gate_report_path_for(change, item_id)
        pipeline_gate_rollback(change, item_id, gate_report_path)

        # Record context chain
        pg_context_chain.sub_end(
            change,
            item_id,
            "gate",
            "FAIL",
            summary=f"Gate FAIL cycle {gate_cycles}/{max_gate_fix}",
        )
        pg_context_chain.rollback_set(
            change,
            item_id,
            f"Gate assessment failed for {item_id} (cycle {gate_cycles}/{max_gate_fix})",
            os.path.basename(gate_report_path),
            level="gate-cycle",
        )

        if gate_cycles >= max_gate_fix:
            # Exhausted: record accepted gaps, continue pipeline
            _record_accepted_gaps(change, item_id, gate_report_path, max_gate_fix)
            pg_context_chain.sub_start(change, item_id, "gate", fix_cycle=gate_cycles)
            cur["waiting"] = False
            state["current"] = None
            state["completed_items"] = state.get("completed_items", []) + [item_id]
            save_state(state)
            return cmd_next(change)

        # Not exhausted: dispatch fix-gate agent
        cur["in_fix_cycle"] = True
        cur["sub"] = "fix-gate"  # ← 区分 verify-fix / gate-fix
        cur["waiting"] = False
        save_state(state)

        pg_context_chain.sub_start(change, item_id, "fix-gate", fix_cycle=gate_cycles)
        cur["waiting"] = True
        save_state(state)

        ctx = filter_track_context(config, item_id, "fix-gate", change=change)
        ctx["gate_cycles"] = gate_cycles
        ctx["cycles_remaining"] = max_gate_fix - gate_cycles
        ctx["gate_report_path"] = gate_report_path
        ctx["max_gate_fix_retries"] = max_gate_fix
        ctx["_change"] = change  # 喂给 prompt 模板的 _change

        result = dispatch_fix_gate_action(item_id, gate_cycles, ctx, config=config)
        return _inject_commit(result, change, item_id, "fix-gate", "fail")


def _advance_track_done(config, change, state, item_id):
    """Mark track as complete, advance to next item."""
    state["current"] = None
    state["completed_items"] = state.get("completed_items", []) + [item_id]
    save_state(state)
    return cmd_next(change)


def _last_fail_reason(state):
    return state.get("fail_reason", "Unknown failure")


def cmd_check(change, item):
    result = run_script(
        PIPELINE_STATE_PY, "check", change, item, change=change, track_id=item
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_progress(change):
    result = run_script(PIPELINE_STATE_PY, "progress", change, change=change)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ============================================================
# invoke-hook (LLM-facing CLI) — thin wrapper, body lives in pg-invoke-hook.py
# ============================================================
#
# 历史: 该子命令原本直接内联在 pg-pipeline-runner.py (v3.1 之前), 但
#   - pg-pipeline-runner.py 同时承担"编排状态机"(next/record) 与
#     "hook executor"(invoke-hook) 两类职责, 关注点混杂.
#   - pg-fix-issue / pg-regression 想复用同一入口, 但只能 import 或路径硬编码
#     pg-pipeline-runner.py, 导致 SKILL 之间互相依赖.
#
# 重构 (v3.2): 把 executor 主体抽出到 runtime 层独立 CLI
#   .pg/skills/src/runtime/bin/pg-invoke-hook.py
#   pg-build / pg-fix-issue / pg-regression 三个 SKILL 统一调用该 CLI.
#   本文件保留同名子命令 (thin wrapper), 转发到 pg-invoke-hook.py, 保证:
#     - 旧 SKILL prompt / 旧 agent prompt / 旧测试脚本中
#       `pg-pipeline-runner.py invoke-hook ...` 调用形式 100% 兼容.
#     - 新代码统一写 `pg-invoke-hook.py invoke-hook ...`.
#     - 当 main() 收到 "invoke-hook" 子命令时, 通过 subprocess 转发,
#       不再做任何 yaml 解析 / spec 渲染.
# ============================================================


def cmd_invoke_hook(argv):
    """Thin wrapper — delegate to runtime-layer pg-invoke-hook.py.

    历史: v3.1 之前, 此函数直接解析 yaml + 渲染 spec + spawn pg-run-hook.py.
    现已迁出到 .pg/skills/src/runtime/bin/pg-invoke-hook.py, 详见该文件顶部
    docstring. 本函数仅负责 subprocess 转发 + 透传 exit code, 行为与 v3.1
    完全等价.

    Args:
        argv: 完整 sys.argv (含程序名 + "invoke-hook" 子命令). main() 传入
              sys.argv; tests 传入 mock 的 sys.argv.
    """
    pg_invoke_hook = os.path.join(
        PROJECT_ROOT, ".pg", "skills", "src", "runtime", "bin", "pg-invoke-hook.py"
    )
    if not os.path.isfile(pg_invoke_hook):
        sys.stderr.write(
            f"Error: pg-invoke-hook.py not found at {pg_invoke_hook}\n"
            f"  (expect: pg-skills subtree synced via `pg upgrade`)\n"
        )
        sys.exit(2)

    proc = subprocess.run(
        ["python3", pg_invoke_hook, *argv[1:]],  # 透传 "invoke-hook" + 后续 flags
        cwd=PROJECT_ROOT,
    )
    # 透传 exit code, 与 v3.1 行为一致.
    sys.exit(proc.returncode)


# ============================================================
# Main
# ============================================================


def cmd_prepare_env_status(change, stage_name=None):
    """Print JSON array of prepare_env status for required stages.

    Usage:
      python3 pg-pipeline-runner.py prepare-env-status <change>
      python3 pg-pipeline-runner.py prepare-env-status <change> <stage_name>

    Output: JSON array (always array, even for single stage query).
    Each element: {"stage": "<name>", "prepare": {"status", "log_path", "message"}}
    """
    config = load_config()
    out = []
    for stage_cfg in config.get("stages") or []:
        s_name = stage_cfg.get("name")
        if stage_name and s_name != stage_name:
            continue
        if not bool((stage_cfg.get("environment") or {}).get("required", False)):
            continue
        out.append({"stage": s_name, "prepare": _build_prepare_status(change, s_name)})
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main():
    if len(sys.argv) < 2:
        print("错误: 缺少参数", file=sys.stderr)
        print("用法:", file=sys.stderr)
        print("  python3 pg-pipeline-runner.py next <change>", file=sys.stderr)
        print(
            "  python3 pg-pipeline-runner.py record <change> <status> [report_path]",
            file=sys.stderr,
        )
        print("  python3 pg-pipeline-runner.py check <change> <item>", file=sys.stderr)
        print("  python3 pg-pipeline-runner.py progress <change>", file=sys.stderr)
        print(
            "  python3 pg-pipeline-runner.py prepare-env-status <change> [stage_name]",
            file=sys.stderr,
        )
        # 历史兼容: invoke-hook 仍作为 pg-pipeline-runner.py 子命令可用 (thin wrapper
        # 转发到 .pg/skills/src/runtime/bin/pg-invoke-hook.py). 新代码统一用新路径.
        print(
            "  python3 pg-pipeline-runner.py invoke-hook --session <S> --env <ENV> --role <ROLE> --instance <I> --action <A> [--stage <ST>] [--tail-lines <N>]   (legacy, forwards to pg-invoke-hook.py)",
            file=sys.stderr,
        )
        print(
            "  python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py invoke-hook ...   (canonical)",
            file=sys.stderr,
        )
        sys.exit(1)

    command = sys.argv[1]

    # invoke-hook is the LLM-facing entry; its own argv parsing is done
    # by argparse inside cmd_invoke_hook (it doesn't need a <change> arg
    # at position 2 the way next/record/check do).
    if command == "invoke-hook":
        cmd_invoke_hook(sys.argv)
        return

    if len(sys.argv) < 3:
        print("错误: 缺少 <change> 参数", file=sys.stderr)
        print(
            "用法: python3 pg-pipeline-runner.py <command> <change> ...",
            file=sys.stderr,
        )
        sys.exit(1)

    change = sys.argv[2]

    VALID_COMMANDS = {"next", "record", "check", "progress", "prepare-env-status"}
    if command not in VALID_COMMANDS:
        print(f"错误: 未知命令 '{command}'", file=sys.stderr)
        print(
            f"有效命令: {', '.join(sorted(VALID_COMMANDS | {'invoke-hook'}))}",
            file=sys.stderr,
        )
        print("用法: python3 pg-pipeline-runner.py next <change>", file=sys.stderr)
        sys.exit(1)

    if command == "next":
        if _use_state_v2():
            from pg_runner_v2 import cmd_next_v2

            result = cmd_next_v2(change)
        else:
            result = cmd_next(change)
    elif command == "record":
        if len(sys.argv) < 4:
            print("错误: record 命令缺少 <status> 参数", file=sys.stderr)
            print(
                "用法: python3 pg-pipeline-runner.py record <change> <status> [report_path] [summary] [outputs] [issues]",
                file=sys.stderr,
            )
            print(
                "status: completed | failed | escalate | pass | fail", file=sys.stderr
            )
            sys.exit(1)
        status = sys.argv[3]
        VALID_STATUSES = {"completed", "failed", "escalate", "pass", "fail"}
        if status not in VALID_STATUSES:
            print(f"错误: 无效 status '{status}'", file=sys.stderr)
            print(f"有效值: {', '.join(sorted(VALID_STATUSES))}", file=sys.stderr)
            sys.exit(1)
        report_path = sys.argv[4] if len(sys.argv) > 4 else ""
        summary = sys.argv[5] if len(sys.argv) > 5 else ""
        outputs = sys.argv[6] if len(sys.argv) > 6 else ""
        issues = sys.argv[7] if len(sys.argv) > 7 else ""
        if _use_state_v2():
            from pg_runner_v2 import cmd_record_v2

            result = cmd_record_v2(
                change, status, report_path, summary, outputs, issues
            )
        else:
            result = cmd_record(change, status, report_path, summary, outputs, issues)
    elif command == "check":
        item = sys.argv[3] if len(sys.argv) > 3 else None
        cmd_check(change, item)
        return
    elif command == "progress":
        cmd_progress(change)
        return
    elif command == "prepare-env-status":
        stage_name = sys.argv[3] if len(sys.argv) > 3 else None
        cmd_prepare_env_status(change, stage_name)
        return
    else:
        sys.exit(1)  # unreachable

    if command in ("next", "record") and isinstance(result, dict):
        config = load_config()
        result = _inject_next_call_timeout(result, config)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
