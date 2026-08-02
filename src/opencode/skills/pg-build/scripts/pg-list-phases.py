#!/usr/bin/env python3
"""pg-list-phases.py — 派生 TODO 列表数据 from execution-manifest.yaml + snapshot.

用途：
  为编排器（pg-manager LLM agent）提供机械派生的 TODO 列表数据源，
  替代 LLM 自行概括固定 4 步骤的旧行为。

输出模式：
  默认模式（--init）：
    输出 manifest 中所有 phase 项（不含 sub-pipeline）。
    每项含 id/track/phase/stage/label/status/kind 字段。
    status 全部为 pending。
    调用方（LLM）按 label 字段逐项调 todowrite 创建 TODO 列表。

  --with-progress 模式：
    额外读取 pipeline.snapshot.json + 调用 runner progress，
    按当前 orchestrator 状态给每项打 status（completed/in_progress/pending）。
    调用方按 status 字段刷新 todowrite 的 content 前缀（[x]/[•]/[ ]）。

  --detect-sub-pipelines 模式：
    读取 snapshot 的 current_sub_pipeline 字段，
    输出当前活跃的子 pipeline 项（如 fix cycle 1 - backend verify）。
    调用方追加到 todowrite 末尾（status 默认 in_progress）。

设计原则：
  - 零副作用：纯读取，不修改任何文件。
  - 容错：snapshot 缺失、manifest 缺失、JSON 损坏都不致命，
    只输出警告到 stderr 并降级到无 progress / 无 sub_pipeline 数据。
  - stdout 仅为 JSON（无附加日志），便于 LLM 直接 json.loads。

用法：
  python3 pg-list-phases.py <change>
  python3 pg-list-phases.py <change> --with-progress
  python3 pg-list-phases.py <change> --detect-sub-pipelines
  python3 pg-list-phases.py <change> --with-progress --detect-sub-pipelines
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys


_SECTION_KEY_RE = re.compile(
    r"^\d+\.\s+(?P<stage>[\w-]+)\.(?P<track>[\w-]+)"
    r"(?::(?P<sub>[\w-]+))?\s*-\s*(?P<label>.+)$"
)

# final-gate / 纯 item 无 sub 时 fallback 解析（标题无 '.' 或仅一项）
_FINAL_GATE_RE = re.compile(r"^\d+\.\s+(?P<item>[\w-]+)\s*-\s*(?P<label>.+)$")

# scenario track 的唯一 sub-phase
_SCENARIO_SUB = "scenario-execute"


def _parse_section_key(section_key: str) -> dict | None:
    """解析 '1. dev.backend:test - xxx' 为结构化字段.

    Returns:
        {stage, track, sub, label} 或 None（解析失败）。
        'final-gate' / 无 sub 的条目用 item 作为 track，sub=None。
    """
    m = _SECTION_KEY_RE.match(section_key)
    if not m:
        m2 = _FINAL_GATE_RE.match(section_key)
        if not m2:
            return None
        return {
            "stage": m2.group("item"),
            "track": m2.group("item"),
            "sub": None,
            "label": m2.group("label"),
        }
    return {
        "stage": m.group("stage"),
        "track": m.group("track"),
        "sub": m.group("sub"),
        "label": m.group("label"),
    }


def _resolve_project_root() -> str:
    """解析 PG_PROJECT_ROOT，否则向上查找 .pg/project.yaml."""
    env_root = os.environ.get("PG_PROJECT_ROOT")
    if env_root and os.path.isfile(os.path.join(env_root, ".pg", "project.yaml")):
        return env_root
    cwd = os.getcwd()
    p = cwd
    for _ in range(6):
        if os.path.isfile(os.path.join(p, ".pg", "project.yaml")):
            return p
        p = os.path.dirname(p)
    return cwd


def _change_root(project_root: str, change: str) -> str:
    """返回 change 的绝对路径（兼容 change 含斜杠的形态）."""
    name = os.path.basename(change.rstrip("/")) if "/" in change else change
    return os.path.join(project_root, ".pg", "changes", name)


def _load_manifest(change_root: str) -> dict | None:
    """读取 execution-manifest.yaml，失败返回 None."""
    path = os.path.join(change_root, "execution-manifest.yaml")
    if not os.path.isfile(path):
        return None
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[pg-list-phases] manifest 解析失败: {e}", file=sys.stderr)
        return None


def _load_snapshot(change_root: str) -> dict | None:
    """读取 pipeline.snapshot.json，失败返回 None."""
    path = os.path.join(change_root, "2-build", "pipeline.snapshot.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[pg-list-phases] snapshot 解析失败: {e}", file=sys.stderr)
        return None


def _load_progress_via_runner(project_root: str, change: str) -> dict | None:
    """通过调 runner progress 获取实时进度. 失败返回 None."""
    runner = os.path.join(
        project_root, ".opencode", "skills", "pg-build", "scripts",
        "pg-pipeline-runner.py",
    )
    if not os.path.isfile(runner):
        print(f"[pg-list-phases] runner 不存在: {runner}", file=sys.stderr)
        return None
    try:
        proc = subprocess.run(
            [sys.executable, runner, "progress", change],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            print(
                f"[pg-list-phases] runner progress 非 0 退出: "
                f"rc={proc.returncode}, stderr={proc.stderr.strip()}",
                file=sys.stderr,
            )
            return None
        return json.loads(proc.stdout)
    except Exception as e:
        print(f"[pg-list-phases] runner progress 调用失败: {e}", file=sys.stderr)
        return None


def _build_items_from_manifest(manifest: dict) -> list[dict]:
    """从 manifest.stages.tracks[].phase_prompts 派生顶层 phase items."""
    items: list[dict] = []
    for stage in manifest.get("stages") or []:
        stage_name = stage.get("name", "")
        for track in stage.get("tracks") or []:
            if not isinstance(track, dict):
                continue
            tid = track.get("id", "")
            if not track.get("enabled", True):
                continue
            track_type = track.get("type", "standard")
            prompts = track.get("phase_prompts") or {}
            for sub, prompt in prompts.items():
                tasks_section = prompt.get("tasks_md_section", "")
                parsed = _parse_section_key(tasks_section)
                label = parsed["label"] if parsed else tasks_section
                items.append({
                    "id": f"{stage_name}.{tid}:{sub}",
                    "track": tid,
                    "phase": sub,
                    "stage": stage_name,
                    "label": f"{tid}:{sub} - {label}" if label else f"{tid}:{sub}",
                    "status": "pending",
                    "kind": "phase",
                    "track_type": track_type,
                })
    final_gate = manifest.get("final_gate")
    if final_gate:
        section = final_gate.get("tasks_md_section", "final-gate")
        parsed = _parse_section_key(section)
        label = parsed["label"] if parsed else "最终门控审查"
        items.append({
            "id": "final-gate",
            "track": "final-gate",
            "phase": "gate",
            "stage": "final",
            "label": f"final-gate - {label}",
            "status": "pending",
            "kind": "final-gate",
            "track_type": "final-gate",
        })
    return items


def _apply_progress(
    items: list[dict],
    progress: dict | None,
    snapshot: dict | None,
) -> None:
    """根据 progress + snapshot 给 items 打 status（in-place）."""
    if not progress and not snapshot:
        return
    if snapshot:
        current_track = snapshot.get("current_track", "") or ""
        current_phase = snapshot.get("current_phase", "") or ""
        status_overall = snapshot.get("status", "")
        if status_overall == "completed":
            for item in items:
                item["status"] = "completed"
            return
        if not current_track:
            return
        current_match = False
        for item in items:
            kind = item.get("kind", "phase")
            if kind == "final-gate":
                if current_track == "final-gate":
                    current_match = True
                    item["status"] = "in_progress"
                elif _is_track_completed(snapshot, "final-gate"):
                    item["status"] = "completed"
                continue
            item_track = item["track"]
            if current_track.endswith(f".{item_track}") or current_track == item_track:
                if item["phase"] == current_phase:
                    item["status"] = "in_progress"
                    current_match = True
                elif _is_phase_already_completed(snapshot, current_track, item["phase"]):
                    item["status"] = "completed"
                elif item["phase"] != current_phase and current_match:
                    item["status"] = "pending"
                else:
                    item["status"] = "pending"
            else:
                if _is_track_completed(snapshot, item["track"]):
                    item["status"] = "completed"


def _is_phase_already_completed(
    snapshot: dict, qualified_track: str, phase: str,
) -> bool:
    """判断 snapshot 中某 qualified_track 的某 phase 是否已完成."""
    tracks = snapshot.get("tracks") or {}
    track_state = tracks.get(qualified_track)
    if not track_state:
        return False
    phases = track_state.get("phases") or {}
    phase_state = phases.get(phase)
    if not phase_state:
        return False
    return phase_state.get("status") in ("completed", "pass", "skipped")


def _is_track_completed(snapshot: dict, bare_track: str) -> bool:
    """判断 bare track 名在 snapshot 中是否所有 phase 都已完成."""
    tracks = snapshot.get("tracks") or {}
    for qtid, track_state in tracks.items():
        if not (qtid == bare_track or qtid.endswith(f".{bare_track}")):
            continue
        if track_state.get("status") == "completed":
            return True
        phases = track_state.get("phases") or {}
        if phases and all(
            ps.get("status") in ("completed", "pass", "skipped")
            for ps in phases.values()
        ):
            return True
    return False


def _detect_sub_pipelines(snapshot: dict | None) -> list[dict]:
    """从 snapshot.current_sub_pipeline 派生 sub-pipeline items."""
    items: list[dict] = []
    if not snapshot:
        return items
    sp = snapshot.get("current_sub_pipeline")
    if not sp:
        return items
    if not isinstance(sp, dict):
        return items
    kind = sp.get("kind", "")
    parent_track = sp.get("parent_track", "")
    parent_phase = sp.get("parent_phase", "")
    cycle = sp.get("cycle", 1)
    phases = sp.get("phases") or []
    current_index = sp.get("current_index", 0)
    bare = parent_track.rsplit(".", 1)[-1] if "." in parent_track else parent_track
    for i, ph in enumerate(phases):
        sub_status = "in_progress" if i == current_index else "pending"
        if i < current_index:
            sub_status = "completed"
        items.append({
            "id": f"{bare}:{kind}-{cycle}:{ph}-{i}",
            "track": bare,
            "phase": ph,
            "stage": parent_track.split(".", 1)[0] if "." in parent_track else "",
            "label": (
                f"[{kind} cycle {cycle}] {bare} {parent_phase} → {ph}"
                if i == current_index
                else f"[{kind} cycle {cycle}] {bare} {parent_phase} → {ph} (queued)"
            ),
            "status": sub_status,
            "kind": kind,
            "parent_track": parent_track,
            "parent_phase": parent_phase,
            "cycle": cycle,
        })
    return items


def main() -> None:
    parser = argparse.ArgumentParser(
        description="派生 pg-build TODO 列表数据 from execution-manifest.yaml",
    )
    parser.add_argument("change", help="change 名")
    parser.add_argument(
        "--with-progress",
        action="store_true",
        help="读取 snapshot + runner progress 给 items 打 status",
    )
    parser.add_argument(
        "--detect-sub-pipelines",
        action="store_true",
        help="从 snapshot 检测活跃 sub-pipeline 并追加",
    )
    args = parser.parse_args()

    project_root = _resolve_project_root()
    change_root = _change_root(project_root, args.change)

    manifest = _load_manifest(change_root)
    if manifest is None:
        print(
            json.dumps(
                {"error": "execution-manifest.yaml 缺失或解析失败",
                 "change": args.change, "items": []},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    items = _build_items_from_manifest(manifest)
    sub_items: list[dict] = []
    snapshot = _load_snapshot(change_root) if (
        args.with_progress or args.detect_sub_pipelines
    ) else None

    if args.with_progress:
        progress = _load_progress_via_runner(project_root, args.change)
        _apply_progress(items, progress, snapshot)

    if args.detect_sub_pipelines:
        sub_items = _detect_sub_pipelines(snapshot)

    output = {
        "change": args.change,
        "manifest_present": True,
        "items": items,
        "sub_pipeline_items": sub_items,
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()