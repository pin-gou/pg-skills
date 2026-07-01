"""Replay — 从 pipeline.events 重建 PipelineState。

v2.1 引入：当 pipeline.snapshot.json 损坏或缺失时，可从 append-only event log
完整重建状态。这提供 checkpoint / resume 能力：

  - 编排器崩溃后无需 snapshot.json 即可继续
  - 调试时可对比 snapshot 与 replay 结果，发现 reducer bug
  - 实现 time-travel debugging（从任意 event 回放）

实现方式：扫描 pipeline.events，按顺序对每个 event 调用 reducer.reduce_state。
初始状态为空 PipelineState(change=change)，bootstrap 事件不通过 reducer 处理，
而是直接通过编排器的 _first_next 重建（保留 SSOT 语义）。
"""

from __future__ import annotations

import json
import os
from typing import Any

from pipeline.state import PipelineState, TrackState, PhaseState
from pipeline.reducer import reduce_state
from pipeline.events import PipelineRecord, EVT_RECORD_RECEIVED


def load_events(change_root: str) -> list[dict[str, Any]]:
    """读取 pipeline.events 并解析为 list of event dict。

    Returns:
        空列表：文件不存在
        list：按文件顺序返回的 event dict
    """
    events_path = os.path.join(change_root, "2-build", "pipeline.events")
    if not os.path.isfile(events_path):
        return []

    events: list[dict[str, Any]] = []
    with open(events_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                # 单行损坏不阻断整体回放；记录警告
                print(
                    f"[replay] WARN: 跳过损坏的 event 第 {lineno} 行: {e}",
                    file=__import__("sys").stderr,
                )
    return events


def replay_state(change_root: str) -> PipelineState:
    """从 pipeline.events 重建 PipelineState。

    Args:
        change_root: change 根目录

    Returns:
        重建后的 PipelineState
        若 events 为空或无 record_received，返回 empty PipelineState

    Notes:
        - bootstrap 阶段的事件（pipeline_started 等）不参与 reducer，
          它们只用于初始化 pipeline_order 等上下文。
        - 仅 record_received 事件影响 state。
        - 编排器后续需通过 _first_next() 再次 bootstrap 阶段设置 stage_prepared 等。
        - 若 change_root 含路径分隔符（如 archive/<date>-<name>），取最后一段作为 change。
    """
    basename = os.path.basename(change_root.rstrip("/"))
    # 处理 archive/<date>-<name> 形式：取 <date>-<name>
    parent = os.path.basename(os.path.dirname(change_root.rstrip("/")))
    if parent == "archive" and "-" in basename:
        # archive/<date>-<name> → 用 <date>-<name> 作为 change name
        change = basename
    else:
        change = basename
    events = load_events(change_root)

    state = PipelineState(change=change)

    for event in events:
        if event.get("type") != EVT_RECORD_RECEIVED:
            # 非 record 事件（pipeline_started / dispatch_started 等）→ 跳过
            continue
        data = event.get("data", {})

        # 构造 PipelineRecord（与 orchestrator.record 中一致）
        record = PipelineRecord(
            track=data.get("track", ""),
            phase=data.get("phase", ""),
            status=data.get("status", ""),
            summary=data.get("summary", ""),
            report_path=data.get("report_path"),
            issues=data.get("issues", ""),
            attempt=data.get("attempt", 1),
            cycle=data.get("cycle", 1),
        )

        # 跳过没有 track/phase 的事件（如 invalid record）
        if not record.track or not record.phase:
            continue

        new_state, action = reduce_state(state, record)
        # 即便 reducer 返回 error / workflow_failed 也接受，仅记录
        state = new_state

    return state


def verify_snapshot_matches_replay(change_root: str) -> tuple[bool, str]:
    """对比 snapshot.json 与 replay 结果。

    用于检测 reducer bug / snapshot 漂移。

    v2.1 已知限制：events 不携带 track 创建事件（track 在 _first_next 中由 snapshot 初始化），
    因此 replay 的 tracks 字典可能为空，但 phase 状态在 track 存在时是正确的。
    此函数重点对比 status / current_track / current_phase 字段。

    Returns:
        (ok, message):
            ok=True → 一致
            ok=False → message 是差异说明
    """
    from pipeline.snapshot import load_snapshot

    snap = load_snapshot(change_root)
    if snap is None:
        return True, "snapshot 不存在，仅返回 replay 结果"

    replayed = replay_state(change_root)

    # 简化对比：只比较顶层状态字段，phase 状态由 snapshot 提供 tracks 上下文后再回放
    snap_summary = {
        "status": snap.status,
        "current_track": snap.current_track,
        "current_phase": snap.current_phase,
        "failed_reason": snap.failed_reason,
    }
    replay_summary = {
        "status": replayed.status,
        "current_track": replayed.current_track,
        "current_phase": replayed.current_phase,
        "failed_reason": replayed.failed_reason,
    }

    if snap_summary == replay_summary:
        return True, "snapshot 与 replay 一致（顶层状态字段匹配）"

    diff_keys = []
    for k in snap_summary:
        if snap_summary[k] != replay_summary[k]:
            diff_keys.append(k)
    return False, f"差异字段: {diff_keys}\nsnapshot: {snap_summary}\nreplay: {replay_summary}"