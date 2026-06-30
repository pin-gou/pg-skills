#!/usr/bin/env python3
"""pg_pipeline_common.py — Shared pure functions for pipeline state & validation.

Extracted from pg-pipeline-state.py to avoid parser duplication between
pg-pipeline-state.py and pg-validate-tasks.py. Contains only pure functions
(zero side effects: no file writes, no sys.exit, no print to stdout).

All functions are byte-for-byte identical to the original sources in
pg-pipeline-state.py (commit 87f48a9 baseline)."""

import os
import re
import shlex

try:
    import yaml
except ImportError:
    yaml = None


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
    # Phase 2+ 优先读 .pg/project.yaml, fallback 到 pg-spec-deprecated/config.yaml (双轨期兼容)
    return (os.path.isfile(os.path.join(path, ".pg", "project.yaml"))
            or os.path.isfile(os.path.join(path, "pg-spec-deprecated", "config.yaml"))
            or os.path.isfile(os.path.join(path, "pg-spec", "config.yaml")))


PROJECT_ROOT = find_project_root()
CONFIG_PATH = os.path.join(PROJECT_ROOT, ".pg/project.yaml")
CHANGES_DIR = os.path.join(PROJECT_ROOT, ".pg", "changes")


# ============================================================
# Loading
# ============================================================

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_tasks_path(change):
    return os.path.join(CHANGES_DIR, change, "tasks.md")


def _bare_track(qualified):
    """Strip stage prefix from qualified item name.
    'dev-isolated.backend' -> 'backend', 'real-integration' -> 'real-integration'
    """
    return qualified.rsplit(".", 1)[1] if "." in qualified else qualified


def _track_matches(sec_item, track):
    """Check if a section item matches a track name (qualified or bare)."""
    if sec_item == track:
        return True
    if sec_item.endswith(f".{track}"):
        return True
    bare = track.rsplit(".", 1)[1] if "." in track else None
    if bare is not None and sec_item == bare:
        return True
    return False


def _read_environment_yaml(change):
    """Read .pg/changes/<change>/environment.yaml.

    Returns a dict mapping stage-name to environment-name (or "skip").

    Raises:
        FileNotFoundError: if environment.yaml does not exist for the change.
        yaml.YAMLError / ValueError: on parse errors (caller decides severity).
    """
    yaml_path = os.path.join(CHANGES_DIR, change, "environment.yaml")
    if not os.path.isfile(yaml_path):
        raise FileNotFoundError(
            f".pg/changes/{change}/environment.yaml 不存在, "
            f"必须由 pg-propose 生成."
        )
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"environment.yaml 顶层必须是 per-stage map (dict), 实际: {type(data).__name__}"
        )
    return {str(k): str(v) for k, v in data.items()}


def get_pipeline_order(config, change=None):
    stages = config.get("stages") or []
    order = []
    env_map = {}
    if change:
        try:
            env_map = _read_environment_yaml(change)
        except FileNotFoundError:
            pass

    for stage in stages:
        stage_name = stage.get("name", "")
        requires_environment = bool((stage.get("environment") or {}).get("required", False))
        stage_tracks = stage.get("tracks") or []

        prepare_env_item = None
        clean_env_item = None
        stage_env_name = env_map.get(stage_name)
        if requires_environment and stage_env_name and stage_env_name != "skip":
            env_cfg = (config.get("environments") or {}).get(stage_env_name, {})
            if env_cfg.get("prepare_env"):
                prepare_env_item = f"{stage_name}.prepare_env"
            if env_cfg.get("clean_env"):
                clean_env_item = f"{stage_name}.clean_env"

        if prepare_env_item:
            order.append(prepare_env_item)

        for t in stage_tracks:
            qualified = f"{stage_name}.{t}" if stage_name else t
            if stage_env_name == "skip":
                continue
            order.append(qualified)

        if clean_env_item:
            order.append(clean_env_item)
    return order


def get_track_type(config, item):
    """Classify an order item as 'track' (TDVG sequence) or 'phase' (direct execution).

    'phase' is reserved for items whose commands the runner dispatches
    without going through the TDVG sub-agent sequence. Two kinds:

      1. Environment lifecycle hooks: prepare_env / clean_env (handled by
         environments.<env>.prepare_env / clean_env scripts).
      2. Simple tracks: tracks.<id>.type == "simple" — runner dispatches
         the pg-build/simple sub-agent to execute tracks.<id>.commands.

    Standard tracks always go through the TDVG sequence (test → dev →
    verify → gate).

    The `item` argument may be either a bare track id (e.g. "openapi-gen")
    or a qualified item id (e.g. "dev.openapi-gen", as used by
    cmd_detect and pipeline_detect). Both forms must classify correctly.
    """
    tracks = config.get("tracks") or {}
    # Try the literal key first (bare form), then strip any stage prefix
    # to look up the bare track id (qualified form).
    bare = item.rsplit(".", 1)[-1] if "." in item else item
    track_id = item if item in tracks else bare
    if track_id in tracks:
        track_cfg = tracks[track_id] or {}
        if track_cfg.get("type") == "simple":
            return "phase"
        return "track"
    return "track"


