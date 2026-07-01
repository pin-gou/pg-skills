"""Dispatch — 构建 action JSON 与 dispatch_file。"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from pipeline.events import PipelineAction
from pipeline.state import PipelineState, TrackState, PhaseState
from template_engine.renderer import render_dispatch_file


_SHANGHAI = timezone(timedelta(hours=8))
PHASE_AGENTS: dict[str, str] = {
    "test":     "pg-build/test",
    "dev":      "pg-build/dev",
    "verify":   "pg-build/verify",
    "gate":     "pg-build/gate",
    "fix":      "pg-build/fix",
    "fix-gate": "pg-build/fix-gate",
    "simple":   "pg-build/simple",
}
FINAL_GATE_AGENT = "pg-build/gate"

_SEQ_COUNTERS: dict[str, int] = {}


def _allocate_seq(change_root: str) -> int:
    """分配下一个全局递增 seq。

    首次调用时扫描 2-build/ 下 NNN-*.md 文件找最大 seq 作为种子，
    后续仅仅递增 in-memory counter。
    """
    build_dir = os.path.join(change_root, "2-build")
    if change_root not in _SEQ_COUNTERS:
        max_seen = 0
        if os.path.isdir(build_dir):
            for fname in os.listdir(build_dir):
                if not fname.endswith(".md"):
                    continue
                m = re.match(r"^(\d{3})-", fname)
                if m:
                    try:
                        max_seen = max(max_seen, int(m.group(1)))
                    except ValueError:
                        continue
        _SEQ_COUNTERS[change_root] = max_seen
    _SEQ_COUNTERS[change_root] += 1
    return _SEQ_COUNTERS[change_root]


def _format_seq(seq: int) -> str:
    """3 位零填充格式化。"""
    return f"{seq:03d}"


def build_ctx(
    state: PipelineState,
    track: str,
    phase: str,
    cycle: int = 1,
) -> dict[str, Any]:
    """构建 dispatch 上下文 dict。

    从 PipelineState 的 TrackState 中提取 sub-agent 所需的配置字段，
    优先使用 Step 4 富化后的字段，fallback 到硬编码默认值。
    """
    t = state.tracks.get(track, TrackState.create(track))
    ph = t.phases.get(phase, PhaseState())

    tasks_preformatted = t.tasks_by_phase.get(phase, "")
    tasks_validation = t.tasks_by_phase.get("verify", "")

    ctx: dict[str, Any] = {
        "_change": state.change,
        "id": track,
        "bare": t.bare,
        "label": t.label or track,
        "modules": list(t.modules),
        "module_roots": t.module_roots or "[]",
        "module_details": t.module_details or "",
        "review_level": t.review_level or "",
        "max_fix_retries": t.max_fix_retries,
        "fix_routing": "source",
        # stage
        "stage_name": track.rsplit(".", 1)[0] if "." in track else "dev",
        "test_key": "unit",
        "gate": "all_pass",
        "env_required": True,
        "env_name": t.env_name or "dev-local",
        "prepare_status": t.prepare_status or "ok",
        "prepare_log_path": t.prepare_log_path or "",
        "test_commands": t.test_commands or "",
        "env_instances": t.env_instances_yaml or "",
        # phase
        "phase": phase,
        "cycle": cycle,
        "attempt": ph.attempt or 1,
        # verify / gate — report_filename 由 build_action 动态分配 seq 后覆盖
        "report_filename": f"{track}-{phase}-report.md",
        "report_seq": 1,
        # fix
        "fix_cycle": cycle,
        "verify_report_path": "",
        "fix_report_filename": f"{track}-{phase}-fix-{cycle}.md",
        # fix-gate
        "gate_report_path": "",
        "gate_cycles": cycle,
        "cycles_remaining": max(0, t.max_gate_fix_retries - cycle + 1),
        "max_gate_fix_retries": t.max_gate_fix_retries,
        # simple
        "track_timeout": 1800,
        "track_on_failure": "workflow_failed",
        "commands_normalized": "",
        # final-gate
        "proposal_path": "",
        "tasks_path": "",
        "design_doc_paths": "",
        "report_paths": "",
        # tasks
        "tasks_preformatted": tasks_preformatted,
        "tasks_validation": tasks_validation,
    }

    # 加入 rollback_context（如果有）
    if state.current_sub_pipeline is not None:
        sp = state.current_sub_pipeline
        ctx["rollback_context"] = {
            "failed_at": "",
            "reason": f"{sp.kind} cycle {sp.cycle}",
            "source": sp.parent_phase,
        }

    return ctx


def build_action(
    state: PipelineState,
    action: PipelineAction,
    change_root: str,
) -> dict[str, Any]:
    """把 PipelineAction 转为标准 action JSON，并写 dispatch_file。"""
    track = action.track
    phase = action.phase
    cycle = action.cycle
    agent = PHASE_AGENTS.get(phase, action.agent)

    # 构建上下文
    ctx = build_ctx(state, track, phase, cycle)

    # 分配全局 seq
    dispatch_seq = _allocate_seq(change_root)
    report_seq = dispatch_seq + 1
    ds = _format_seq(dispatch_seq)
    rs = _format_seq(report_seq)
    ctx["dispatch_seq"] = ds
    ctx["report_seq"] = rs
    ctx["report_filename"] = f"{rs}-{track}-{phase}-report.md"
    ctx["fix_report_filename"] = f"{rs}-{track}-{phase}-fix-{cycle}.md"

    # 写 dispatch_file
    filepath = render_dispatch_file(
        change_root=change_root,
        track=track,
        phase=phase,
        ctx=ctx,
        cycle=cycle,
        dispatch_seq=ds,
    )

    result: dict[str, Any] = {
        "action": "dispatch",
        "item": track,
        "sub": phase,
        "agent": agent,
        "cycle": cycle,
        "dispatch_seq": ds,
        "report_seq": rs,
        "dispatch_file": filepath,
    }
    return result


def build_final_gate_action(
    state: PipelineState,
    change_root: str,
) -> dict[str, Any]:
    """构建 final-gate dispatch action。"""
    ctx = build_ctx(state, "final-gate", "gate")

    # 分配全局 seq
    dispatch_seq = _allocate_seq(change_root)
    report_seq = dispatch_seq + 1
    ds = _format_seq(dispatch_seq)
    rs = _format_seq(report_seq)
    ctx["dispatch_seq"] = ds
    ctx["report_seq"] = rs
    ctx["report_filename"] = f"{rs}-final-gate-gate-report.md"

    # 补充 final-gate 专有字段
    ctx["proposal_path"] = os.path.join(change_root, "proposal.md")
    ctx["tasks_path"] = os.path.join(change_root, "tasks.md")
    ctx["design_doc_paths"] = os.path.join(change_root, "design.md")

    # 扫描 2-build/ 下所有 gate assessment 报告
    build_dir = os.path.join(change_root, "2-build")
    report_paths: list[str] = []
    if os.path.isdir(build_dir):
        for fname in sorted(os.listdir(build_dir)):
            if fname.endswith("-gate-assessment.md") or fname.endswith("-gate-report.md"):
                report_paths.append(os.path.join(build_dir, fname))
    ctx["report_paths"] = "\n".join(report_paths)

    filepath = render_dispatch_file(
        change_root=change_root,
        track="final-gate",
        phase="gate",
        ctx=ctx,
        dispatch_seq=ds,
    )

    return {
        "action": "dispatch_final_gate",
        "item": "final-gate",
        "agent": FINAL_GATE_AGENT,
        "dispatch_seq": ds,
        "report_seq": rs,
        "dispatch_file": filepath,
    }