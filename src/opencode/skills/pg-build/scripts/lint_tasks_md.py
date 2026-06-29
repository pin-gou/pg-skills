#!/usr/bin/env python3
"""lint_tasks_md.py — CI lint enforcing tasks.md SSOT discipline.

Per build-r plan §9.9:

  Default rule: 禁止 sub-agent 直接 Edit tasks.md 的 `[ ]` → `[x]`
  改动必须通过 `pg-pipeline-state-v2.py mark-task` CLI.

  Bypass cases:
    - 新增任务 (新增 `- [ ] X.Y <description>` 行) 允许直接 Edit
    - pg-propose 阶段 (生成新 tasks.md) 允许
    - runbook / docs 下不影响 SSOT 的 checkbox 允许

  Detection method:
    Run `git diff <tasks.md>` and look for lines where:
      - `- [ ]` was REMOVED (diff starts with `- [ ]`)
      - `- [x]` was ADDED (diff adds `+ [x]`)
    AND the same line content (X.Y task number + description) appears
    on both sides (i.e. it's a TOGGLE, not just adding a new task).

Usage:
  python3 lint_tasks_md.py <tasks_md_path> [<tasks_md_path> ...]
  python3 lint_tasks_md.py --staged          # lint git-indexed changes
  python3 lint_tasks_md.py --diff <ref>      # lint changes vs <ref>

Exit codes:
  0  no violations
  1  violations found (printed to stderr)
  2  usage error / git not available

This script is intentionally lightweight (no PyYAML, no gitpython). It
shells out to `git diff` for the actual diff text.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import List, Tuple


CHECKBOX_LINE_RE = re.compile(
    r"^(\s*)(?:-)?\s*\[([ x])\]\s*(\d+)\.(\d+)(.*)$"
)


def parse_checkbox_line(line: str) -> Tuple[str, str, int, int, str] | None:
    """Parse a checkbox line. Returns (indent, marker, section, sub, suffix).

    marker is ' ' (unchecked) or 'x' (checked). section is the leading
    task number (e.g. `1` from `1.1`), sub is the sub-task index (e.g. `1`).
    Returns None for non-checkbox.
    Accepts both raw tasks.md lines (`- [ ] 1.1 desc`) and diff lines
    with their leading `+`/`-` already stripped by the caller.
    """
    line = line.rstrip("\n")
    m = CHECKBOX_LINE_RE.match(line)
    if not m:
        return None
    indent, marker, section, sub, suffix = m.groups()
    return (indent, marker, int(section), int(sub), suffix)


def diff_toggle_pairs(diff_text: str) -> List[Tuple[int, int, int, str]]:
    """Find pairs of (removed - [ ] X.Y, added [x] X.Y) in a diff.

    Returns a list of (section, sub, line_no_in_added, summary) tuples.
    section + sub together identify the task (e.g. (1, 1) for "1.1").
    """


def diff_toggle_pairs(diff_text: str) -> List[Tuple[int, int, str]]:
    """Find pairs of (removed - [ ] task_id, added [x] task_id) in a diff.

    Returns a list of (task_id, line_no_in_added, summary) tuples for each
    detected toggle. The line_no_in_added is the line in the post-change
    file (for error reporting).
    """
    # Parse unified diff: group lines by hunk @@ -X,Y +A,B @@
    hunks = []
    current_hunk: list = []
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            if current_hunk:
                hunks.append(current_hunk)
            current_hunk = [line]
        else:
            current_hunk.append(line)
    if current_hunk:
        hunks.append(current_hunk)

    toggles = []
    for hunk_lines in hunks:
        # Removed = `- [ ] X.Y desc`
        # Added   = `+ [x] X.Y desc`
        # Track the removed-task-id -> description mapping, then look for
        # matching added entries. Pairing key is `(section, sub)`.
        removed: dict = {}     # (section, sub) -> list of descriptions
        added: list = []       # list of (section, sub, line_no, description)
        hunk_added_line = 0
        for line in hunk_lines[1:]:  # skip @@ header
            if line.startswith("+"):
                parsed = parse_checkbox_line(line[1:])
                if parsed is not None:
                    _indent, marker, section, sub, suffix = parsed
                    if marker == "x":
                        added.append((section, sub, hunk_added_line, suffix))
                hunk_added_line += 1
            elif line.startswith("-"):
                parsed = parse_checkbox_line(line[1:])
                if parsed is not None:
                    _indent, marker, section, sub, suffix = parsed
                    if marker == " ":
                        removed.setdefault((section, sub), []).append(suffix)
                # `-` doesn't increment added line counter
            else:
                # Context line (or empty) advances the added-line counter
                if line.startswith(" "):
                    hunk_added_line += 1
                elif line == "":
                    # pure blank context — likely end of hunk
                    hunk_added_line += 1

        # Pair up: for each added [x], check if a matching `- [ ] X.Y desc`
        # was removed (description match).
        for section, sub, added_line_no, desc in added:
            key = (section, sub)
            if key in removed:
                # any of the removed suffixes matches the added description?
                for rem_desc in removed[key]:
                    if rem_desc.strip() == desc.strip():
                        toggles.append((section, sub, added_line_no, desc.strip()))
                        break
    return toggles


def get_git_diff(tasks_path: str, mode: str = "working") -> str:
    """Get the diff for tasks_path. mode: 'working' or 'staged'.

    Default (working) returns staged + unstaged combined, which is what
    CI usually wants (catches both `git add` and untracked edits).
    """
    cwd = _find_git_root(tasks_path)
    if mode == "staged":
        cmd = ["git", "diff", "--cached", "--unified=3", "--", tasks_path]
    elif mode == "all":
        # staged + working-tree combined
        staged = subprocess.run(
            ["git", "diff", "--cached", "--unified=3", "--", tasks_path],
            capture_output=True, text=True, cwd=cwd, timeout=10)
        working = subprocess.run(
            ["git", "diff", "--unified=3", "--", tasks_path],
            capture_output=True, text=True, cwd=cwd, timeout=10)
        return staged.stdout + working.stdout
    else:
        cmd = ["git", "diff", "--unified=3", "--", tasks_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                  cwd=cwd, timeout=10)
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        return ""
    return result.stdout


def get_git_diff_vs_ref(tasks_path: str, ref: str) -> str:
    cwd = _find_git_root(tasks_path)
    cmd = ["git", "diff", "--unified=3", ref, "--", tasks_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                  cwd=cwd, timeout=10)
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        return ""
    return result.stdout


def _find_git_root(path: str) -> str:
    cur = os.path.abspath(os.path.dirname(path))
    for _ in range(8):
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.getcwd()
        cur = parent
    return os.getcwd()


def _parse_section_headings(tasks_path: str) -> dict:
    """Parse tasks.md ## headings → {section_number: (track, phase)}.

    Heading format: `## N. <track>:<phase> - <label>` or
    `## N. <track>:<phase>` (without label).

    Example: `## 1. dev.backend:test - test stage` →
             {1: ("dev.backend", "test")}
    """
    headings = {}
    if not os.path.isfile(tasks_path):
        return headings
    with open(tasks_path, encoding="utf-8") as f:
        content = f.read()
    pat = re.compile(r"^##\s+(\d+)\.\s+([^\s:]+):([^\s\-]+)", re.MULTILINE)
    for m in pat.finditer(content):
        section_num = int(m.group(1))
        track = m.group(2)
        phase = m.group(3).rstrip(" -")
        headings[section_num] = (track, phase)
    return headings


def _read_state_marked(state_path: str) -> dict:
    """Read state.json and return {(track, phase): {marked_sub_ids}}."""
    if not os.path.isfile(state_path):
        return {}
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.loads(f.read())
    except (OSError, ValueError):
        return {}
    out = {}
    for track, tdata in (state.get("tracks") or {}).items():
        for phase, pdata in (tdata.get("phases") or {}).items():
            marked = set(pdata.get("tasks_marked") or [])
            if marked:
                out[(track, phase)] = marked
    return out


def lint_tasks_md(tasks_path: str, diff_text: str | None = None) -> List[str]:
    """Return list of violation messages. Empty list = OK.

    Algorithm:
      1. Parse git diff for `- [ ] X.Y` → `+ [x] X.Y` toggle pairs.
      2. For each toggle, look up the section number in tasks.md headings
         to find (track, phase).
      3. Cross-check state.json: if `phases.<phase>.tasks_marked` contains
         the sub-task number, the toggle is legitimate (CLI-written) and
         should be skipped.
      4. Anything else is a violation.
    """
    if not os.path.isfile(tasks_path):
        return [f"tasks.md not found: {tasks_path}"]

    if diff_text is None:
        diff_text = get_git_diff(tasks_path)
    toggles = diff_toggle_pairs(diff_text)
    if not toggles:
        return []

    # Read section → (track, phase) mapping from CURRENT tasks.md (the
    # post-change version). This assumes the heading still exists, which
    # it should for a valid edit.
    headings = _parse_section_headings(tasks_path)

    # Read state.json for cross-check.
    state_path = os.path.join(os.path.dirname(tasks_path), "2-build",
                                ".pipeline-state.json")
    state_marked = _read_state_marked(state_path)

    msgs = []
    for section, sub, line_no, desc in toggles:
        # Resolve section → (track, phase) via heading
        track_phase = headings.get(section)
        if track_phase is None:
            # Section heading missing or unparseable — treat as violation
            task_label = f"{section}.{sub}"
            msgs.append(
                f"{tasks_path}:{line_no}: 检测到 checkbox 改动 "
                f"({task_label}): `{desc[:50]}` — section heading 未找到, "
                f"无法验证 mark-task 调用"
            )
            continue
        track, phase = track_phase

        # Check state.json: is this sub-task already recorded as marked?
        marked = state_marked.get((track, phase), set())
        if sub in marked:
            # Legitimate: CLI wrote both state.json and tasks.md
            continue

        # Violation: toggle in tasks.md but not in state.json
        task_label = f"{section}.{sub}"
        msgs.append(
            f"{tasks_path}:{line_no}: 检测到直接 checkbox 改动 "
            f"({task_label}): `{desc[:50]}` — 必须通过 "
            f"`pg-pipeline-state-v2.py mark-task <change> {track} {phase} {sub}` "
            f"标记任务完成 (state.json 未记录此 tasks_marked)"
        )
    return msgs


def main() -> int:
    p = argparse.ArgumentParser(
        description="Lint tasks.md for direct checkbox toggles "
                    "(must use mark-task CLI in build-r).")
    p.add_argument("paths", nargs="*", help="tasks.md files to lint")
    p.add_argument("--staged", action="store_true",
                   help="lint staged (git diff --cached) changes")
    p.add_argument("--diff", metavar="REF",
                   help="lint changes vs REF (e.g. HEAD~1)")
    args = p.parse_args()

    paths = args.paths
    if not paths:
        # Default: lint all .pg/changes/*/tasks.md under cwd
        changes_dir = os.path.join(os.getcwd(), ".pg", "changes")
        if os.path.isdir(changes_dir):
            for entry in os.listdir(changes_dir):
                cand = os.path.join(changes_dir, entry, "tasks.md")
                if os.path.isfile(cand):
                    paths.append(cand)

    if not paths:
        print("lint_tasks_md: no tasks.md files given (and none auto-found)",
              file=sys.stderr)
        return 2

    all_violations = []
    for path in paths:
        if args.diff:
            diff_text = get_git_diff_vs_ref(path, args.diff)
        elif args.staged:
            diff_text = get_git_diff(path, mode="staged")
        else:
            # Default: combine staged + unstaged (covers all local changes)
            diff_text = get_git_diff(path, mode="all")
        violations = lint_tasks_md(path, diff_text)
        all_violations.extend(violations)

    if all_violations:
        print("=".ljust(78, "="), file=sys.stderr)
        print(f"lint_tasks_md: {len(all_violations)} violation(s)", file=sys.stderr)
        print("=".ljust(78, "="), file=sys.stderr)
        for v in all_violations:
            print(v, file=sys.stderr)
        print("", file=sys.stderr)
        print("修复方式: 通过 mark-task CLI 标记任务完成:", file=sys.stderr)
        print("  python3 .opencode/skills/pg-build/scripts/pg_pipeline_state_v2.py \\",
              file=sys.stderr)
        print("    <change> mark-task <track> <phase> <task_id>", file=sys.stderr)
        print("然后 reset tasks.md 改动:", file=sys.stderr)
        print("  git checkout -- .pg/changes/<change>/tasks.md", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())