def normalize_simple_command(entry, track_default_timeout=None):
    """Normalize a simple-track commands entry to a canonical dict.

    Users may write commands as plain strings (legacy/short form) or as
    objects with explicit timeout and failure-handling. This helper is the
    boundary between the YAML config and the runner's internal loop, so the
    loop never has to branch on entry shape again.

    Accepted shapes:
      - "echo hi"   (string) — shorthand; uses track defaults, on_failure=fail.
      - {"cmd": "...", "timeout_seconds": 60, "on_failure": "continue",
         "retry_max": 2, "retry_timeout_seconds": 30}

    Resolved timeout precedence (highest wins):
      1. command.timeout_seconds if not None
      2. track.timeout_seconds (default 1800s) if not None
      3. None (no timeout — caller's responsibility)

    Resolved on_failure precedence:
      1. command.on_failure if present (override track default)
      2. "fail" (per-command default; track-level policy is applied by the
         runner after collecting all per-command failures)

    Resolved retry_timeout_seconds:
      1. command.retry_timeout_seconds if not None
      2. command.timeout_seconds (so retried commands inherit the same cap
         unless caller explicitly sets a shorter per-retry cap)

    Args:
        entry: a string or dict from the track config.
        track_default_timeout: int/None — track.timeout_seconds value (may be
            None for "no track default"); typically 1800 from schema.

    Returns:
        dict with keys: cmd, timeout_seconds, on_failure, retry_max,
        retry_timeout_seconds. Always has all keys so callers don't have to
        branch on presence.

    Raises:
        ValueError: if entry is not a string or dict, or if dict is missing
        the required `cmd` field, or if cmd is empty.
    """
    if isinstance(entry, str):
        cmd = entry
        timeout = None  # signal to runner to fall back to track default
        on_failure = "fail"
        retry_max = 2
        retry_timeout = None
    elif isinstance(entry, dict):
        if "cmd" not in entry:
            raise ValueError(
                f"Simple-track command object missing required 'cmd' field: {entry!r}")
        cmd = entry["cmd"]
        if not isinstance(cmd, str) or not cmd.strip():
            raise ValueError(
                f"Simple-track command 'cmd' must be a non-empty string: {entry!r}")
        timeout = entry.get("timeout_seconds")
        on_failure = entry.get("on_failure", "fail")
        if on_failure not in ("fail", "continue", "retry"):
            raise ValueError(
                f"Simple-track command on_failure must be one of "
                f"fail/continue/retry; got {on_failure!r}")
        retry_max = entry.get("retry_max", 2)
        retry_timeout = entry.get("retry_timeout_seconds")
    else:
        raise ValueError(
            f"Simple-track command entries must be string or dict; got "
            f"{type(entry).__name__}: {entry!r}")

    # Apply timeout precedence: explicit command value wins; otherwise use
    # track default; otherwise None (no timeout).
    if timeout is None:
        timeout = track_default_timeout

    # Retry timeout: explicit retry value wins; otherwise fall back to the
    # main timeout (so retried commands inherit the same cap).
    if retry_timeout is None:
        retry_timeout = timeout

    return {
        "cmd": cmd,
        "timeout_seconds": timeout,
        "on_failure": on_failure,
        "retry_max": retry_max,
        "retry_timeout_seconds": retry_timeout,
    }


def normalize_module_command(entry, module_default_timeout=None):
    """Normalize a module-command entry (build/lint/test.<key>) to canonical dict.

    Distinct from normalize_simple_command because module commands have no
    on_failure/retry policy — failure means the track is broken and the
    runner escalates via the fix-agent path. Only timeout is needed.

    Accepted shapes:
      - "cd foo && mvn package"  (string shorthand)
      - {"cmd": "...", "timeout_seconds": 60}

    Resolved timeout precedence (highest wins):
      1. command.timeout_seconds if not None
      2. module.timeout_seconds (default 1800s from schema) if not None
      3. None — caller decides whether to render a plain command (no wrap)
         or refuse to run. The runner always renders a timeout when timeout
         is None, defaulting to schema default 1800.

    Args:
        entry: a string or dict from modules.<m>.build / lint / test.<key>.
        module_default_timeout: int/None — modules.<m>.timeout_seconds value;
            defaults to 1800 in schema. May be None for "no module default".

    Returns:
        dict with keys: cmd, timeout_seconds. timeout_seconds is always an
        int (never None) — schema guarantees a default of 1800.

    Raises:
        ValueError: if entry is not a string or dict, or if dict is missing
        the required `cmd` field, or if cmd is empty.
    """
    if isinstance(entry, str):
        cmd = entry
        timeout = None
    elif isinstance(entry, dict):
        if "cmd" not in entry:
            raise ValueError(
                f"Module command object missing required 'cmd' field: {entry!r}")
        cmd = entry["cmd"]
        if not isinstance(cmd, str) or not cmd.strip():
            raise ValueError(
                f"Module command 'cmd' must be a non-empty string: {entry!r}")
        timeout = entry.get("timeout_seconds")
    else:
        raise ValueError(
            f"Module command entries must be string or dict; got "
            f"{type(entry).__name__}: {entry!r}")

    if timeout is None:
        timeout = module_default_timeout
    if timeout is None:
        # Schema default 1800; applied here so renderer never sees None.
        timeout = 1800

    return {
        "cmd": cmd,
        "timeout_seconds": int(timeout),
    }


def render_module_command(normalized):
    """Render a normalized module command as a timeout-wrapped shell string.

    The runner hands this string to sub-agents (dev/test) verbatim. They
    invoke it via the Bash tool; the `timeout` command enforces the cap
    and returns exit 124 on expiry, which the sub-agent surfaces as a
    command failure and the runner escalates to the fix agent.

    Output format:
        timeout <N> bash -c '<cmd>'

    The single-quote wrapping is safe for typical shell pipelines (cd && cmd)
    but breaks for commands containing literal single quotes. None of the
    current 6 modules use literal single quotes; if needed later, switch
    to a here-doc or shlex.quote.

    Args:
        normalized: dict from normalize_module_command with keys cmd and
            timeout_seconds (both present, timeout_seconds is int).

    Returns:
        str — the timeout-prefixed shell command.
    """
    cmd = normalized["cmd"]
    timeout = normalized["timeout_seconds"]
    return f"timeout {timeout} bash -c {shlex.quote(cmd)}"


