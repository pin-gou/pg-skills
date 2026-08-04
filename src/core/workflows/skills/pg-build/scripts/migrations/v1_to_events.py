#!/usr/bin/env python3
"""v1_to_events.py — 读取旧 .pipeline-state.json 生成新 pipeline.events。

用法：
  python3 v1_to_events.py <change_root>

从 v1 的 dispatch_history / completed_items 重建新 v2 event log。
（v1 的 context-chain.md 数据已被 dispatch_history + completed_items 完整覆盖，无需额外读取。）
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Any


_SHANGHAI = timezone(timedelta(hours=8))

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from pipeline.event_log import EventLog
from pipeline.snapshot import save_snapshot, snapshot_path
from pipeline.state import PipelineState, TrackState, PhaseState


def _now_iso() -> str:
    return datetime.now(_SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def migrate(change_root: str) -> dict[str, Any]:
    """把旧 state 文件迁移到新 event log 格式。

    Args:
        change_root: change 根目录

    Returns:
        {"ok": bool, "events_written": int, "snapshot_written": bool, "warnings": [str]}
    """
    result: dict[str, Any] = {
        "ok": True,
        "events_written": 0,
        "snapshot_written": False,
        "warnings": [],
    }

    # 已有新 event log → 跳过
    event_log = EventLog(change_root=change_root)
    if not event_log.is_empty():
        result["warnings"].append("event log 已存在，跳过迁移")
        return result

    change = os.path.basename(change_root)

    # 读取旧 v1 state
    v1_state = _read_v1_state(change_root)
    if v1_state is None:
        result["warnings"].append("未找到旧 .pipeline-state.json，跳过迁移")
        return result

    # 写入 event log
    events: list[dict[str, Any]] = []

    # pipeline_started
    events.append({
        "ts": _now_iso(),
        "type": "pipeline_started",
        "data": {"change": change, "pipeline_order": v1_state.get("pipeline_order", [])},
    })

    # dispatch_history (从 v1 state 获取)
    for entry in v1_state.get("dispatch_history", []):
        events.append({
            "ts": entry.get("started_at", _now_iso()),
            "type": "dispatch_started",
            "data": {
                "track": entry.get("track", entry.get("item", "")),
                "phase": entry.get("phase", entry.get("sub", "")),
                "agent": entry.get("agent", ""),
                "attempt": entry.get("attempt", 1),
            },
        })
        # 如果有 result，写 record_received
        result_entry = entry.get("result")
        if result_entry and result_entry != "pending":
            events.append({
                "ts": _now_iso(),
                "type": "record_received",
                "data": {
                    "track": entry.get("track", entry.get("item", "")),
                    "phase": entry.get("phase", entry.get("sub", "")),
                    "status": result_entry,
                    "summary": "",
                },
            })

    # completed_items → track_completed
    for item in v1_state.get("completed_items", []):
        events.append({
            "ts": _now_iso(),
            "type": "track_completed",
            "data": {"track": item},
        })

    # 最终状态
    if v1_state.get("completed"):
        events.append({
            "ts": _now_iso(),
            "type": "pipeline_completed",
            "data": {"final_status": "completed"},
        })
    elif v1_state.get("failed"):
        events.append({
            "ts": _now_iso(),
            "type": "workflow_failed",
            "data": {"reason": v1_state.get("fail_reason", "unknown")},
        })

    # 写 events
    for event in events:
        event_log.append(event["type"], event["data"], ts=event["ts"])
    result["events_written"] = len(events)

    # 尝试从旧 dispatch_history + completed_items 重建 PipelineState
    try:
        state = _rebuild_state_from_v1(v1_state, change)
        save_snapshot(change_root, state)
        result["snapshot_written"] = True
    except Exception as e:
        result["warnings"].append(f"snapshot 重建失败: {e}")

    return result


def _read_v1_state(change_root: str) -> dict[str, Any] | None:
    """读取旧 v1 .pipeline-state.json 或 .pipeline-state.json (v1 schema)。"""
    # 先找 2-build/ 下的 state
    for path in [
        os.path.join(change_root, "2-build", ".pipeline-state.json"),
        os.path.join(change_root, ".pipeline-state.json"),
    ]:
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _rebuild_state_from_v1(v1: dict[str, Any], change: str) -> PipelineState:
    """从旧 v1 state dict 重建 v2 PipelineState。"""
    version = v1.get("version", 1)
    pipeline_order = v1.get("pipeline_order") or v1.get("context", {}).get("pipeline_order", [])

    tracks: dict[str, TrackState] = {}

    # 从 tracks 字段重建
    v1_tracks = v1.get("tracks", {})
    for tid, tdata in v1_tracks.items():
        bare = tdata.get("bare", tid.rsplit(".", 1)[-1] if "." in tid else tid)
        phases: dict[str, PhaseState] = {}
        for pname, pdata in (tdata.get("phases", {})).items():
            phases[pname] = PhaseState(
                status=pdata.get("status", "pending"),
                attempt=pdata.get("attempt", 0),
                report_path=pdata.get("report_path"),
                summary=pdata.get("summary", ""),
            )
        tracks[tid] = TrackState(
            track_id=tid,
            bare=bare,
            status=tdata.get("status", "pending"),
            phases=phases,
        )

    # 从 completed_items 补缺失的 track
    for item in v1.get("completed_items", []):
        if item not in tracks:
            bare = item.rsplit(".", 1)[-1] if "." in item else item
            tracks[item] = TrackState(track_id=item, bare=bare, status="completed")

    # context 信息
    ctx = v1.get("context", {})
    failed_reason = ctx.get("failed_reason") if version >= 2 else v1.get("fail_reason")

    return PipelineState(
        change=change,
        pipeline_order=tuple(pipeline_order),
        tracks=tracks,
        status="completed" if v1.get("completed") else ("failed" if v1.get("failed") else "running"),
        failed_reason=failed_reason,
        init_committed=ctx.get("init_committed", False),
        init_commit_sha=ctx.get("init_commit_sha"),
        feature_branch=ctx.get("feature_branch"),
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python3 v1_to_events.py <change_root>", file=sys.stderr)
        sys.exit(1)

    change_root = sys.argv[1]
    if not os.path.isdir(change_root):
        print(f"错误: 目录不存在 {change_root}", file=sys.stderr)
        sys.exit(1)

    result = migrate(change_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("events_written", 0) > 0:
        print(f"迁移完成: {result['events_written']} events")
    if result.get("warnings"):
        for w in result["warnings"]:
            print(f"警告: {w}", file=sys.stderr)


if __name__ == "__main__":
    main()