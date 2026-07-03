"""Reducer — 纯函数 reduce_state。

输入：(state, event) → 输出：(new_state, action)

所有状态转换逻辑集中在此文件中。reducer 是纯函数：
  - 无 I/O（不读写文件）
  - 无副作用
  - 输入不可变，输出新对象
"""

from __future__ import annotations

from typing import Any

from pipeline.state import (
    PhaseState,
    PipelineState,
    TrackState,
    SUB_PHASES,
    SUB_PHASES_WITH_FIX,
    FIX_SUB,
    FIX_GATE_SUB,
    SIMPLE_SUB,
)
from pipeline.events import (
    FINAL_GATE_TRACK,
    FINAL_GATE_PHASE,
    PipelineRecord,
    PipelineAction,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_ESCALATE,
    STATUS_PASS,
    STATUS_FAIL,
    DEFAULT_FIX_ROUTING,  # v2.2
    FIX_ROUTING_RE_VERIFY,  # v2.2
    EVT_FIX_SKIPPED_VERIFY,  # v2.2
)
from pipeline.sub_pipeline import (
    SubPipeline,
    create_fix_cycle,
    create_gate_fix_cycle,
    FIX_CYCLE_PHASES,
    GATE_FIX_CYCLE_PHASES,
)


# ============================================================
# 常量
# ============================================================

MAX_FIX_CYCLES = 4  # fix 循环最大次数（verify escalate 几次后强制 gate）
# 各 track 级重试限制从 TrackState 读取：
#   max_fail_retries / max_fix_retries / max_gate_fix_retries
# 默认值（TrackState 创建时使用）：
#   max_fail_retries = 3
#   max_fix_retries = 5
#   max_gate_fix_retries = 2


