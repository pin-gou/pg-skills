"""Tasks.md — checkbox 同步。

tasks.md 的 checkbox 是派生视图，SSOT 是 PipelineState。
此模块提供从 state → tasks.md checkbox 的写入，
以及从 tasks.md → state 的初始读取。
"""

from __future__ import annotations

import os
import re
from typing import Any

from pipeline.state import PipelineState, TrackState, PhaseState, SUB_PHASES


# 匹配 "## N. track:phase - label" 格式的标题
_SECTION_HEADING_RE = re.compile(r"^##\s+(\d+)\.\s+([a-zA-Z0-9_.-]+):([a-zA-Z0-9_-]+)\s*-\s*(.+)$")
# 匹配 "## N. track - label"（无 :sub 的 phase item）
_SECTION_HEADING_NO_SUB = re.compile(r"^##\s+(\d+)\.\s+([a-zA-Z0-9_.-]+)\s*-\s*(.+)$")
# 匹配 task 行 "- [ ] X.Y"
_TASK_RE = re.compile(r"^(\s*-\s*\[\s\]\s*)(\d+)\.(\d+)(.*)$")


def get_tasks_path(change_root: str) -> str:
    return os.path.join(change_root, "tasks.md")


def extract_section_content(change_root: str, track: str, phase: str | None) -> str:
    """从 tasks.md 提取指定 track[:phase] section 的内容。

    返回 section 标题之后、下一个 section 标题之前的所有行。
    phase=None 时匹配 "## N. track - label"（无 :sub 格式）。
    """
    tasks_path = get_tasks_path(change_root)
    if not os.path.isfile(tasks_path):
        return ""

    with open(tasks_path, encoding="utf-8") as f:
        lines = f.readlines()

    in_section = False
    result: list[str] = []

    for line in lines:
        stripped = line.strip()

        m = _SECTION_HEADING_RE.match(stripped)
        if m:
            if in_section:
                break
            in_section = (m.group(2) == track and (phase is None or m.group(3) == phase))
            if in_section:
                result.append(line)
            continue

        m2 = _SECTION_HEADING_NO_SUB.match(stripped)
        if m2:
            if in_section:
                break
            in_section = (m2.group(2) == track)
            if in_section:
                result.append(line)
            continue

        if in_section:
            result.append(line)

    return "".join(result).rstrip()


def mark_task(change_root: str, section_item: str, section_sub: str | None, task_id: int) -> bool:
    """在 tasks.md 中把 - [ ] X.Y 改为 - [x] X.Y。

    如果已经勾选或不存在则什么都不做。

    Returns:
        True if a line was changed
    """
    tasks_path = get_tasks_path(change_root)
    if not os.path.isfile(tasks_path):
        return False

    with open(tasks_path, encoding="utf-8") as f:
        lines = f.readlines()

    changed = False
    in_section = False
    section_item_match = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 检测 section heading
        m = _SECTION_HEADING_RE.match(stripped)
        if m:
            in_section = (m.group(2) == section_item and
                          (section_sub is None or m.group(3) == section_sub))
            section_item_match = True
            continue

        # 检测没有 :sub 的 heading（如 simple track）
        if not section_item_match:
            m2 = _SECTION_HEADING_NO_SUB.match(stripped)
            if m2:
                in_section = (m2.group(2) == section_item)
                section_item_match = True
                continue

        if not in_section:
            continue

        # 检测 task 行
        tm = _TASK_RE.match(stripped)
        if tm and int(tm.group(3)) == task_id:
            prefix = tm.group(1)
            num_x = tm.group(2)
            num_y = tm.group(3)
            rest = tm.group(4)
            new_line = f"{prefix.replace('[ ]', '[x]')}{num_x}.{num_y}{rest}\n"
            lines[i] = new_line
            changed = True
            break  # 只勾第一个匹配项

    if not changed:
        return False

    with open(tasks_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True


def sync_from_state(change_root: str, state: PipelineState) -> int:
    """把 PipelineState 的 completed phases 同步到 tasks.md checkbox。

    Returns:
        number of checkboxes updated
    """
    count = 0
    for track_id, track in state.tracks.items():
        for phase_name, ph in track.phases.items():
            if ph.status == "completed" and ph.tasks_marked:
                for task_id in ph.tasks_marked:
                    if mark_task(change_root, track_id, phase_name, task_id):
                        count += 1
    return count


def mark_phase_completed(change_root: str, track: str, phase: str) -> int:
    """把指定 (track, phase) 的所有未勾 checkbox 一次性勾完。

    Returns:
        勾选的任务数
    """
    tasks_path = get_tasks_path(change_root)
    if not os.path.isfile(tasks_path):
        return 0

    with open(tasks_path, encoding="utf-8") as f:
        lines = f.readlines()

    in_section = False
    updated = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        m = _SECTION_HEADING_RE.match(stripped)
        if m:
            in_section = (m.group(2) == track and m.group(3) == phase)
            continue

        m2 = _SECTION_HEADING_NO_SUB.match(stripped)
        if m2:
            in_section = (m2.group(2) == track)
            continue

        if not in_section:
            continue

        # 如果是新 section heading 且不在当前 section → 退出
        if _SECTION_HEADING_RE.match(stripped) or _SECTION_HEADING_NO_SUB.match(stripped):
            break

        tm = _TASK_RE.match(stripped)
        if tm:
            prefix = tm.group(1)
            num_x = tm.group(2)
            num_y = tm.group(3)
            rest = tm.group(4)
            if "[ ]" in prefix:
                lines[i] = f"{prefix.replace('[ ]', '[x]')}{num_x}.{num_y}{rest}\n"
                updated += 1

    if updated == 0:
        return 0

    with open(tasks_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return updated