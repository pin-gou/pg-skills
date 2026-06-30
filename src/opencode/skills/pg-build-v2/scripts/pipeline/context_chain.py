"""Context Chain — 从 event log 生成可读的 context-chain.md。

主要供人阅读和审计。机器状态走 event log。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any


_SHANGHAI = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(_SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def context_chain_path(change_root: str) -> str:
    return os.path.join(change_root, "2-build", "context-chain.md")


def init_context_chain(change_root: str) -> None:
    """初始化 context-chain.md。"""
    path = context_chain_path(change_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Context Chain - {os.path.basename(change_root)}\n\n")
        f.write("---\n")
        f.write("*此文件由编排器自动管理，请勿手动修改*\n\n")


def append_event(change_root: str, event: dict[str, Any]) -> None:
    """根据 event 类型追加可读行到 context-chain.md。

    Args:
        change_root: change 根目录
        event: 来自 event log 的一条 event（含 ts, type, data）
    """
    path = context_chain_path(change_root)
    event_type = event.get("type", "")
    ts = event.get("ts", _now_iso())
    data = event.get("data", {})

    lines: list[str] = []

    if event_type == "pipeline_started":
        lines.append(f"### {ts} - PIPELINE STARTED\n")
        lines.append(f"**change**: {data.get('change', '')}\n\n")

    elif event_type == "dispatch_started":
        track = data.get("track", "")
        phase = data.get("phase", "")
        attempt = data.get("attempt", 1)
        lines.append(f"### {ts} - {track}:{phase} START\n")
        lines.append(f"**状态**: IN_PROGRESS (attempt {attempt})\n\n")

    elif event_type == "record_received":
        track = data.get("track", "")
        phase = data.get("phase", "")
        status = data.get("status", "")
        summary = data.get("summary", "")
        lines.append(f"### {ts} - {track}:{phase} END\n")
        lines.append(f"**状态**: {status.upper()}\n")
        if summary:
            lines.append(f"**摘要**: {summary}\n")
        if data.get("report_path"):
            lines.append(f"**报告**: {data['report_path']}\n")
        lines.append("\n")

    elif event_type == "track_completed":
        track = data.get("track", "")
        lines.append(f"### {ts} - {track} COMPLETED\n\n")

    elif event_type == "pipeline_completed":
        lines.append(f"### {ts} - PIPELINE COMPLETED\n")
        lines.append(f"**状态**: {data.get('final_status', 'completed')}\n\n")

    elif event_type == "workflow_failed":
        lines.append(f"### {ts} - WORKFLOW FAILED\n")
        lines.append(f"**原因**: {data.get('reason', 'unknown')}\n\n")

    elif event_type == "prepare_env_completed":
        ok = data.get("success", False)
        skipped = data.get("skipped", False)
        label = "SKIPPED" if skipped else ("OK" if ok else "FAILED")
        lines.append(f"### {ts} - PREPARE_ENV {label}\n")
        if data.get("log_path"):
            lines.append(f"**日志**: {data['log_path']}\n")
        lines.append("\n")

    elif event_type == "fix_cycle_started":
        lines.append(f"### {ts} - FIX CYCLE {data.get('cycle', '')}\n")

    elif event_type == "gate_cycle_started":
        lines.append(f"### {ts} - GATE FIX CYCLE {data.get('cycle', '')}")
        if data.get("cycles_remaining") is not None:
            lines.append(f" ({data['cycles_remaining']} remaining)")
        lines.append("\n")

    elif event_type == "git_commit":
        sha = data.get("sha", "")
        msg = data.get("message", "")
        lines.append(f"### {ts} - GIT COMMIT\n")
        lines.append(f"**sha**: {sha}\n")
        lines.append(f"**message**: {msg}\n\n")

    if not lines:
        return

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(lines)


def rebuild_from_events(change_root: str, events: list[dict[str, Any]]) -> str:
    """根据 event 列表重建完整的 context-chain.md。

    Returns:
        context-chain.md 的完整内容（字符串）
    """
    init_context_chain(change_root)
    for event in events:
        append_event(change_root, event)
    with open(context_chain_path(change_root), encoding="utf-8") as f:
        return f.read()