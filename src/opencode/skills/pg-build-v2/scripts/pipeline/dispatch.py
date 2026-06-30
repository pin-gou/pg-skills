"""Dispatch — 构建 action JSON 与 dispatch_file。"""

from __future__ import annotations

import os
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


def build_ctx(
    state: PipelineState,
    track: str,
    phase: str,
    cycle: int = 1,
) -> dict[str, Any]:
    """构建 dispatch 上下文 dict。

    从 PipelineState 中提取 sub-agent 所需的配置字段。
    """
    t = state.tracks.get(track, TrackState.create(track))
    ph = t.phases.get(phase, PhaseState())

    ctx: dict[str, Any] = {
        "_change": state.change,
        "id": track,
        "bare": t.bare,
        "label": t.label or track,
        "modules": list(t.modules),
        "module_roots": [],
        "module_details": [],
        "review_level": "",
        "max_fix_retries": 5,
        "fix_routing": "source",
        # stage
        "stage_name": track.rsplit(".", 1)[0] if "." in track else "dev",
        "test_key": "unit",
        "gate": "all_pass",
        "env_required": True,
        "env_name": "dev-local",
        "prepare_status": "ok",
        "prepare_log_path": "",
        "test_commands": "",
        "env_instances": {},
        # phase
        "phase": phase,
        "cycle": cycle,
        "attempt": ph.attempt or 1,
        # verify / gate
        "report_filename": f"{track}-{phase}-report.md",
        "report_seq": 1,
        # fix
        "fix_cycle": cycle,
        "verify_report_path": "",
        "fix_report_filename": f"{track}-{phase}-fix-{cycle}.md",
        # fix-gate
        "gate_report_path": "",
        "gate_cycles": cycle,
        "cycles_remaining": 2 - cycle,
        "max_gate_fix_retries": 2,
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
        "tasks_preformatted": "",
        "tasks_validation": "",
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

    # 写 dispatch_file
    filepath = render_dispatch_file(
        change_root=change_root,
        track=track,
        phase=phase,
        ctx=ctx,
        cycle=cycle,
    )

    result: dict[str, Any] = {
        "action": "dispatch",
        "item": track,
        "sub": phase,
        "agent": agent,
        "cycle": cycle,
        "dispatch_file": filepath,
    }
    return result


def build_final_gate_action(
    state: PipelineState,
    change_root: str,
) -> dict[str, Any]:
    """构建 final-gate dispatch action。"""
    ctx = build_ctx(state, "final-gate", "gate")

    # 补充 final-gate 专有字段
    ctx["proposal_path"] = os.path.join(change_root, "proposal.md")
    ctx["tasks_path"] = os.path.join(change_root, "tasks.md")
    ctx["design_doc_paths"] = os.path.join(change_root, "design.md")
    ctx["report_paths"] = ""

    filepath = render_dispatch_file(
        change_root=change_root,
        track="final-gate",
        phase="gate",
        ctx=ctx,
    )

    return {
        "action": "dispatch_final_gate",
        "item": "final-gate",
        "agent": FINAL_GATE_AGENT,
        "dispatch_file": filepath,
    }