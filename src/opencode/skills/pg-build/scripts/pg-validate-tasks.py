#!/usr/bin/env python3
"""pg-validate-tasks.py — Read-only runner-consumability validator for tasks.md.

Validates that tasks.md can be correctly parsed and consumed by the pipeline
state machine (pg-pipeline-state.py). Pure structural validation — no content
semantics.

Usage:
  python3 pg-validate-tasks.py validate <change>
    → stdout: JSON report
    → auto-writes: .pg/changes/<change>/1-propose-review/validate-tasks-{round}.md

  python3 pg-validate-tasks.py validate <change> --format human
    → stdout: JSON summary (single line, for quick parse)
    → auto-writes: human-readable markdown report (same path)

  python3 pg-validate-tasks.py validate <change> --skip-stages <stages>
    → <stages> = comma-separated stage names (can be repeated)
    → skips validation of all items under the given stages
    → use when on_conditions evaluation skipped a stage; tasks.md has no
      chapters for those stages by design

Exit code: 0 if valid, 1 if any error-level issues detected.
"""

import json
import os
import re
import sys

from pg_pipeline_common import (
    find_project_root, load_config, get_tasks_path, get_pipeline_order,
    parse_tasks, find_sections_for_item, count_tasks, _track_matches,
    CHANGES_DIR,
)

VALID_SUBS = {"test", "dev", "verify", "gate"}


# ============================================================
# Validation logic
# ============================================================

def _is_env_hook(item):
    """Return True if item is a prepare_env or clean_env lifecycle hook.
    These are phase items executed directly by the runner, not tracks with
    tasks.md sections — so validation should not flag them as missing."""
    bare = item.rsplit(".", 1)[1] if "." in item else item
    return bare in ("prepare_env", "clean_env")


def _is_simple_track(config, item):
    """Return True if item is a simple-type track (type: simple in config.yaml).

    Simple tracks are executed directly by the runner via _execute_phase;
    they are not subject to TDVG sub-phase validation. They MAY have a
    tasks.md section (recommended for documentation) — the runner rewrites
    it to a noop marker at startup. They MAY also omit the section
    entirely; either way validation must not flag them as missing."""
    bare = item.rsplit(".", 1)[1] if "." in item else item
    tracks = (config.get("tracks") or {})
    track_cfg = tracks.get(bare) or {}
    return track_cfg.get("type") == "simple"


