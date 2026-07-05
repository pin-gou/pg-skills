"""Snapshot 持久化：保存与恢复最新 PipelineState。

设计：
- snapshot 是 event log 的"派生视图"
- 由 event log 重建：reducer replay 所有 events → 最新 state
- 缓存到 disk：每次 reducer 输出后写入 snapshot.json（供 cold start 快速启动）
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pipeline.event_log import EventLog
from pipeline.state import PipelineState


SNAPSHOT_FILENAME = "pipeline.snapshot.json"


def snapshot_path(change_root: str) -> str:
    """返回 snapshot 文件路径。"""
    return os.path.join(change_root, "2-build", SNAPSHOT_FILENAME)


def save_snapshot(change_root: str, state: PipelineState) -> None:
    """把 state 写到 disk（覆盖式）。"""
    path = snapshot_path(change_root)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def load_snapshot(change_root: str) -> PipelineState | None:
    """从 disk 读取最新 snapshot。文件不存在返回 None。"""
    path = snapshot_path(change_root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return PipelineState.from_dict(data)
    except (json.JSONDecodeError, KeyError, OSError):
        # 损坏的 snapshot 视为不存在
        return None


def rebuild_from_events(
    change_root: str,
    initial_state: PipelineState,
    reducer_fn,
) -> PipelineState:
    """从 event log 重建最新 state。

    Args:
        change_root: change 根目录
        initial_state: 起始 state（一般为空 PipelineState(change=...)）
        reducer_fn: 接受 (state, event_dict) 返回 (new_state, action) 的纯函数
                    注意：reducer 通常返回 action，但 snapshot 重建只需要 state
                    此函数只取 reducer 的第一个返回值（new_state），丢弃 action
    """
    log = EventLog(change_root=change_root)
    state = initial_state
    for event in log.iter_events():
        try:
            new_state, _action = reducer_fn(state, event)
            state = new_state
        except Exception:
            # 单个 event 处理失败不阻塞整体回放
            continue
    return state


def write_snapshot_from_log(change_root: str, reducer_fn) -> PipelineState:
    """重建并写入 snapshot。返回最新 state。"""
    initial = PipelineState(change=os.path.basename(change_root.rstrip("/")))
    state = rebuild_from_events(change_root, initial, reducer_fn)
    save_snapshot(change_root, state)
    return state