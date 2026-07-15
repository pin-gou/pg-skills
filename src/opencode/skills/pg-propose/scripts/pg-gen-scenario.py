#!/usr/bin/env python3
"""pg-gen-scenario.py — Generate per-track scenario-<track>.yaml skeletons.

Usage:
    python3 pg-gen-scenario.py <change>

Reads `.pg/changes/<change>/1-propose-review/on-conditions-eval.md` to find
`scenario_tracks_decision` segment (SSOT written by `pg-gen-tasks-skeleton.py`).

Behavior:
  - For each enabled scenario track: write `scenario-<track-id>.yaml` skeleton
    (LLM 必须在阶段三自审时填充 Scenario 内容)
  - No enabled tracks → no-op (do NOT write any scenario files)
  - decision missing → emit error: must run `pg-gen-tasks-skeleton.py` first

This script is pure-function (zero side effects beyond writing scenario files).
"""

import json
import os
import sys

try:
    import yaml
except ImportError:
    print('{"error": "PyYAML is required. Install with: pip install pyyaml"}',
          file=sys.stderr)
    sys.exit(1)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from pg_pipeline_common import CHANGES_DIR

_DECISION_MARKER = "## scenario_tracks_decision (v3.6)"


def _read_scenario_decisions(change: str) -> dict | None:
    """从 on-conditions-eval.md 读取 scenario_tracks_decision 段 (多 track).

    Returns:
        dict of {track_id: {enabled: bool, mode: str, reason: str}}
        或 None (eval.md 不存在 / 段缺失)
    """
    eval_path = os.path.join(
        CHANGES_DIR, change, "1-propose-review", "on-conditions-eval.md"
    )
    if not os.path.isfile(eval_path):
        return None
    try:
        with open(eval_path, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    if _DECISION_MARKER not in content:
        return None
    section = content.split(_DECISION_MARKER, 1)[1]
    section = section.split("\n## ", 1)[0]

    decisions: dict[str, dict] = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        parts = [p.strip().strip("*") for p in line.split("|")]
        if len(parts) < 5:
            continue
        tid = parts[1].strip()
        if not tid or tid == "track_id":
            continue
        decisions[tid] = {
            "enabled": parts[2].strip().lower() == "true",
            "mode": parts[3].strip(),
            "reason": parts[4].strip(),
        }
    return decisions if decisions else None


def _build_skeleton_yaml(change: str, track_id: str) -> dict:
    """构造 scenario-<track-id>.yaml skeleton —— LLM 在阶段三自审时填充。"""
    return {
        "scenarios": [
            {
                "scenario_id": "S-<unique-name>",
                "critical": True,
                "description": "一句话描述此 Scenario 验证目标（LLM 必填）",
                "given": [
                    "<前置条件 1>",
                    "<前置条件 2>",
                ],
                "when": [
                    {
                        "name": "<动作名>",
                        "method": "GET",
                        "url": "/api/.../...",
                        "expect_status": 200,
                    },
                ],
                "then": [
                    "status_code == 200",
                    "response.<field> matches <regex>",
                ],
                "and": [
                    {"name": "<cleanup>", "action": "HTTP DELETE /api/.../.../..."},
                ],
                "evidence": [
                    "2-build/<scenario_id>-evidence.json",
                ],
            },
        ],
        "_meta": {
            "_comment": (
                "scenario.yaml 由 pg-gen-scenario.py 生成的 skeleton, "
                "LLM 必填。scenario_id / given / when / then / and / evidence "
                "是必填段, critical / description 必填, _meta 段最终会被 pg-build "
                "scenario-execute agent 忽略。"
            ),
            "change": change,
            "track_id": track_id,
            "schema_version": "v3.6",
        },
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pg-gen-scenario.py <change>", file=sys.stderr)
        sys.exit(1)

    change = sys.argv[1]
    decisions = _read_scenario_decisions(change)

    if decisions is None:
        print(
            f"ERROR: scenario_tracks_decision 段缺失, 必须先跑:\n"
            f"  python3 .opencode/skills/pg-propose/scripts/pg-gen-tasks-skeleton.py "
            f"--change {change} --scenario-decisions 'track1=true,track2=auto' --scenario-reason '...' ...",
            file=sys.stderr,
        )
        sys.exit(1)

    enabled_tracks = {tid: d for tid, d in decisions.items() if d["enabled"]}
    if not enabled_tracks:
        result = {
            "scenario_files_written": [],
            "scenario_tracks_enabled": False,
            "action": "skipped (no scenario track enabled)",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    written = []
    for track_id, decision in enabled_tracks.items():
        filename = f"scenario-{track_id}.yaml"
        scenario_path = os.path.join(CHANGES_DIR, change, filename)
        os.makedirs(os.path.dirname(scenario_path), exist_ok=True)
        skeleton = _build_skeleton_yaml(change, track_id)
        with open(scenario_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                skeleton, f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        written.append(filename)

    result = {
        "scenario_files_written": written,
        "scenario_tracks_enabled": True,
        "action": f"skeletons written for {len(written)} track(s): {', '.join(written)}",
        "reason": next(iter(enabled_tracks.values()))["reason"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()