# ============================================================
# tasks.md parser
# ============================================================

HEADING_RE = re.compile(r"^## (\d+)\.\s+(.+)$")
# Code block state tracking (for parse_tasks_sections)
_CODE_BLOCK_MARKER_RE = re.compile(r"^```")


def parse_tasks_sections(tasks_path):
    """Parse tasks.md into sections with code-block-aware heading detection.

    Returns list of dicts:
      heading      str — full heading line (e.g. "## 1. dev.backend:test - ...")
      section_key  str — heading after "## " (stable key for manifest reference)
      body         str — content lines between this heading and next heading
      start_line   int — 0-based line index of the heading line
      end_line     int — 0-based line index of the next heading (or total lines)

    Sections are in document order.
    """
    with open(tasks_path, encoding="utf-8") as f:
        all_lines = f.readlines()

    sections = []
    in_code_block = False
    for i, line in enumerate(all_lines):
        if _CODE_BLOCK_MARKER_RE.match(line.strip()):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        m = HEADING_RE.match(line)
        if not m:
            continue
        if sections:
            sections[-1]["end_line"] = i
        sections.append(dict(
            heading=line.rstrip("\n"),
            section_key=f"{m.group(1)}. {m.group(2).strip()}",
            body="",
            start_line=i,
            end_line=len(all_lines),
        ))

    for sec in sections:
        sec["body"] = "".join(all_lines[sec["start_line"] + 1: sec["end_line"]])

    return sections