def _now_iso() -> str:
    """v2.1: 当前时间 ISO 格式字符串 — 给 accepted_gaps 打时间戳用。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _make_gap_entry(
    track: str, phase: str, cycles: int, max_cycles: int,
    issues: str,
) -> dict[str, Any]:
    """v2.1: 构造 accepted_gaps 条目。

    协议：fix 循环或 gate-fix 循环耗尽时，由 reducer 写入此条目到 track.accepted_gaps。
    orchestrator 在 record 后检测 accepted_gaps 增量并写 EVT_GAP_ACCEPTED 事件。
    """
    return {
        "track": track,
        "phase": phase,
        "cycles_attempted": cycles,
        "max_cycles": max_cycles,
        "issues": issues[:500] if issues else "",
        "accepted_at": _now_iso(),
    }

# Sub-agent 映射
PHASE_AGENTS: dict[str, str] = {
    "test":     "pg-build/test",
    "dev":      "pg-build/dev",
    "verify":   "pg-build/verify",
    "gate":     "pg-build/gate",
    "fix":      "pg-build/fix",
    "fix-gate": "pg-build/fix-gate",
    "simple":   "pg-build/simple",
}


# ============================================================
# 工具函数
# ============================================================

def _error_action(reason: str) -> tuple[PipelineState, PipelineAction]:
    """返回 error action（无效的状态转换）。"""
    return PipelineState(), PipelineAction(
        kind="error", detail={"reason": reason}
    )


def _fail_action(track: str, phase: str, reason: str) -> tuple[PipelineState, PipelineAction]:
    """返回 workflow_failed action。"""
    return PipelineState(), PipelineAction(
        kind="workflow_failed",
        track=track,
        phase=phase,
        detail={"reason": reason},
    )


def _dispatch_action(
    track: str, phase: str, cycle: int = 1, attempt: int = 1,
) -> PipelineAction:
    """构建 dispatch action。"""
    return PipelineAction(
        kind="dispatch",
        track=track,
        phase=phase,
        cycle=cycle,
        attempt=attempt,
        agent=PHASE_AGENTS.get(phase, ""),
    )


def _track_phase_index(state: PipelineState, track: str, phase: str) -> int:
    """返回 phase 在 SUB_PHASES 中的下标。"""
    phases = SUB_PHASES_WITH_FIX
    try:
        return phases.index(phase)
    except ValueError:
        return -1


def _update_phase(
    track: TrackState, phase: str,
    status: str = "",
    attempt: int = 0,
    summary: str = "",
    report_path: str | None = None,
) -> TrackState:
    """更新 track 的某个 phase 状态。返回新 TrackState。"""
    old_phases = track.phases
    old = old_phases.get(phase, PhaseState())
    new_phase = old.replace(
        status=status or old.status,
        attempt=attempt or old.attempt,
        summary=summary or old.summary,
        report_path=report_path or old.report_path,
    )
    new_phases = dict(old_phases)
    new_phases[phase] = new_phase
    return track.replace(phases=new_phases)


# ============================================================
# Reducer 主入口
# ============================================================

def reduce_state(
    state: PipelineState,
    event: PipelineRecord | dict[str, Any],
) -> tuple[PipelineState, PipelineAction]:
    """Reducer 纯函数。

    Args:
        state: 当前 pipeline 状态
        event: 可以是 PipelineRecord（从 record() 调用）或 dict（从 event log 回放）

    Returns:
        (new_state, action) — 均不可变。action 为下一步要执行的动作。
    """
    # 统一 event 格式（支持 dict 回放）
    if isinstance(event, dict):
        if event.get("type") != "record_received":
            # 非 record 事件（pipeline_started 等）→ 状态不变
            return state, PipelineAction(kind="noop")
        data = event.get("data", {})
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
    else:
        record = event

    track = record.track
    phase = record.phase
    status = record.status

    # ===== 主 match 块 =====
    # 按 (phase, status) 分组，每个 case 返回 (new_state, action)

    # ─── 子 pipeline 路径 ───
    # 子 pipeline 与主 pipeline 共享 match 逻辑，
    # 但 sub_pipeline_advance 负责把子 pipeline 的结果映射回主 pipeline
    if state.current_sub_pipeline is not None:
        sp = state.current_sub_pipeline
        if track == sp.parent_track and phase == sp.current_phase:
            return _handle_sub_pipeline_record(state, record, sp)

    # ─── test / dev / simple ───
    # 这三类 phase 只有 completed / failed 两种状态
    if phase in ("test", "dev", "simple"):
        return _handle_linear_phase(state, record)

    # ─── verify ───
    if phase == "verify":
        return _handle_verify(state, record)

    # ─── fix (子 pipeline 中的 fix phase) ───
    if phase == "fix":
        return _handle_fix(state, record)

    # ─── fix-gate (子 pipeline 中的 fix-gate phase) ───
    if phase == "fix-gate":
        return _handle_fix_gate(state, record)

    # ─── final-gate 优先于 gate ───
    # final-gate 的 phase 也是 "gate"，但 track 是 "final-gate"
    if track == FINAL_GATE_TRACK:
        return _handle_final_gate(state, record)

    # ─── gate ───
    if phase == "gate":
        return _handle_gate(state, record)

    # ─── final-gate ───
    if track == FINAL_GATE_TRACK or phase == FINAL_GATE_PHASE:
        return _handle_final_gate(state, record)

    return _error_action(f"unknown phase: {phase!r}")


# ============================================================
# 子 reducer：线性 phase（test / dev / simple）
# ============================================================

def _handle_linear_phase(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    track = record.track
    phase = record.phase

    new_state = state
    if track in new_state.tracks:
        t = new_state.tracks[track]

        if record.status == STATUS_COMPLETED:
            t = _update_phase(t, phase, status="completed", attempt=record.attempt,
                              summary=record.summary, report_path=record.report_path)

            # simple track 到此结束
            if phase == SIMPLE_SUB:
                t = t.replace(status="completed")
                new_state = new_state.replace(
                    tracks={**new_state.tracks, track: t}
                )
                return new_state, PipelineAction(kind="advance", track=track)

            # test / dev → 下一个 phase
            next_phase = _next_phase(phase)
            if next_phase is None:
                return new_state, PipelineAction(kind="advance", track=track)
            new_state = new_state.replace(
                tracks={**new_state.tracks, track: t},
                current_track=track,
                current_phase=next_phase,
            )
            return new_state, _dispatch_action(track, next_phase)

        elif record.status == STATUS_FAILED:
            old = t.phases.get(phase, PhaseState())
            attempt = old.attempt + 1
            max_retries = t.max_fail_retries
            if attempt > max_retries:
                return _fail_action(
                    track, phase,
                    f"{track}:{phase} failed after {max_retries} attempts",
                )
            t = _update_phase(t, phase, status="pending", attempt=attempt,
                              summary=record.summary)
            new_state = new_state.replace(
                tracks={**new_state.tracks, track: t},
                current_track=track,
                current_phase=phase,
            )
            return new_state, _dispatch_action(track, phase, attempt=attempt)

    return _error_action(f"track not found: {track}")


# ============================================================
# 子 reducer：verify
# ============================================================

def _handle_verify(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    track = record.track
    if track not in state.tracks:
        return _error_action(f"track not found: {track}")
    t = state.tracks[track]

    if record.status == STATUS_COMPLETED:
        # PROCEED → 进入 gate
        t = _update_phase(t, "verify", status="completed",
                          summary=record.summary, report_path=record.report_path)
        gate_phase = t.phases.get("gate", PhaseState())
        gate_attempt = gate_phase.attempt + 1
        new_state = state.replace(
            tracks={**state.tracks, track: t},
            current_track=track,
            current_phase="gate",
        )
        return new_state, _dispatch_action(track, "gate", attempt=gate_attempt)

    elif record.status == STATUS_ESCALATE:
        # ESCALATE → fix 循环（或强制 gate）
        # v2.2: escalate 必须有 tasks_updated
        if not record.tasks_updated:
            return _error_action(
                f"escalate requires tasks_updated with failed V-* IDs: {track}:{record.phase}"
            )
        verify = t.phases.get("verify", PhaseState())
        fix_cycles = len(verify.fix_cycles)
        if fix_cycles >= MAX_FIX_CYCLES:
            # 耗尽 → 强制 gate
            t = _update_phase(t, "verify", status="completed",
                              summary="fix cycles exhausted, force gate")
            new_state = state.replace(
                tracks={**state.tracks, track: t},
                current_track=track,
                current_phase="gate",
            )
            return new_state, _dispatch_action(track, "gate", cycle=1)

        # 创建 fix 子 pipeline
        sp = create_fix_cycle(track, fix_cycles + 1)
        # 记录 fix_cycle 信息到 verify phase
        verify = verify.replace(
            fix_cycles=(*verify.fix_cycles, {
                "cycle": fix_cycles + 1,
                "status": "pending",
            }),
        )
        phases = dict(t.phases)
        phases["verify"] = verify
        t = t.replace(phases=phases)
        new_state = state.replace(
            tracks={**state.tracks, track: t},
            current_sub_pipeline=sp,
            current_track=track,
            current_phase=sp.current_phase,
        )
        return new_state, _dispatch_action(track, sp.current_phase, cycle=sp.cycle)

    elif record.status == STATUS_FAILED:
        attempt = verify_attempt(state, track) + 1
        max_retries = t.max_fail_retries if track in state.tracks else 3
        if attempt > max_retries:
            return _fail_action(track, "verify",
                                f"{track}:verify failed after {max_retries} attempts")
        t = _update_phase(t, "verify", status="pending", attempt=attempt)
        new_state = state.replace(tracks={**state.tracks, track: t})
        return new_state, _dispatch_action(track, "verify", attempt=attempt)

    return _error_action(f"invalid verify status: {record.status}")


# ============================================================
# 子 reducer：fix
# ============================================================

def _handle_fix(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    track = record.track
    if track not in state.tracks:
        return _error_action(f"track not found: {track}")

    # [v2.1 修复] 提前获取 t — 修复 UnboundLocalError：
    # 之前只在 STATUS_COMPLETED 分支里给 t 赋值，STATUS_FAILED 分支直接使用 t 导致崩溃
    t = state.tracks[track]

    if record.status == STATUS_COMPLETED:
        verify = t.phases.get("verify", PhaseState())
        # 标记 fix_cycle 完成
        fix_cycles = list(verify.fix_cycles)
        if fix_cycles:
            last = dict(fix_cycles[-1])
            last["status"] = "completed"
            fix_cycles[-1] = last
        verify = verify.replace(fix_cycles=tuple(fix_cycles))
        dict_phases = dict(t.phases)
        dict_phases["verify"] = verify
        t = t.replace(phases=dict_phases)

        # v2.2: 检查 fix_routing
        sp = state.current_sub_pipeline
        fix_routing = t.fix_routing or DEFAULT_FIX_ROUTING

        if fix_routing == DEFAULT_FIX_ROUTING:
            # direct_to_gate: fix 完成后跳过子 pipeline 的 verify，直接进 gate
            new_state = state.replace(
                tracks={**state.tracks, track: t},
                current_sub_pipeline=None,
                current_track=track,
                current_phase="gate",
            )
            # 说明: gate 的 cycle 设为 1（不累加 fix 的 cycle）
            return new_state, _dispatch_action(track, "gate", cycle=1)
        else:
            # re_verify: 推进子 pipeline 到 verify phase
            new_state = state.replace(
                tracks={**state.tracks, track: t},
            )
            return _sub_pipeline_advance(new_state, sp=sp)

    elif record.status == STATUS_FAILED:
        # fix 失败 → 重试
        attempt = (t.phases.get(FIX_SUB, PhaseState()).attempt or 0) + 1
        max_retries = t.max_fix_retries if track in state.tracks else 5
        if attempt > max_retries:
            # [v2.1 修复 + accept_gap 协议] fix 循环耗尽 → 接受 gap，track 标记 completed
            gap = _make_gap_entry(
                track=track, phase="fix",
                cycles=attempt, max_cycles=max_retries,
                issues=record.summary or record.issues or "",
            )
            t = _update_phase(t, FIX_SUB, status="completed",
                              summary=f"fix exhausted after {max_retries} cycles, gap accepted")
            t = t.replace(status="completed", accepted_gaps=(*t.accepted_gaps, gap))
            new_state = state.replace(
                tracks={**state.tracks, track: t},
                current_sub_pipeline=None,
                current_track="",
                current_phase="",
            )
            return new_state, PipelineAction(kind="advance", track=track)
        t = _update_phase(t, FIX_SUB, status="pending", attempt=attempt)
        new_state = state.replace(tracks={**state.tracks, track: t})
        return new_state, _dispatch_action(track, FIX_SUB, attempt=attempt)

    return _error_action(f"invalid fix status: {record.status}")


# ============================================================
# 子 reducer：fix-gate
# ============================================================

def _handle_fix_gate(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    track = record.track
    if track not in state.tracks:
        return _error_action(f"track not found: {track}")

    # [v2.1 修复] 提前获取 t — 修复 STATUS_FAILED 分支 UnboundLocalError
    t = state.tracks[track]

    if record.status == STATUS_COMPLETED:
        gate = t.phases.get("gate", PhaseState())
        fix_gates = list(gate.fix_gates)
        if fix_gates:
            last = dict(fix_gates[-1])
            last["status"] = "completed"
            fix_gates[-1] = last
        gate = gate.replace(fix_gates=tuple(fix_gates))
        dict_phases = dict(t.phases)
        dict_phases["gate"] = gate
        t = t.replace(phases=dict_phases)

        new_state = state.replace(tracks={**state.tracks, track: t})
        return _sub_pipeline_advance(new_state, sp=state.current_sub_pipeline)

    elif record.status == STATUS_FAILED:
        attempt = (t.phases.get(FIX_GATE_SUB, PhaseState()).attempt or 0) + 1
        max_retries = t.max_fix_retries if track in state.tracks else 5
        if attempt > max_retries:
            return _fail_action(track, "fix-gate",
                                f"{track}:fix-gate failed after {max_retries} attempts")
        t = _update_phase(t, FIX_GATE_SUB, status="pending", attempt=attempt)
        new_state = state.replace(tracks={**state.tracks, track: t})
        return new_state, _dispatch_action(track, FIX_GATE_SUB, attempt=attempt)

    return _error_action(f"invalid fix-gate status: {record.status}")


# ============================================================
# 子 reducer：gate
# ============================================================

def _handle_gate(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    track = record.track
    if track not in state.tracks:
        return _error_action(f"track not found: {track}")
    t = state.tracks[track]

    if record.status == STATUS_PASS:
        # gate pass → track completed
        t = _update_phase(t, "gate", status="pass",
                          summary=record.summary, report_path=record.report_path)
        t = t.replace(status="completed")
        gate = t.phases.get("gate", PhaseState())
        gate = gate.replace(
            gate_cycles=(*gate.gate_cycles, {
                "cycle": len(gate.gate_cycles) + 1,
                "status": "pass",
            }),
        )
        dict_phases = dict(t.phases)
        dict_phases["gate"] = gate
        t = t.replace(phases=dict_phases)
        new_state = state.replace(
            tracks={**state.tracks, track: t},
            current_track="",
            current_phase="",
        )
        return new_state, PipelineAction(kind="advance", track=track)

    elif record.status == STATUS_FAIL:
        # gate fail → gate-fix 子 pipeline 或耗尽
        gate = t.phases.get("gate", PhaseState())
        gate_cycles = len(gate.gate_cycles)
        max_gate = t.max_gate_fix_retries

        if gate_cycles >= max_gate:
            # [v2.1 accept_gap 协议] 耗尽 → 接受 gap 到 track.accepted_gaps，track 完成
            gap = _make_gap_entry(
                track=track, phase="gate",
                cycles=gate_cycles, max_cycles=max_gate,
                issues=record.summary or record.issues or "",
            )
            t = _update_phase(t, "gate", status="pass",
                              summary=f"gate-fix exhausted after {max_gate} cycles, gap accepted")
            t = t.replace(status="completed", accepted_gaps=(*t.accepted_gaps, gap))
            new_state = state.replace(
                tracks={**state.tracks, track: t},
                current_track="",
                current_phase="",
            )
            return new_state, PipelineAction(kind="advance", track=track)

        # 创建 gate-fix 子 pipeline
        sp = create_gate_fix_cycle(track, gate_cycles + 1)
        gate = gate.replace(
            gate_cycles=(*gate.gate_cycles, {
                "cycle": gate_cycles + 1,
                "status": "fail",
            }),
        )
        dict_phases = dict(t.phases)
        dict_phases["gate"] = gate
        t = t.replace(phases=dict_phases)
        new_state = state.replace(
            tracks={**state.tracks, track: t},
            current_sub_pipeline=sp,
            current_track=track,
            current_phase=sp.current_phase,
        )
        return new_state, _dispatch_action(track, sp.current_phase, cycle=sp.cycle)

    return _error_action(f"invalid gate status: {record.status}")


# ============================================================
# 子 reducer：final-gate
# ============================================================

def _handle_final_gate(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    if record.status in (STATUS_PASS, STATUS_COMPLETED):
        new_state = state.replace(
            status="completed",
            current_track="",
            current_phase="",
        )
        return new_state, PipelineAction(kind="done", detail={"status": "completed"})

    elif record.status == STATUS_FAIL:
        new_state = state.replace(
            status="failed",
            failed_reason=record.summary or "final-gate assessment failed",
        )
        return _fail_action(FINAL_GATE_TRACK, FINAL_GATE_PHASE, new_state.failed_reason)

    return _error_action(f"invalid final-gate status: {record.status}")


# ============================================================
# 子 pipeline 管理
# ============================================================

def _handle_sub_pipeline_record(
    state: PipelineState, record: PipelineRecord, sp: SubPipeline,
) -> tuple[PipelineState, PipelineAction]:
    """处理子 pipeline 范围内的 record。"""
    # 把子 pipeline 的 phase 路由到对应的父 pipeline handler
    if record.phase == "fix":
        return _handle_fix(state, record)
    elif record.phase == "fix-gate":
        return _handle_fix_gate(state, record)
    elif record.phase == "verify":
        # gate-fix 子 pipeline 中的 verify
        return _handle_sub_verify(state, record, sp)
    elif record.phase == "gate":
        # gate-fix 子 pipeline 中的 gate
        return _handle_sub_gate(state, record, sp)
    return _error_action(f"unexpected sub-pipeline phase: {record.phase}")


def _handle_sub_verify(
    state: PipelineState, record: PipelineRecord, sp: SubPipeline,
) -> tuple[PipelineState, PipelineAction]:
    """gate-fix 子 pipeline 中的 verify（必须 PROCEED 才能继续）。"""
    track = record.track
    t = state.tracks.get(track)

    if record.status == STATUS_COMPLETED:
        if t is not None:
            t = _update_phase(t, "verify", status="completed",
                              summary=record.summary, report_path=record.report_path)
            state = state.replace(tracks={**state.tracks, track: t})
        return _sub_pipeline_advance(state, sp=sp)

    elif record.status == STATUS_ESCALATE:
        # 子 pipeline 中的 verify 失败 → 回到 fix
        if sp.current_index > 0:
            sp = SubPipeline(
                pipeline_id=sp.pipeline_id,
                parent_track=sp.parent_track,
                parent_phase=sp.parent_phase,
                cycle=sp.cycle,
                kind=sp.kind,
                phases=sp.phases,
                current_index=sp.current_index - 1,
                status="running",
            )
            state = state.replace(current_sub_pipeline=sp)
            return state, _dispatch_action(track, "fix", cycle=sp.cycle)

    elif record.status == STATUS_FAILED:
        return _fail_action(track, "verify",
                            f"{track}:verify failed in sub-pipeline {sp.pipeline_id}")

    return _error_action(f"unexpected sub-verify status: {record.status}")


def _handle_sub_gate(
    state: PipelineState, record: PipelineRecord, sp: SubPipeline,
) -> tuple[PipelineState, PipelineAction]:
    """gate-fix 子 pipeline 中的 gate（子 pipeline 最后一 phase）。"""
    track = record.track
    t = state.tracks.get(track)

    if record.status == STATUS_PASS:
        # 子 pipeline 中的 gate pass → 标记主 pipeline gate 为 pass，track 完成
        if t is not None:
            t = _update_phase(t, "gate", status="pass",
                              summary=record.summary, report_path=record.report_path)
            t = t.replace(status="completed")
            state = state.replace(
                tracks={**state.tracks, track: t},
                current_sub_pipeline=None,
                current_track="",
                current_phase="",
            )
        return state, PipelineAction(kind="advance", track=track)

    elif record.status == STATUS_FAIL:
        # 子 pipeline 中的 gate 仍然 fail → 再试 gate-fix
        return _handle_gate(state, record)

    return _error_action(f"unexpected sub-gate status: {record.status}")


def _sub_pipeline_advance(
    state: PipelineState, sp: SubPipeline | None,
) -> tuple[PipelineState, PipelineAction]:
    """子 pipeline 当前 phase 完成后，推进到下一 phase 或完成子 pipeline。"""
    if sp is None:
        return state, PipelineAction(kind="error", detail={"reason": "no active sub-pipeline"})

    if sp.is_last_phase:
        # 子 pipeline 完成 → 回到主 pipeline
        track = sp.parent_track
        parent_phase = sp.parent_phase

        if parent_phase == "verify":
            # v2.2: fix_routing 控制 fix 完成后的流向
            t = state.tracks.get(track)
            verify = t.phases.get("verify", PhaseState()) if t else PhaseState()
            fix_routing = t.fix_routing if t else ""
            fix_routing = fix_routing or DEFAULT_FIX_ROUTING

            if fix_routing == DEFAULT_FIX_ROUTING:
                new_state = state.replace(
                    current_sub_pipeline=None,
                    current_track=track,
                    current_phase="gate",
                )
                return new_state, _dispatch_action(track, "gate", cycle=1)
            else:
                next_cycle = len(verify.fix_cycles) + 1 if verify.fix_cycles else 1
                new_state = state.replace(
                    current_sub_pipeline=None,
                    current_track=track,
                    current_phase="verify",
                )
                return new_state, _dispatch_action(track, "verify", cycle=next_cycle)

        elif parent_phase == "gate":
            # gate-fix 子 pipeline 完成 → 回到 gate（dispatch 下一个 gate cycle）
            new_state = state.replace(
                current_sub_pipeline=None,
                current_track=track,
                current_phase="gate",
            )
            return new_state, _dispatch_action(track, "gate")

        return state, PipelineAction(kind="advance", track=track)

    # 子 pipeline 推进到下一 phase
    next_sp = sp.advance()
    new_state = state.replace(
        current_sub_pipeline=next_sp,
        current_track=next_sp.parent_track,
        current_phase=next_sp.current_phase,
    )
    return new_state, _dispatch_action(
        next_sp.parent_track, next_sp.current_phase, cycle=next_sp.cycle,
    )


# ============================================================
# 辅助函数
# ============================================================

def _next_phase(current: str) -> str | None:
    """返回 SUB_PHASES 中的下一 phase。"""
    try:
        idx = SUB_PHASES_WITH_FIX.index(current)
        if idx + 1 < len(SUB_PHASES_WITH_FIX):
            return SUB_PHASES_WITH_FIX[idx + 1]
        return None
    except ValueError:
        return None


def verify_attempt(state: PipelineState, track: str) -> int:
    """获取 verify phase 的当前 attempt 计数。"""
    t = state.tracks.get(track)
    if t is None:
        return 0
    return t.phases.get("verify", PhaseState()).attempt