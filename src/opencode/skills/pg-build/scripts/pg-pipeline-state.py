#!/usr/bin/env python3
"""pg-pipeline-state.py — Pipeline progress state machine for pg-build.

All state is read from/written to tasks.md directly (single source of truth).
No sidecar files needed.

  Commands:
  detect <change>
    Find first pipeline.order item with unfinished tasks.
    Returns JSON with {item, type, subPhase, completedItems, totalItems, message}.
    If all complete, returns {item: null, message: "ALL_COMPLETED"}.

  check <change> <item>
    Check detailed status of a pipeline item (all its sub-sections).
    Returns JSON with {item, type, status, sections: [{sub, unchecked, checked, status}]}

  mark <change> <item> [sub]
    Mark all - [ ] as - [x] in sections matching item[:sub].
    sub is optional; if omitted, marks all sections for the item.

  rollback <change> <track>
    Roll back all - [x] to - [ ] for all sections of a track.

  gate-rollback <change> <track> <gate_report_path>
    Partially roll back tasks in a track based on a gate report.
    Parses **关联 task** fields from `### {track}:G-N` sections in the report.
    Falls back to full track rollback if report missing or no parseable fields.

  progress <change>
    Show completion summary across all pipeline items.
    Returns JSON with {completed, total, done, items: [...]}

Usage (all commands from project root):
  python3 .opencode/skills/pg-build/scripts/pg-pipeline-state.py detect <change>
  python3 .opencode/skills/pg-build/scripts/pg-pipeline-state.py check <change> <item>
  python3 .opencode/skills/pg-build/scripts/pg-pipeline-state.py mark <change> <item> [sub]
  python3 .opencode/skills/pg-build/scripts/pg-pipeline-state.py rollback <change> <track>
  python3 .opencode/skills/pg-build/scripts/pg-pipeline-state.py gate-rollback <change> <track> <gate_report_path>
  python3 .opencode/skills/pg-build/scripts/pg-pipeline-state.py progress <change>
"""

import json
import os
import re
import sys

from pg_pipeline_common import (
    find_project_root, load_config, get_tasks_path, get_pipeline_order,
    parse_tasks, _parse_heading, count_tasks, find_sections_for_item,
    _item_sections_with_status, _item_status, _has_any_section,
    _track_matches, _bare_track, _read_environment_yaml, get_track_type,
    PROJECT_ROOT, CONFIG_PATH, CHANGES_DIR,
    HEADING_RE, _TRACK_HEADING_RE, _PHASE_HEADING_RE,
)


# ============================================================
# State-specific helpers (not in common — depend on CHANGES_DIR)
# ============================================================

