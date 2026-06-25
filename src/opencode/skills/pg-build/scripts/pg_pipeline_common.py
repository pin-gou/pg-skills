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
    all_noop is True when every task line is literally "- 无".
    """
    unchecked = 0
    checked = 0
    noop_lines = 0

    for line in lines:
        s = line.strip()
        if s.startswith("- [ ]"):
            unchecked += 1
        elif s.startswith("- [x]"):
            checked += 1
        elif s == "- 无":
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