#!/usr/bin/env python3
"""pg-gen-manifest.py — Generate execution-manifest.yaml from tasks.md + project.yaml.

Usage:
    python3 pg-gen-manifest.py <change>

Reads tasks.md and project.yaml, then generates .pg/changes/<change>/execution-manifest.yaml
that encodes which stages/tracks to run, their environment, and which tasks.md sections
to use as prompt source for each sub-phase.

This is a pure-function CLI: zero side effects beyond writing the manifest file.
"""

import os
import re
import sys

try:
    import yaml
except ImportError:
    yaml = None

# Add pg-build scripts dir to path for imports
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from pg_pipeline_common import (
    PROJECT_ROOT,
    CHANGES_DIR,
    CONFIG_PATH,
    load_config,
    parse_tasks_sections,
    get_track_type,
    count_tasks,
)


def _parse_stage_env_from_tasks_md(content: str) -> dict[str, str]:
    """Parse environment mapping from tasks.md top block quote.

    Expected format (in the top block quote):
        > - **environment 选择**：dev → dev-local

    Returns dict mapping stage-name to environment-name (e.g. {"dev": "dev-local"}).
    """
    result = {}
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r'>\s*-\s*\*\*environment\s*选择\*\*\s*[:：]\s*(.+?)(?:\s*（|$)', line)
        if not m:
            continue
        for part in m.group(1).split('，'):
            part = part.strip().rstrip('）')
            cm = re.match(r'(\w+)\s*[→➜]\s*(\S+)', part)
            if cm:
                result[cm.group(1)] = cm.group(2)
    return result


_SECTION_KEY_RE = re.compile(
    r'^\d+\.\s+(?P<stage>[\w-]+)\.(?P<track>[\w-]+)'
    r'(?::(?P<sub>[\w-]+))?\s*-\s*(?P<label>.+)$'
)


def _parse_section_key(section_key: str) -> dict | None:
    """Parse a section_key like '1. dev.backend:test - label' into structured fields.

    Returns dict with keys: stage, track, sub (or None), label
    Returns None if parsing fails (not a standard section format).
    """
    m = _SECTION_KEY_RE.match(section_key)
    if not m:
        return None
    return {
        "stage": m.group("stage"),
        "track": m.group("track"),
        "sub": m.group("sub"),
        "label": m.group("label"),
    }


def build_manifest(change: str) -> dict:
    """Build the execution manifest dict for the given change."""
    config = load_config()
    tasks_path = os.path.join(CHANGES_DIR, change, "tasks.md")

    with open(tasks_path, encoding="utf-8") as f:
        tasks_content = f.read()

    sections = parse_tasks_sections(tasks_path)

    # Extract environment mapping from tasks.md top block quote
    env_map = _parse_stage_env_from_tasks_md(tasks_content)
    tracks_cfg = config.get("tracks") or {}

    # Build per-stage, per-track structure from sections
    # stage_tracks: {stage_name: {track_id: {phases: {sub: section_key}, is_simple: bool}}}
    stage_tracks: dict[str, dict] = {}
    final_gate_section = None

    for sec in sections:
        parsed = _parse_section_key(sec["section_key"])
        if parsed is None:
            # Check if this is a final-gate section
            if "final-gate" in sec["section_key"] or "final_gate" in sec["section_key"]:
                final_gate_section = sec["section_key"]
                continue
            continue

        stage = parsed["stage"]
        track = parsed["track"]
        sub = parsed["sub"]

        if stage not in stage_tracks:
            stage_tracks[stage] = {}

        if track not in stage_tracks[stage]:
            stage_tracks[stage][track] = {
                "phases": {},
                "all_noop": True,
                "type": get_track_type(config, track),
            }

        stage_tracks[stage][track]["phases"][sub] = sec["section_key"]

        # Check if this section body is noop
        _, _, all_noop = count_tasks(sec["body"].splitlines(keepends=True))
        if not all_noop:
            stage_tracks[stage][track]["all_noop"] = False

    # Build manifest stages
    manifest_stages = []
    for stage_name, tracks_dict in stage_tracks.items():
        manifest_tracks = []
        for track_id, track_info in tracks_dict.items():
            if track_info["all_noop"]:
                continue

            entry = {
                "id": track_id,
                "type": track_info["type"],
            }

            if track_info["type"] == "simple":
                # Read commands from project.yaml
                track_cfg = tracks_cfg.get(track_id, {})
                raw_commands = track_cfg.get("commands", [])
                commands = []
                for cmd in raw_commands:
                    if isinstance(cmd, str):
                        commands.append(cmd)
                    elif isinstance(cmd, dict):
                        commands.append(cmd.get("cmd", ""))
                entry["commands"] = commands
            else:
                # Standard track: include phase_prompts
                prompts = {}
                for sub_name in ("test", "dev", "verify", "gate"):
                    if sub_name in track_info["phases"]:
                        prompts[sub_name] = {
                            "tasks_md_section": track_info["phases"][sub_name]
                        }
                if prompts:
                    entry["phase_prompts"] = prompts

            manifest_tracks.append(entry)

        if not manifest_tracks:
            continue

        stage_entry = {
            "name": stage_name,
            "environment": env_map.get(stage_name, "dev-local"),
            "tracks": manifest_tracks,
        }
        manifest_stages.append(stage_entry)

    manifest = {
        "schema_version": "2026-06-30",
        "change": change,
        "stages": manifest_stages,
    }

    if final_gate_section:
        manifest["final_gate"] = {
            "tasks_md_section": final_gate_section,
        }

    return manifest


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pg-gen-manifest.py <change>", file=sys.stderr)
        sys.exit(1)

    change = sys.argv[1]
    manifest = build_manifest(change)

    output_path = os.path.join(CHANGES_DIR, change, "execution-manifest.yaml")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(manifest, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"manifest written: {output_path}")
    print(f"  stages: {len(manifest['stages'])}")
    total_tracks = sum(len(s["tracks"]) for s in manifest["stages"])
    print(f"  tracks: {total_tracks}")
    if "final_gate" in manifest:
        print(f"  final-gate: {manifest['final_gate']['tasks_md_section']}")


if __name__ == "__main__":
    main()