def validate(change, skip_stages=None):
    config = load_config()
    order = get_pipeline_order(config, change)
    tasks_path = get_tasks_path(change)
    skip_stages = skip_stages or set()

    if not os.path.isfile(tasks_path):
        return {
            "valid": False,
            "items": [],
            "issues": [{
                "severity": "error",
                "code": "tasks_missing",
                "item": None,
                "sub": None,
                "message": f"tasks.md 不存在: {tasks_path}",
            }],
            "summary": {"total_pipeline_items": 0, "ok": 0, "missing": 0,
                        "errors": 1, "warnings": 0, "infos": 0,
                        "skipped_stages": sorted(skip_stages),
                        "skipped_items": []},
        }

    sections, _ = parse_tasks(tasks_path)
    all_pipeline_items = list(order) + ["final-gate"]

    # Filter out environment lifecycle hooks (prepare_env/clean_env) —
    # they are phase items handled directly by the runner, not tracks with
    # tasks.md sections.
    all_pipeline_items = [i for i in all_pipeline_items if not _is_env_hook(i)]

    # Filter out simple tracks — they are executed directly by the runner
    # via _execute_phase and may or may not have a tasks.md section.
    skipped_items = [i for i in all_pipeline_items if _is_simple_track(config, i)]
    all_pipeline_items = [i for i in all_pipeline_items if not _is_simple_track(config, i)]

    # Filter out items whose stage was explicitly skipped via --skip-stages.
    # Stage name is the first segment before "." (e.g. "prepare-env-scripts.env-scripts"
    # → stage "prepare-env-scripts"). Use exact match (not prefix) to avoid
    # accidental matches like "dev-backend-and-agent" matching
    # "dev-backend-and-agent-extra".
    skipped_items += [
        i for i in all_pipeline_items
        if i.split(".", 1)[0] in skip_stages
    ]
    all_pipeline_items = [
        i for i in all_pipeline_items
        if i.split(".", 1)[0] not in skip_stages
    ]

    items_status = []
    issues = []

    for item in all_pipeline_items:
        matches = find_sections_for_item(sections, item)
        if not matches:
            if item == "final-gate":
                issues.append({
                    "severity": "error",
                    "code": "missing_final_gate",
                    "item": item,
                    "sub": None,
                    "message": "tasks.md 缺少 final-gate 章节",
                })
            else:
                issues.append({
                    "severity": "error",
                    "code": "missing_track",
                    "item": item,
                    "sub": None,
                    "message": f"pipeline order 中存在但 tasks.md 无对应章节",
                })
            items_status.append({
                "item": item,
                "status": "missing",
                "sections": [],
            })
            continue

        sec_list = []
        ok = True
        for sec in matches:
            entry = {
                "sub": sec["sub"],
                "unchecked": 0,
                "checked": 0,
                "noop": False,
            }
            if sec["sub"] is not None:
                if sec["sub"] not in VALID_SUBS:
                    issues.append({
                        "severity": "error",
                        "code": "invalid_sub",
                        "item": item,
                        "sub": sec["sub"],
                        "message": f"sub '{sec['sub']}' 不在 {sorted(VALID_SUBS)} 范围内",
                    })
                    ok = False
                    entry["sub"] = sec["sub"]
                un, ch, noop = count_tasks(sec["lines"])
                entry["unchecked"] = un
                entry["checked"] = ch
                entry["noop"] = noop
                if not noop and un + ch == 0:
                    issues.append({
                        "severity": "warning",
                        "code": "empty_section",
                        "item": item,
                        "sub": sec["sub"],
                        "message": f"section {item}:{sec['sub']} 有 0 个 task 且不是 noop",
                    })
            else:
                # Phase-style heading (item - label, no sub)
                un, ch, noop = count_tasks(sec["lines"])
                entry["unchecked"] = un
                entry["checked"] = ch
                entry["noop"] = noop
                if not noop and un + ch == 0:
                    issues.append({
                        "severity": "warning",
                        "code": "empty_section",
                        "item": item,
                        "sub": None,
                        "message": f"section {item} 有 0 个 task 且不是 noop",
                    })
            sec_list.append(entry)

        items_status.append({
            "item": item,
            "status": "ok" if ok else "error",
            "sections": sec_list,
        })

    # Check section order continuity (info level)
    orders = sorted([s["order"] for s in sections])
    for i in range(len(orders) - 1):
        if orders[i + 1] - orders[i] > 1:
            issues.append({
                "severity": "info",
                "code": "order_gap",
                "item": None,
                "sub": None,
                "message": f"章节编号不连续: 从 {orders[i]} 跳到 {orders[i + 1]}",
            })
        elif orders[i + 1] == orders[i]:
            issues.append({
                "severity": "info",
                "code": "order_duplicate",
                "item": None,
                "sub": None,
                "message": f"章节编号重复: {orders[i]} 出现多次",
            })

    errors = len([i for i in issues if i["severity"] == "error"])
    warnings = len([i for i in issues if i["severity"] == "warning"])
    infos = len([i for i in issues if i["severity"] == "info"])
    total_items = len(all_pipeline_items)
    ok_count = len([i for i in items_status if i["status"] == "ok"])
    missing_count = len([i for i in items_status if i["status"] == "missing"])

    return {
        "valid": errors == 0,
        "items": items_status,
        "issues": issues,
        "summary": {
            "total_pipeline_items": total_items,
            "ok": ok_count,
            "missing": missing_count,
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "skipped_stages": sorted(skip_stages),
            "skipped_items": skipped_items,
        },
    }


# ============================================================
# Output helpers
# ============================================================