def _is_phase_completed(change, item):
    """Check whether a phase item (prepare_env/clean_env) is already completed
    by reading .pipeline-state.json. Prevents cmd_detect from re-returning
    the same phase after _execute_phase has already advanced it."""
    state_path = os.path.join(CHANGES_DIR, change, "2-build", ".pipeline-state.json")
    if os.path.isfile(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        return item in state.get("completed_items", [])
    return False


def _get_deployment_override(change, item):
    """Resolve environment for a stage from environment.yaml.

    Backward-compatible: legacy callers pass `item` as qualified track name
    (e.g. 'dev-mock-integration.backend'). We extract the stage prefix and
    look up environment.yaml accordingly.

    Returns:
      None           — yaml missing or stage not declared
      "__skip__"     — stage marked as skip
      "<env-name>"   — chosen environment name
    """
    try:
        env_map = _read_environment_yaml(change)
    except FileNotFoundError:
        return None
    bare = _bare_track(item)
    for key in (item.rsplit(".", 1)[0] if "." in item else item, bare):
        if key in env_map:
            candidate = env_map[key]
            return "__skip__" if candidate == "skip" else candidate
    return None


# ============================================================
# Commands
# ============================================================

def cmd_detect(change):
    if not _ensure_tasks(change):
        return

    config = load_config()
    order = get_pipeline_order(config, change)
    tasks_path = get_tasks_path(change)
    sections, _ = parse_tasks(tasks_path)
    tracks = config.get("tracks") or {}

    total = len(order) + 1  # +1 for final-gate
    completed = 0

    for item in list(order) + ["final-gate"]:
        # v3.0: every item is a track (or final-gate). No more phase concept.
        if item == "final-gate":
            # final-gate completion is checked by runner separately;
            # here we just count it as a "to-be-checked" item in total.
            continue

        # Environment lifecycle hooks (prepare_env / clean_env) and simple
        # tracks (tracks.<id>.type == "simple") are phase items, not tracks.
        # They have no TDVG sub-phase; the runner's _execute_phase path
        # handles them. Report them directly as `type: "phase"` so the
        # runner dispatches _execute_phase instead of skipping or
        # dispatching a sub-agent.
        #
        # NOTE: simple tracks must be surfaced as phase items BEFORE the
        # `_item_sections_with_status` all_noop short-circuit below, because
        # if a simple track section contains `- [ ]` task lines, all_noop
        # would be False and cmd_detect would attempt to drive TDVG
        # sub-phase dispatch. v3.2+: tasks.md simple-track sections
        # contain a single `- [ ] N.1 执行 tracks.<id>.commands` placeholder
        # task (per pg-propose template), which still produces
        # all_noop=False — so the phase-branch routing below is REQUIRED
        # to keep simple tracks out of the TDVG sub-phase machinery.
        #
        # get_track_type expects the BARE track id (no stage prefix like
        # "dev."), so we strip the prefix before lookup. We keep `item` in
        # the result as the qualified form ("dev.simple-foo") to match the
        # env-hook return shape — _execute_phase re-strips the prefix
        # internally via _bare_track.
        #
        # _is_phase_completed takes the qualified form (matching how
        # runner writes `completed_items` from state["current"]["item"],
        # which is also qualified).
        bare = _bare_track(item)
        if bare in ("prepare_env", "clean_env") or get_track_type(config, bare) == "phase":
            if _is_phase_completed(change, item):
                completed += 1
                continue
            _print_json({
                "item": item,
                "type": "phase",
                "completedItems": completed,
                "totalItems": total,
                "message": item,
            })
            return

        _, untotal, chtotal, all_noop = _item_sections_with_status(sections, item)

        if not _has_any_section(sections, item):
            completed += 1
            continue
        if all_noop:
            completed += 1
            continue
        if untotal == 0:
            completed += 1
            continue

        # Determine current sub-phase within the item
        current_sub = None
        for sec in find_sections_for_item(sections, item):
            un, _ch, noop = count_tasks(sec["lines"])
            if un > 0 and not noop:
                current_sub = sec["sub"]
                break

        result = {
            "item": item,
            "type": "track",
            "completedItems": completed,
            "totalItems": total,
        }
        if current_sub:
            result["subPhase"] = current_sub
            result["message"] = f"{item}:{current_sub}"
        else:
            result["message"] = item

        _print_json(result)
        return

    _print_json({
        "item": None,
        "completedItems": total,
        "totalItems": total,
        "message": "ALL_COMPLETED",
    })


def cmd_check(change, item):
    if not _ensure_tasks(change):
        return

    config = load_config() if item else None
    tasks_path = get_tasks_path(change)
    sections, _ = parse_tasks(tasks_path)

    sub_filter = None
    if item and ":" in item:
        parts = item.split(":", 1)
        item = parts[0]
        sub_filter = parts[1]

    item_type = "track" if (config and item and item != "final-gate") else ("gate" if item == "final-gate" else None)
    item_sections = find_sections_for_item(sections, item, sub_filter) if item else []
    result_sections = []
    untotal = chtotal = 0
    all_noop = True

    for sec in item_sections:
        un, ch, noop = count_tasks(sec["lines"])
        untotal += un
        chtotal += ch
        if not noop:
            all_noop = False
        result_sections.append({
            "sub": sec["sub"],
            "label": sec["label"],
            "order": sec["order"],
            "unchecked": un,
            "checked": ch,
            "total": un + ch,
            "noop": noop,
            "status": _section_status(un, ch, noop),
        })

    status = _item_status(untotal, chtotal, all_noop, bool(item_sections))

    _print_json({
        "item": item,
        "type": item_type,
        "status": status,
        "sections": result_sections,
    })


def cmd_mark(change, item, sub):
    if not _ensure_tasks(change):
        return

    tasks_path = get_tasks_path(change)
    sections, lines = parse_tasks(tasks_path)

    total_marked = 0
    sections_affected = 0

    target_sections = find_sections_for_item(sections, item, sub)
    for sec in target_sections:
        for i in range(sec["start_line"] + 1, sec["end_line"]):
            s = lines[i].strip()
            if s.startswith("- [ ]"):
                indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                rest = s[5:]
                lines[i] = f"{indent}- [x]{rest}\n"
                total_marked += 1
        sections_affected += 1

    if total_marked > 0:
        with open(tasks_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    label = f"{item}:{sub}" if sub else item
    _print_json({
        "item": item,
        "sub": sub,
        "sectionsAffected": sections_affected,
        "tasksMarked": total_marked,
        "message": f"Marked {total_marked} tasks complete in {label}",
    })


def cmd_rollback(change, track):
    if not _ensure_tasks(change):
        return

    tasks_path = get_tasks_path(change)
    sections, lines = parse_tasks(tasks_path)

    total_rolled = 0
    sections_affected = 0

    for sec in sections:
        if not _track_matches(sec["item"], track):
            continue
        for i in range(sec["start_line"] + 1, sec["end_line"]):
            s = lines[i].strip()
            if s.startswith("- [x]"):
                indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                rest = s[5:]
                lines[i] = f"{indent}- [ ]{rest}\n"
                total_rolled += 1
        sections_affected += 1

    if total_rolled > 0:
        with open(tasks_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    _print_json({
        "track": track,
        "sectionsAffected": sections_affected,
        "tasksRolledBack": total_rolled,
        "message": f"Rolled back {total_rolled} tasks in {track}",
    })


def cmd_gate_rollback(change, track, gate_report_path):
    """Based on gate report's 关联 task fields, partially roll back tasks.md.

    Parses format: **关联 task**: {item}:{sub} 任务 X.Y[, 任务 X.Z]

    Fallback: if report missing or no parseable 关联 task fields,
    delegate to cmd_rollback for full track rollback.
    """
    if not _ensure_tasks(change):
        return

    # Fallback 1: report file does not exist
    if not os.path.isfile(gate_report_path):
        cmd_rollback(change, track)
        return

    with open(gate_report_path, encoding="utf-8") as f:
        report = f.read()

    # Extract all G-N sections for this track only (no cross-track pollution)
    gap_pattern = re.compile(
        r"###\s+" + re.escape(track) + r":G-\d+.*?(?=###\s+" + re.escape(track) + r":G-|\Z)",
        re.DOTALL,
    )
    task_refs = set()
    for gap_block in gap_pattern.findall(report):
        m = re.search(r"\*\*关联 task\*\*:\s*(.+?)(?:\n|$)", gap_block)
        if not m:
            continue
        ref_text = m.group(1).strip()
        for task_id in re.findall(r"\d+\.\d+", ref_text):
            task_refs.add(task_id)

    # Fallback 2: no parseable 关联 task fields
    if not task_refs:
        cmd_rollback(change, track)
        return

    # Partial rollback: only the task IDs referenced in gate report
    tasks_path = get_tasks_path(change)
    sections, lines = parse_tasks(tasks_path)

    rolled = 0
    for sec in sections:
        if not _track_matches(sec["item"], track):
            continue
        for i in range(sec["start_line"] + 1, sec["end_line"]):
            s = lines[i].strip()
            if not s.startswith("- [x]"):
                continue
            cm = re.match(r"\s*-\s*\[[x ]\]\s*(\d+\.\d+)", lines[i])
            if cm and cm.group(1) in task_refs:
                indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                rest = s[5:]
                lines[i] = f"{indent}- [ ]{rest}\n"
                rolled += 1

    if rolled > 0:
        with open(tasks_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    _print_json({
        "track": track,
        "tasksRolledBack": rolled,
        "mode": "partial",
        "message": f"Partially rolled back {rolled} tasks referenced in gate report",
    })


def cmd_progress(change):
    if not _ensure_tasks(change):
        return

    config = load_config()
    order = get_pipeline_order(config, change)
    tasks_path = get_tasks_path(change)
    sections, _ = parse_tasks(tasks_path)

    items = []
    completed = 0
    total = len(order) + 1

    for item in list(order) + ["final-gate"]:
        # Environment lifecycle hooks (prepare_env / clean_env) are phase
        # items, not tracks. They have no tasks.md sections; mark them as
        # `not_found` so callers can distinguish phase from track.
        bare = _bare_track(item)
        is_env_hook = bare in ("prepare_env", "clean_env")

        item_sections, untotal, chtotal, all_noop = _item_sections_with_status(sections, item)
        has_sections = len(item_sections) > 0
        status = _item_status(untotal, chtotal, all_noop, has_sections)

        if status in ("completed", "skip", "not_found"):
            completed += 1

        if is_env_hook:
            item_type = "phase"
        elif item == "final-gate":
            item_type = "gate"
        else:
            item_type = "track"
        sec_summary = []
        for sec in item_sections:
            un, ch, noop = count_tasks(sec["lines"])
            sec_summary.append({"sub": sec["sub"], "unchecked": un, "checked": ch, "noop": noop})

        items.append({
            "item": item,
            "type": item_type,
            "status": status,
            "sections": sec_summary,
        })

    _print_json({
        "completed": completed,
        "total": total,
        "done": completed >= total,
        "items": items,
    })


# ============================================================
# Helpers
# ============================================================

def _ensure_tasks(change):
    path = get_tasks_path(change)
    if not os.path.isfile(path):
        _print_json({"error": f"tasks.md not found: {path}"})
        return False
    return True


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


def _print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ============================================================
# Main
# ============================================================

COMMANDS = {"detect", "check", "mark", "rollback", "gate-rollback", "progress"}


def main():
    if len(sys.argv) < 3:
        _usage()
        sys.exit(1)

    command = sys.argv[1]
    change = sys.argv[2]

    if command not in COMMANDS:
        print(f"Unknown command: {command}", file=sys.stderr)
        _usage()
        sys.exit(1)

    if command == "detect":
        cmd_detect(change)
    elif command == "check":
        item = sys.argv[3] if len(sys.argv) > 3 else None
        cmd_check(change, item)
    elif command == "mark":
        item = sys.argv[3] if len(sys.argv) > 3 else None
        sub = sys.argv[4] if len(sys.argv) > 4 else None
        if not item:
            print("Usage: mark <change> <item> [sub]", file=sys.stderr)
            sys.exit(1)
        cmd_mark(change, item, sub)
    elif command == "rollback":
        track = sys.argv[3] if len(sys.argv) > 3 else None
        if not track:
            print("Usage: rollback <change> <track>", file=sys.stderr)
            sys.exit(1)
        cmd_rollback(change, track)
    elif command == "gate-rollback":
        track = sys.argv[3] if len(sys.argv) > 3 else None
        gate_report_path = sys.argv[4] if len(sys.argv) > 4 else None
        if not track or not gate_report_path:
            print("Usage: gate-rollback <change> <track> <gate_report_path>", file=sys.stderr)
            sys.exit(1)
        cmd_gate_rollback(change, track, gate_report_path)
    elif command == "progress":
        cmd_progress(change)


def _usage():
    print(__doc__, file=sys.stderr)


if __name__ == "__main__":
    main()