def parse_tasks(tasks_path):
    """Parse tasks.md into a list of Section dicts.

    Each Section:
      order         int       — section number (## N.)
      heading_tail  str       — everything after "## N. "
      item          str       — pipeline item id (e.g. "backend")
      sub           str|None  — sub-phase ("test"/"dev"/"verify"/"gate") or None
      label         str       — display label
      start_line    int       — 0-based index of the heading line
      end_line      int       — 0-based index of the next heading (or EOF)
      lines         [str]     — content lines between sections (excl. heading)
    """
    with open(tasks_path, encoding="utf-8") as f:
        all_lines = f.readlines()

    sections = []
    for i, line in enumerate(all_lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        order = int(m.group(1))
        heading_tail = m.group(2).strip()
        if sections:
            sections[-1]["end_line"] = i
        item, sub, label = _parse_heading(heading_tail)
        sections.append(dict(
            order=order,
            heading_tail=heading_tail,
            item=item,
            sub=sub,
            label=label,
            start_line=i,
            end_line=len(all_lines),
            lines=[],
        ))

    for sec in sections:
        sec["lines"] = all_lines[sec["start_line"] + 1: sec["end_line"]]

    return sections, all_lines


# Match "backend:test - 后端测试先行"  →  (item, sub, label)
_TRACK_HEADING_RE = re.compile(r"^([a-zA-Z0-9_.-]+):([a-zA-Z0-9_-]+)\s*-\s*(.+)$")
# Match "proto-compile - Proto编译"     →  (item, None, label)
# Also match "stage.track - ... ..." (e.g. simple-track heading without :sub).
# Simple tracks have no TDVG sub-phase, so their heading is "stage.track - label"
# rather than "stage.track:sub - label". We must allow '.' in the item part.
_PHASE_HEADING_RE = re.compile(r"^([a-zA-Z0-9_.-]+)\s*-\s*(.+)$")


def _parse_heading(tail):
    m = _TRACK_HEADING_RE.match(tail)
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    m = _PHASE_HEADING_RE.match(tail)
    if m:
        item = m.group(1)
        # Strip any stage prefix to keep `sec["item"]` as the bare track id.
        # The qualified form (e.g. "dev.openapi-gen") is reconstructed by
        # callers via `get_pipeline_order` when needed.
        bare = item.rsplit(".", 1)[-1] if "." in item else item
        return bare, None, m.group(2).strip()
    return tail, None, None


def count_tasks(lines):
    """Count checkbox states in a list of lines.

    Returns (unchecked, checked, all_noop).
    all_noop is True when every task line is literally "- 无" or starts with "- 无" (comment allowed).
    """
    unchecked = 0
    checked = 0
    noop_lines = 0
    noop_re = re.compile(r'^\s*-\s*无')

    for line in lines:
        s = line.strip()
        if s.startswith("- [ ]"):
            unchecked += 1
        elif s.startswith("- [x]"):
            checked += 1
        elif noop_re.match(s):
            noop_lines += 1

    has_any_task = unchecked + checked > 0
    all_noop = noop_lines > 0 and not has_any_task
    return unchecked, checked, all_noop


# ============================================================
# Section matching helpers
# ============================================================

def find_sections_for_item(sections, item, sub=None):
    """Return sub-sections matching item (and optionally sub).
    Supports qualified (dev-isolated.backend) and bare (backend) names."""
    return [
        sec for sec in sections
        if _track_matches(sec["item"], item)
           and (sub is None or sec["sub"] == sub)
    ]


def _item_sections_with_status(sections, item):
    """Return (sections_list, unchecked_total, checked_total, all_noop)."""
    item_sections = find_sections_for_item(sections, item)
    unchecked_total = 0
    checked_total = 0
    all_noop = True
    for sec in item_sections:
        un, ch, noop = count_tasks(sec["lines"])
        unchecked_total += un
        checked_total += ch
        if not noop:
            all_noop = False
    return item_sections, unchecked_total, checked_total, all_noop


def _item_status(unchecked_total, checked_total, all_noop, has_sections):
    if not has_sections:
        return "not_found"
    if all_noop:
        return "skip"
    if unchecked_total == 0:
        return "completed"
    if checked_total > 0:
        return "in_progress"
    return "pending"


def _has_any_section(sections, item):
    return any(_track_matches(sec["item"], item) for sec in sections)


def _section_status(unchecked, checked, noop):
    if noop:
        return "skip"
    if unchecked == 0 and checked == 0:
        return "no_tasks"
    if unchecked == 0:
        return "completed"
    if checked > 0:
        return "in_progress"
    return "pending"


# ============================================================
# Build bootstrap shared helpers
# ============================================================
#
# 这些 helper 抽取自 pg-pipeline-runner.py (v1 cmd_next/cmd_record) 与
# pg_runner_v2.py (v2 cmd_next_v2/cmd_record_v2) 的共有副作用逻辑,
# 解决 v2 重构时丢函数的回归问题 (build-r Step 3 漂移).
#
# 设计要点:
#   - pg_build_bootstrap / pg_build_dispatch_context: v1 与 v2 都调用同一份
#   - pg_build_record_log: 仅 v2 调用 (v1 保留原 17 处散落调用, 按用户决定)
#   - state 参数 type-dispatch: 同时支持 v1 state dict 与 v2 PipelineState
#   - 失败语义: 全部容错, 任何 helper 异常不阻塞 dispatch

import subprocess as _subprocess
import sys as _sys


class EnvHookError(Exception):
    """Raised when an env-hook phase (prepare_env / clean_env) execution fails.

    Used by execute_env_hook_inline() to signal non-recoverable env-hook failure
    to callers (pg_build_bootstrap, cmd_next_v2). The exception carries the
    log path and exit code so the runner can surface them via the
    `env_hook_failed` action.

    Attributes:
        phase_name: "prepare_env" or "clean_env".
        log_path: absolute path to the hook's stdout/stderr log.
        exit_code: int non-zero return from the hook script.
    """

    def __init__(self, phase_name: str, log_path: str, exit_code: int):
        self.phase_name = phase_name
        self.log_path = log_path
        self.exit_code = exit_code
        super().__init__(
            f"env-hook {phase_name} failed (exit_code={exit_code}, "
            f"log={log_path})")


def execute_env_hook_inline(change: str, phase_name: str) -> dict:
    """Execute a prepare_env/clean_env hook inline during bootstrap.

    Resolves the first stage's environment (via runner._resolve_stage_env),
    reads its `prepare_env` or `clean_env` action from project.yaml, and runs
    the hook script synchronously. On success, writes phase_start/phase_end
    to context-chain.md and returns a result dict. On failure, returns a
    result dict with success=False (caller decides whether to raise).

    This helper **does not** raise EnvHookError — it returns the result so
    pg_build_bootstrap() can decide whether the failure should abort the
    bootstrap. The caller (pg_build_bootstrap) is responsible for raising
    EnvHookError when prepare_env fails (since that aborts dispatch).

    Args:
        change: change name.
        phase_name: "prepare_env" or "clean_env".

    Returns:
        dict with keys:
          - success (bool): True iff the hook exited 0.
          - skipped (bool): True iff no hook configured for this phase.
          - log_path (str|None): absolute path to hook log, when run.
          - exit_code (int|None): hook exit code (0 on success, non-zero on fail).
          - env_name (str|None): resolved environment name.
          - phase_item (str|None): qualified item id like "dev.prepare_env".
          - error (str|None): error message if success=False.
    """
    if phase_name not in ("prepare_env", "clean_env"):
        return {"success": False, "skipped": False,
                "error": f"invalid phase_name: {phase_name!r}",
                "phase_item": None, "log_path": None, "exit_code": None,
                "env_name": None}

    runner = None
    try:
        runner = _import_runner_helpers()
    except Exception as e:
        return {"success": False, "skipped": False,
                "error": f"runner import failed: {e}",
                "phase_item": None, "log_path": None, "exit_code": None,
                "env_name": None}

    # Load project config.
    try:
        config = runner.load_config()
    except Exception as e:
        return {"success": False, "skipped": False,
                "error": f"load_config failed: {e}",
                "phase_item": None, "log_path": None, "exit_code": None,
                "env_name": None}

    # Find the first stage whose environment should actually be prepared.
    # Only consider stages with environment.required == true — stages like
    # prepare-env-scripts (required: false) don't need env preparation.
    stages = config.get("stages") or []
    stage_name = None
    stage_cfg = None
    for s in stages:
        env_cfg = s.get("environment") or {}
        if s.get("tracks") and env_cfg.get("required", False):
            stage_name = s.get("name")
            stage_cfg = s
            break
    if stage_cfg is None:
        return {"success": True, "skipped": True,
                "phase_item": None, "log_path": None, "exit_code": None,
                "env_name": None}

    # Resolve environment for this stage.
    try:
        env_name = runner._resolve_stage_env(change, stage_name)
    except FileNotFoundError as e:
        return {"success": False, "skipped": False,
                "error": f"environment.yaml missing: {e}",
                "phase_item": f"{stage_name}.{phase_name}",
                "log_path": None, "exit_code": None, "env_name": None}
    except Exception as e:
        return {"success": False, "skipped": False,
                "error": f"resolve_stage_env failed: {e}",
                "phase_item": f"{stage_name}.{phase_name}",
                "log_path": None, "exit_code": None, "env_name": None}

    if env_name == "__skip__":
        return {"success": True, "skipped": True,
                "phase_item": f"{stage_name}.{phase_name}",
                "log_path": None, "exit_code": None, "env_name": None}

    env_cfg = (config.get("environments") or {}).get(env_name, {})
    action = env_cfg.get(phase_name)
    if not action:
        return {"success": True, "skipped": True,
                "phase_item": f"{stage_name}.{phase_name}",
                "log_path": None, "exit_code": None, "env_name": env_name}

    phase_item = f"{stage_name}.{phase_name}"
    script_path = action.get("script")
    if not script_path:
        return {"success": False, "skipped": False,
                "error": f"environment {env_name}.{phase_name} has no script",
                "phase_item": phase_item, "log_path": None, "exit_code": None,
                "env_name": env_name}

    # Resolve log path (mirrors runner._phase_log_path).
    try:
        log_path = str(runner._phase_log_path(change, phase_item))
    except Exception as e:
        log_path = os.path.join(
            CHANGES_DIR, change, "2-build",
            f"{phase_item.replace('.', '-')}.log")
        print(f"[execute_env_hook_inline] _phase_log_path failed, using "
              f"fallback {log_path}: {e}", file=_sys.stderr)

    # Build command: invoke pg-run-hook.py with JSON spec on stdin (mirrors
    # _execute_phase pattern at runner.py:3126-3143).
    import shlex as _shlex
    import json as _json

    # Use runner's PROJECT_ROOT when available (so test fixtures with
    # overridden PROJECT_ROOT resolve the same path as v1 _execute_phase).
    project_root = getattr(runner, "PROJECT_ROOT", None) or PROJECT_ROOT
    PG_HOOK_RUNNER = os.path.join(
        project_root, ".pg", "skills", "src", "runtime", "lib", "pg-run-hook.py"
    )

    args = action.get("args") or []
    inner_cmd = "bash " + _shlex.quote(script_path) + (
        " " + " ".join(_shlex.quote(str(a)) for a in args) if args else ""
    )
    spec = {
        "cmd": inner_cmd,
        "change": change,
        "stage": stage_name,
        "env": env_name,
        "hook_type": phase_name,
        "timeout_seconds": action.get("timeout_seconds"),
        "log_path": log_path,
        "caller": "pg-build",
        "skill": "pg-build",
    }
    cmd = (
        f"python3 {_shlex.quote(PG_HOOK_RUNNER)}"
        f" <<'EOF'\n{_json.dumps(spec, indent=2)}\nEOF"
    )

    # Ensure log directory exists, then run synchronously.
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    except Exception:
        pass

    # Context-chain bookkeeping: phase_start.
    try:
        from pg_context_chain import phase_start, phase_end
        phase_start(change, phase_item)
    except Exception as e:
        print(f"[execute_env_hook_inline] phase_start failed: {e}",
              file=_sys.stderr)

    try:
        proc = _subprocess.run(
            cmd, shell=True,
            stdout=open(log_path, "w"),
            stderr=_subprocess.STDOUT,
            timeout=action.get("timeout_seconds") or 600,
        )
        exit_code = proc.returncode
    except _subprocess.TimeoutExpired as e:
        exit_code = 124
        try:
            with open(log_path, "a") as f:
                f.write(f"\n[execute_env_hook_inline] TIMEOUT after "
                        f"{e.timeout}s\n")
        except Exception:
            pass
    except Exception as e:
        exit_code = 1
        try:
            with open(log_path, "a") as f:
                f.write(f"\n[execute_env_hook_inline] EXCEPTION: {e}\n")
        except Exception:
            pass

    success = (exit_code == 0)

    # Context-chain bookkeeping: phase_end.
    try:
        from pg_context_chain import phase_end
        phase_end(change, phase_item,
                  summary=f"exit_code={exit_code}, "
                          f"env={env_name}, log={log_path}")
    except Exception as e:
        print(f"[execute_env_hook_inline] phase_end failed: {e}",
              file=_sys.stderr)

    return {
        "success": success,
        "skipped": False,
        "log_path": log_path,
        "exit_code": exit_code,
        "env_name": env_name,
        "phase_item": phase_item,
        "error": None if success else (f"exit_code={exit_code}, log={log_path}"),
    }


def _resolve_default_env(state) -> str:
    """Best-effort: return the first stage's environment name for env-hook.

    Falls back to 'dev-local' (the most common case) when state has no
    environment_name yet. This is only used during bootstrap before any
    environment.yaml is loaded by execute_env_hook_inline.
    """
    state_dict = _normalize_state_for_bootstrap(state)
    env_name = state_dict.get("environment_name")
    if env_name:
        return env_name
    return "dev-local"


def _persist_environment_summary(state, env_name: str, log_path: str,
                                  instances: dict) -> None:
    """Persist environment summary into state for first-dispatch挂载.

    Writes into state.context.environment_summary so subsequent cmd_next_v2
    calls can read it without re-executing the env-hook.

    Args:
        state: v1 state dict OR v2 PipelineState.
        env_name: resolved environment name (e.g. "dev-local").
        log_path: absolute path to the prepare_env log.
        instances: dict of {role: [{name, host, port?}, ...]}.
    """
    summary = {
        "name": env_name,
        "prepare_env_log_path": log_path,
        "instances": instances,
    }
    if hasattr(state, "data") and isinstance(state.data, dict):
        ctx = state.data.setdefault("context", {})
        ctx["environment_summary"] = summary
        ctx["environment_summary_dispatched"] = False
        if hasattr(state, "commit"):
            try:
                state.commit()
            except Exception:
                pass
        return
    if isinstance(state, dict):
        state["environment_summary"] = summary
        state["environment_summary_dispatched"] = False
        return


def _extract_instances_summary(config: dict) -> dict:
    """Build the精简 instances summary from environments.<env>.roles.

    Returns a dict like:
        {"backend": [{"name": "backend-1", "host": "localhost", "port": 9080}],
         "frontend": [{"name": "frontend-1", "host": "localhost", "port": 3008}],
         "agent": [{"name": "agent-1", "host": "localhost"}]}

    Only includes roles/instances that are actually configured. Used to
    populate ctx.environment for the first dispatch after prepare_env.
    """
    envs = config.get("environments") or {}
    # Use dev-local as the default reference env (the one we just prepared).
    # If not present, fall back to the first env that has roles.
    env_cfg = envs.get("dev-local") or {}
    if not env_cfg.get("roles"):
        for _name, cfg in envs.items():
            if cfg.get("roles"):
                env_cfg = cfg
                break
    roles_cfg = env_cfg.get("roles") or {}
    summary = {}
    for role_name, role_cfg in roles_cfg.items():
        inst_list = []
        for inst in (role_cfg.get("instances") or []):
            entry = {"name": inst.get("name"), "host": inst.get("host")}
            if "port" in inst:
                entry["port"] = inst["port"]
            inst_list.append(entry)
        summary[role_name] = inst_list
    return summary


def _import_runner_helpers():
    """Lazy import of pg-pipeline-runner.py module-level functions.

    Returns the module object so callers can call its functions
    (e.g. runner.migrate_legacy_state_files, runner._ensure_context_chain, etc.).
    Raises ImportError if the runner module is not on sys.path.
    """
    scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
    if scripts_dir not in _sys.path:
        _sys.path.insert(0, scripts_dir)
    if scripts_dir not in _sys.path:
        _sys.path.insert(0, scripts_dir)
    import importlib
    return importlib.import_module("pg_pipeline_runner")


def _normalize_state_for_bootstrap(state):
    """Return a v1-style state dict from either a v1 dict or v2 PipelineState.

    v1 state: {init_committed: bool, ...}
    v2 PipelineState: instance with .data["context"] = {...}

    Returns the v1-style dict (mutations to it may be persisted by caller
    using _persist_state_mutation).
    """
    if hasattr(state, "data") and isinstance(state.data, dict):
        ctx = state.data.get("context") or {}
        return ctx
    if isinstance(state, dict):
        return state
    raise TypeError(
        f"state must be a dict (v1) or PipelineState (v2); got {type(state).__name__}")


def _persist_state_mutation(state, key, value):
    """Persist a state mutation back to the original state container.

    For v1 dict: write directly into the dict. Caller is responsible for
    save_state() to disk after the helper returns (cmd_next / cmd_record do
    this in their normal flow).
    For v2 PipelineState: write into data["context"][key] and commit to disk
    via .commit() — this is the v2 equivalent of save_state.
    """
    if hasattr(state, "data") and isinstance(state.data, dict):
        ctx = state.data.setdefault("context", {})
        ctx[key] = value
        if hasattr(state, "commit"):
            try:
                state.commit()
            except Exception:
                pass
        return
    if isinstance(state, dict):
        state[key] = value
        return


def pg_build_bootstrap(change, state):
    """Run the 5 environment-init side effects required before the first dispatch.

    Encapsulates what v1 cmd_next:2862-2885 used to do inline:
      1. migrate_legacy_state_files(change) — pull legacy state.json from change root
      2. _ensure_context_chain(change) — create 2-build/context-chain.md
      3. _ensure_feature_branch(change) — git checkout -b feat/pg/<change>
      4. _maybe_bootstrap_init_commit(change, state) — initial git commit
      5. **execute_env_hook_inline("prepare_env")** — run env script + log
         phase_start/phase_end to context-chain.md. Failures RAISE EnvHookError
         so callers (cmd_next_v2) can surface them via `env_hook_failed` action.

    Args:
        change: change name.
        state: v1 state dict (with init_committed key) OR v2 PipelineState instance.

    Returns:
        dict | None — the init_commit result dict (with branch/sha/message keys)
        when this call actually ran the bootstrap; None when skipped (already done).

    Raises:
        EnvHookError: when prepare_env execution fails (non-zero exit).
            Caller (cmd_next_v2) catches this and returns `env_hook_failed` action.

    Side effects:
        - Migrates legacy state files at .pg/changes/<change>/* → 2-build/
        - Creates .pg/changes/<change>/2-build/context-chain.md (idempotent)
        - git checkout -b feat/pg/<change> (idempotent, only if not already on it)
        - git add -A + git commit "chore(<change>): bootstrap pg-build" (idempotent
          via state.init_committed marker)
        - Runs environments.<env>.prepare_env script synchronously (v2 only;
          v1 still uses _execute_phase / phase_result path)
        - Writes phase_start/phase_end entries to context-chain.md
        - Persists environment_summary into state.context for first-dispatch挂载

    Note:
        Side effects 1-4 are best-effort: failures are caught and logged
        (printed to stderr) but never raised, so dispatch always proceeds.
        Side effect 5 (env-hook) is the only step that RAISES on failure —
        since a broken env means dispatch cannot proceed meaningfully.
    """
    state_dict = _normalize_state_for_bootstrap(state)

    runner = None
    try:
        runner = _import_runner_helpers()
    except Exception as e:
        print(f"[pg_build_bootstrap] import runner failed: {e}", file=_sys.stderr)
        return None

    # 1. migrate_legacy_state_files
    try:
        moved = runner.migrate_legacy_state_files(change)
        if moved:
            print(f"[pg_build_bootstrap] migrated legacy state: {moved}", file=_sys.stderr)
    except Exception as e:
        print(f"[pg_build_bootstrap] migrate failed: {e}", file=_sys.stderr)

    # 2. _ensure_context_chain
    try:
        runner._ensure_context_chain(change)
    except Exception as e:
        print(f"[pg_build_bootstrap] ensure_context_chain failed: {e}", file=_sys.stderr)

    # 3. _ensure_feature_branch
    try:
        runner._ensure_feature_branch(change)
    except Exception as e:
        print(f"[pg_build_bootstrap] ensure_feature_branch failed: {e}", file=_sys.stderr)

    # 4. _maybe_bootstrap_init_commit
    init_commit = None
    try:
        init_commit = runner._maybe_bootstrap_init_commit(change, state_dict)
    except Exception as e:
        print(f"[pg_build_bootstrap] init_commit failed: {e}", file=_sys.stderr)

    # Persist init_committed marker back to the original state container.
    if init_commit is not None:
        _persist_state_mutation(state, "init_committed", True)

    # 5. ==== Inline env-hook (prepare_env) ====
    # This is the v2 path: bootstrap now does the prepare_env work itself,
    # so callers don't need to handle `phase_result` action. v1 callers
    # ignore the EnvHookError because v1 still uses _execute_phase path.
    # We detect "v1 vs v2" by checking whether the state has the v2 schema
    # attribute. v1 state is a plain dict with no `data` attribute; v2 is
    # PipelineState with .data["context"].
    is_v2_state = hasattr(state, "data") and isinstance(state.data, dict)
    if is_v2_state:
        # Mark prepare_env completed flag so we don't re-execute on re-call.
        # pg_build_bootstrap may be called multiple times per session (e.g.
        # after each record), but the env-hook only needs to run once.
        ctx = state.data.setdefault("context", {})
        if not ctx.get("prepare_env_completed"):
            result = execute_env_hook_inline(change, "prepare_env")
            ctx["prepare_env_completed"] = True
            ctx["prepare_env_result"] = {
                "success": result.get("success"),
                "skipped": result.get("skipped"),
                "log_path": result.get("log_path"),
                "exit_code": result.get("exit_code"),
                "env_name": result.get("env_name"),
                "phase_item": result.get("phase_item"),
            }
            # Persist environment summary for first-dispatch挂载.
            if result.get("success") and not result.get("skipped"):
                try:
                    config = runner.load_config()
                    instances = _extract_instances_summary(config)
                    _persist_environment_summary(
                        state, result["env_name"], result["log_path"],
                        instances)
                except Exception as e:
                    print(f"[pg_build_bootstrap] persist environment_summary "
                          f"failed: {e}", file=_sys.stderr)

            try:
                if hasattr(state, "commit"):
                    state.commit()
            except Exception:
                pass

            if not result.get("success"):
                # RAISE — caller (cmd_next_v2) must surface as env_hook_failed.
                raise EnvHookError(
                    phase_name="prepare_env",
                    log_path=result.get("log_path") or "",
                    exit_code=result.get("exit_code") or -1)

    return init_commit


def pg_build_dispatch_context(change, item_id, sub, config):
    """Assemble the dispatch ctx for a track sub-phase.

    Encapsulates what v1 cmd_next:2932-2941 used to do inline:
      1. filter_track_context(config, item_id, sub, change=change) — track config slice
      2. _enrich_context_with_rollback — populate ctx["rollback_context"] from
         pg_context_chain.rollback_get (only when a rollback is pending)
      3. _enrich_context_with_stage — populate ctx["stage"] with environment.hooks
         so sub-agents know how to start/stop services
      4. _enrich_context_with_tasks — populate ctx["tasks_preformatted"] from
         tasks.md checkbox state
      5. _enrich_context_with_prompt_injection — splice build_rules into prompt

    Args:
        change: change name.
        item_id: track id (e.g. "backend", "openapi-gen").
        sub: sub-phase ("test" / "dev" / "verify" / "gate" / "fix" / "fix-gate" / "simple").
        config: full project.yaml dict (from load_config()).

    Returns:
        (ctx, has_rollback) — ctx is a dict ready to pass to dispatch_action;
        has_rollback is True when ctx["rollback_context"] is populated (used by
        v1 to set state["current"]["has_rollback"]).

    Note:
        Failures in individual enrich_* calls are caught and logged so that
        a broken rollback get / tasks parse doesn't block dispatch.
    """
    try:
        runner = _import_runner_helpers()
    except Exception as e:
        print(f"[pg_build_dispatch_context] import runner failed: {e}", file=_sys.stderr)
        # Fall back to bare ctx; sub-agent won't have hooks/tasks but dispatch proceeds.
        return {"_change": change}, False

    # 1. filter_track_context — get the track config slice
    try:
        ctx = runner.filter_track_context(config, item_id, sub, change=change)
    except Exception as e:
        print(f"[pg_build_dispatch_context] filter_track_context failed: {e}", file=_sys.stderr)
        ctx = {}
    ctx["_change"] = change

    # 2. rollback — read pending rollback context if any
    has_rollback = False
    try:
        # Reuse the runner's already-imported pg_context_chain module so
        # tests that mock `runner.pg_context_chain` work transparently.
        pgcc = getattr(runner, "pg_context_chain", None)
        if pgcc is None:
            import pg_context_chain as pgcc
        rb = pgcc.rollback_get(change, item_id)
        runner._enrich_context_with_rollback(ctx, rb)
        has_rollback = bool(rb and rb.get("found"))
    except Exception as e:
        print(f"[pg_build_dispatch_context] rollback failed: {e}", file=_sys.stderr)

    # 3. stage / environment hooks
    try:
        runner._enrich_context_with_stage(ctx, config, item_id, change=change)
    except Exception as e:
        print(f"[pg_build_dispatch_context] stage failed: {e}", file=_sys.stderr)

    # 4. tasks.md preformatted
    try:
        runner._enrich_context_with_tasks(ctx, change, item_id, sub)
    except Exception as e:
        print(f"[pg_build_dispatch_context] tasks failed: {e}", file=_sys.stderr)

    # 5. build_rules prompt injection
    try:
        runner._enrich_context_with_prompt_injection(ctx, config, item_id, sub)
    except Exception as e:
        print(f"[pg_build_dispatch_context] prompt_injection failed: {e}", file=_sys.stderr)

    return ctx, has_rollback


def pg_build_record_log(change, item, sub, status, summary="", outputs="", issues=""):
    """Record sub-agent outcome in context-chain.md (v2 path only).

    Encapsulates the 17 pg_context_chain.* call sites scattered through v1
    cmd_record (lines 3055/3140/3228/3374/3377/3399/3423/3434/3455/3480/3502/
    3521/3535/3628/3730/3755/3777/3791/3804) into a single dispatch-by-(sub,status)
    function. v1 still calls the original 17 sites (per user decision); v2
    cmd_record_v2 calls this function once at the entry point.

    Mappings (mirrors v1 cmd_record behavior):
      - completed (test/dev/verify/simple)  → sub_end('COMPLETED')
      - completed (fix/fix-gate)            → sub_end('COMPLETED', fix_cycle) +
                                                sub_start(parent_phase, fix_cycle)
      - failed                              → sub_end('FAILED', issues)
      - escalate                            → sub_end('COMPLETED') +
                                                sub_start('fix', fix_cycle=1)
      - pass (track)                        → sub_end('PASS') +
                                                sub_start(next_sub) — caller
                                                drives the next dispatch
      - pass (final-gate)                   → sub_end('PASS')
      - fail (track)                        → sub_end('FAIL') +
                                                rollback_set(reason, source) +
                                                sub_start('fix-gate', fix_cycle=1)
      - fail (final-gate)                   → (no context-chain write; workflow
                                                failure is recorded elsewhere)

    Args:
        change: change name.
        item: track id (or "final-gate").
        sub: sub-phase ("test" / "dev" / "verify" / "gate" / "fix" /
            "fix-gate" / "simple" / None for final-gate).
        status: one of ALLOWED_STATUS values (completed/failed/escalate/pass/fail).
        summary: sub-agent summary text.
        outputs: sub-agent outputs (comma-separated task IDs).
        issues: failure details (for sub_end issues field).

    Note:
        Always best-effort: failures are caught and logged, never raised.
    """
    try:
        import pg_context_chain as _pgcc
    except Exception as e:
        print(f"[pg_build_record_log] import pg_context_chain failed: {e}", file=_sys.stderr)
        return

    try:
        if status == "completed":
            if sub in ("fix", "fix-gate"):
                _pgcc.sub_end(change, item, sub, "COMPLETED",
                              summary=summary, outputs=outputs, issues=issues,
                              fix_cycle=1)
                parent = "verify" if sub == "fix" else "gate"
                _pgcc.sub_start(change, item, parent, fix_cycle=1)
            else:
                _pgcc.sub_end(change, item, sub, "COMPLETED",
                              summary=summary, outputs=outputs, issues=issues)
        elif status == "failed":
            _pgcc.sub_end(change, item, sub, "FAILED", "", "", issues)
        elif status == "escalate":
            # verify → fix cycle
            _pgcc.sub_end(change, item, "verify", "COMPLETED",
                          summary=summary or "ESCALATE",
                          outputs=outputs, issues=issues)
            _pgcc.sub_start(change, item, "fix", fix_cycle=1)
        elif status == "pass":
            if item == "final-gate":
                _pgcc.sub_end(change, "final-gate", "gate", "PASS", summary=summary)
            else:
                _pgcc.sub_end(change, item, "gate", "PASS", summary=summary)
                # The next sub dispatch (e.g. next track) is driven by the caller;
                # we only record the gate pass here.
        elif status == "fail":
            if item == "final-gate":
                # final-gate fail is a workflow-level failure; no context-chain
                # entry — caller handles it.
                return
            _pgcc.sub_end(change, item, "gate", "FAIL",
                          summary=summary, outputs=outputs, issues=issues)
            _pgcc.rollback_set(change, item,
                               reason=summary or "gate FAIL",
                               source=f"{item}:gate")
            _pgcc.sub_start(change, item, "fix-gate", fix_cycle=1)
    except Exception as e:
        print(f"[pg_build_record_log] ({sub},{status}) failed: {e}", file=_sys.stderr)