def _write_human_report(change, report, round_num):
    review_dir = os.path.join(CHANGES_DIR, change, "1-propose-review")
    os.makedirs(review_dir, exist_ok=True)
    path = os.path.join(review_dir, f"validate-tasks-{round_num}.md")

    lines = []
    lines.append("# Tasks.md Runner-Consumability Validation Report\n")
    lines.append(f"**Change**: {change}\n")
    lines.append(f"**Round**: {round_num}\n")

    s = report["summary"]
    valid_mark = "✅" if report["valid"] else "❌"
    lines.append(f"**Valid**: {valid_mark}\n\n")
    lines.append("## Summary\n\n")
    lines.append(f"| Metric | Value |\n")
    lines.append(f"|--------|-------|\n")
    lines.append(f"| Pipeline items | {s['total_pipeline_items']} |\n")
    lines.append(f"| OK | {s['ok']} |\n")
    lines.append(f"| Missing | {s['missing']} |\n")
    lines.append(f"| Errors | {s['errors']} |\n")
    lines.append(f"| Warnings | {s['warnings']} |\n")
    lines.append(f"| Infos | {s['infos']} |\n")
    if s.get("skipped_stages"):
        lines.append(f"| Skipped stages | {', '.join(s['skipped_stages'])} |\n")
    if s.get("skipped_items"):
        lines.append(f"| Skipped items | {len(s['skipped_items'])} |\n")
    lines.append("\n")

    if s.get("skipped_items"):
        lines.append("## Skipped Items\n\n")
        lines.append(f"**Source stages**: {', '.join(s.get('skipped_stages', []))}\n\n")
        lines.append("| Item | Skipped by |\n")
        lines.append("|------|------------|\n")
        for item in s["skipped_items"]:
            stage = item.split(".", 1)[0]
            lines.append(f"| `{item}` | `--skip-stages {stage}` |\n")
        lines.append("\n")
        lines.append("_Skipped items are not validated. The orchestrator should ensure "
                     "these stages are explicitly disabled via on_conditions evaluation "
                     "in `1-propose-review/review-notes.md`._\n\n")

    if report["issues"]:
        lines.append("## Issues\n\n")
        for iss in report["issues"]:
            sev = iss["severity"]
            if sev == "error":
                icon = "🔴"
            elif sev == "warning":
                icon = "🟡"
            else:
                icon = "🔵"
            loc = f"`{iss['item']}`" if iss["item"] else ""
            if iss.get("sub"):
                loc += f":`{iss['sub']}`"
            lines.append(f"### {icon} {sev}: {iss['code']} {loc}\n\n")
            lines.append(f"{iss['message']}\n\n")

    lines.append("---\n\n")
    lines.append("_Auto-generated by pg-validate-tasks.py_\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return path


def _next_round(change):
    review_dir = os.path.join(CHANGES_DIR, change, "1-propose-review")
    if not os.path.isdir(review_dir):
        return 1
    pattern = re.compile(r"^validate-tasks-(\d+)\.md$")
    max_n = 0
    for fname in os.listdir(review_dir):
        m = pattern.match(fname)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


# ============================================================
# CLI
# ============================================================

def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    change = sys.argv[2]
    format_mode = "json"
    skip_stages = set()
    i = 3
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--format" and i + 1 < len(sys.argv):
            format_mode = sys.argv[i + 1]
            i += 2
        elif arg == "--skip-stages" and i + 1 < len(sys.argv):
            for s in sys.argv[i + 1].split(","):
                s = s.strip()
                if s:
                    skip_stages.add(s)
            i += 2
        else:
            i += 1

    if command != "validate":
        print(f"Unknown command: {command}", file=sys.stderr)
        print("Usage: validate <change> [--format human] [--skip-stages <stages>]", file=sys.stderr)
        sys.exit(1)

    report = validate(change, skip_stages=skip_stages)
    round_num = _next_round(change)
    _write_human_report(change, report, round_num)

    if format_mode == "human":
        s = report["summary"]
        print(json.dumps({
            "valid": report["valid"],
            "summary": s,
            "issue_count": len(report["issues"]),
            "human_report": f"1-propose-review/validate-tasks-{round_num}.md",
        }, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    sys.exit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()