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

# v2.3: fix_routing 已废弃。所有 fix 完成后统一走 re_verify（→ fix → verify → fix → ...），
# 直到 track.max_fix_retries 用尽（耗尽点：fix_cycle_started 次数 == max_fix_retries）
# 或 verify 最终返回 completed 才进 gate。
# 注意：MAX_FIX_CYCLES 不再使用，改为读取 t.max_fix_retries。
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

def _error_action(state: PipelineState, reason: str) -> tuple[PipelineState, PipelineAction]:
    """返回 error action — 保留当前 state。

    关键约束：error path 不应清空 state，否则 orchestrator.record 后续副作用
    （save_snapshot, _auto_commit）会破坏持久层。
    返回 (state, error_action)，让 caller 决定如何处理 action。
    """
    return state, PipelineAction(
        kind="error", detail={"reason": reason}
    )


def _fail_action(state: PipelineState, track: str, phase: str, reason: str) -> tuple[PipelineState, PipelineAction]:
    """返回 workflow_failed action — 保留当前 state。

    Note: workflow_failed 是 terminal action（标记 status=failed）。
    caller (orchestrator._action_to_dict) 仍会 save_snapshot，但只是把
    failed 标记写入，不是破坏 tracks 内容。state 必须保留 tracks 内容以便排错。
    """
    return state, PipelineAction(
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
    phases = SUB_PHASES
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

    return _error_action(state, f"unknown phase: {phase!r}")


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
                    new_state, track, phase,
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

    return _error_action(new_state, f"track not found: {track}")


# ============================================================
# 子 reducer：verify
# ============================================================

def _handle_verify(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    track = record.track
    if track not in state.tracks:
        return _error_action(state, f"track not found: {track}")
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
                state,
                f"escalate requires tasks_updated with failed V-* IDs: {track}:{record.phase}"
            )
        verify = t.phases.get("verify", PhaseState())
        fix_cycles = len(verify.fix_cycles)
        # v2.3: limit 读 track.max_fix_retries（语义：verify→fix 循环总次数）
        max_fix_loops = t.max_fix_retries
        if fix_cycles >= max_fix_loops:
            # 耗尽 → 强制 gate（即使仍有未修复的 V-*）
            t = _update_phase(t, "verify", status="completed",
                              summary=f"fix cycles exhausted ({fix_cycles}/{max_fix_loops}), force gate")
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
            return _fail_action(state, track, "verify",
                                f"{track}:verify failed after {max_retries} attempts")
        t = _update_phase(t, "verify", status="pending", attempt=attempt)
        new_state = state.replace(tracks={**state.tracks, track: t})
        return new_state, _dispatch_action(track, "verify", attempt=attempt)

    return _error_action(state, f"invalid verify status: {record.status}")


# ============================================================
# 子 reducer：fix
# ============================================================

def _handle_fix(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    track = record.track
    if track not in state.tracks:
        return _error_action(state, f"track not found: {track}")

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

        # v2.3: fix 完成后统一 re_verify（→ 子 pipeline 推进到 verify）
        # 不再有"fix_routing"分支：fix 完成后总是进入 verify，让 verify 再次校验 V-*
        # 直到 verify.completed 或 max_fix_retries 耗尽。
        sp = state.current_sub_pipeline
        new_state = state.replace(
            tracks={**state.tracks, track: t},
        )
        return _sub_pipeline_advance(new_state, sp=sp)

    elif record.status == STATUS_FAILED:
        # v2.3: fix 失败 → 进入 verify（不再重试 fix）
        # 语义：fix agent 自身失败时不再 retry，让 verify 重新检查；如果 verify 通过，
        # 视为 fix cycle 完成但仍走 verify；如果 verify 再次 escalate，触发下一轮 fix。
        # 这样 max_fix_retries 真正成为 "verify→fix 循环总次数"。
        verify = t.phases.get("verify", PhaseState())
        fix_cycles = list(verify.fix_cycles)
        if fix_cycles:
            last = dict(fix_cycles[-1])
            last["status"] = "failed"
            fix_cycles[-1] = last
        verify = verify.replace(fix_cycles=tuple(fix_cycles))
        dict_phases = dict(t.phases)
        dict_phases["verify"] = verify
        t = t.replace(phases=dict_phases)
        sp = state.current_sub_pipeline
        new_state = state.replace(
            tracks={**state.tracks, track: t},
        )
        return _sub_pipeline_advance(new_state, sp=sp)

    # v2.3: fix 子 pipeline 不再拥有 attempt/max_retries 概念。
    # STATUS_FAILED 走同一个分支（见上），不再"重试 fix 自身"。
    # 兜底：处理未来可能出现的非 COMPLETED/FAILED status：
    return _error_action(state, f"invalid fix status: {record.status}")


# ============================================================
# 子 reducer：fix-gate
# ============================================================

def _handle_fix_gate(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    track = record.track
    if track not in state.tracks:
        return _error_action(state, f"track not found: {track}")

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
            return _fail_action(state, track, "fix-gate",
                                f"{track}:fix-gate failed after {max_retries} attempts")
        t = _update_phase(t, FIX_GATE_SUB, status="pending", attempt=attempt)
        new_state = state.replace(tracks={**state.tracks, track: t})
        return new_state, _dispatch_action(track, FIX_GATE_SUB, attempt=attempt)

    return _error_action(state, f"invalid fix-gate status: {record.status}")


# ============================================================
# 子 reducer：gate
# ============================================================

def _handle_gate(
    state: PipelineState, record: PipelineRecord,
) -> tuple[PipelineState, PipelineAction]:
    track = record.track
    if track not in state.tracks:
        return _error_action(state, f"track not found: {track}")
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

    return _error_action(state, f"invalid gate status: {record.status}")


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
        return _fail_action(
            new_state, FINAL_GATE_TRACK, FINAL_GATE_PHASE,
            new_state.failed_reason or "final-gate assessment failed",
        )

    return _error_action(state, f"invalid final-gate status: {record.status}")


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
    return _error_action(state, f"unexpected sub-pipeline phase: {record.phase}")


def _handle_sub_verify(
    state: PipelineState, record: PipelineRecord, sp: SubPipeline,
) -> tuple[PipelineState, PipelineAction]:
    """fix-cycle 或 gate-fix 子 pipeline 中的 verify。

    v2.3 行为变更：
    - STATUS_COMPLETED: 子 pipeline 完成，直接 dispatch gate（verify 通过 → 不再循环）
    - STATUS_ESCALATE: 子 pipeline 中的 verify 失败 → 回到 fix
    - STATUS_FAILED: 子 pipeline 失败
    """
    track = record.track
    t = state.tracks.get(track)

    if record.status == STATUS_COMPLETED:
        # 子 pipeline 中的 verify.completed → sub-pipeline 完成
        # fix-cycle: sub-pipeline 是 (fix, verify) → 完成 = 进 gate
        # gate-fix cycle: sub-pipeline 是 (fix-gate, verify, gate) → 完成 = 进入下一 phase（gate）
        if t is not None:
            t = _update_phase(t, "verify", status="completed",
                              summary=record.summary, report_path=record.report_path)
            state = state.replace(tracks={**state.tracks, track: t})
        # 让 _sub_pipeline_advance 决定下一步（fix-cycle → gate, gate-fix cycle → next phase）
        return _sub_pipeline_advance(state, sp=sp)

    elif record.status == STATUS_ESCALATE:
        # 子 pipeline 中的 verify 失败 → 回到 fix
        # v2.3: 这是"第二轮/第N轮"verify→fix 循环的入口，
        # 必须在 dispatch fix 之前先记录 fix_cycle，并检查是否超 max_fix_retries。
        if sp.current_index > 0:
            # 读 max_fix_retries（从 track 状态）
            t = state.tracks.get(track)
            max_fix = t.max_fix_retries if t else 5
            # 检查 limit：fix_cycles 数量应等于 fix dispatches 数
            current_cycles = (
                len(t.phases.get("verify", PhaseState()).fix_cycles)
                if t else 0
            )

            if current_cycles >= max_fix:
                # 已在 sub-pipeline 中再次 escalate，超出 max_fix_retries
                # → 强制进 gate，结束循环
                if t:
                    t = _update_phase(
                        t, "verify",
                        status="completed",
                        summary=f"fix cycles exhausted in sub-pipeline ({current_cycles}/{max_fix}), force gate",
                        report_path=record.report_path,
                    )
                new_state = state.replace(
                    tracks={**state.tracks, track: t} if t else state.tracks,
                    current_sub_pipeline=None,
                    current_track=track,
                    current_phase="gate",
                )
                gate_phase = (t.phases.get("gate", PhaseState()) if t else PhaseState())
                gate_attempt = gate_phase.attempt + 1
                return new_state, _dispatch_action(track, "gate", attempt=gate_attempt)

            # 未超 limit：追加 fix_cycle 记录，回到 fix
            if t:
                verify = t.phases.get("verify", PhaseState())
                verify = verify.replace(
                    fix_cycles=(*verify.fix_cycles, {
                        "cycle": current_cycles + 1,
                        "status": "pending",
                    }),
                )
                phases = dict(t.phases)
                phases["verify"] = verify
                t = t.replace(phases=phases)
                state = state.replace(tracks={**state.tracks, track: t})

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
        return _fail_action(state, track, "verify",
                            f"{track}:verify failed in sub-pipeline {sp.pipeline_id}")

    return _error_action(state, f"unexpected sub-verify status: {record.status}")


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

    return _error_action(state, f"unexpected sub-gate status: {record.status}")


def _sub_pipeline_advance(
    state: PipelineState, sp: SubPipeline | None,
) -> tuple[PipelineState, PipelineAction]:
    """子 pipeline 当前 phase 完成后，推进到下一 phase 或完成子 pipeline。"""
    if sp is None:
        return state, PipelineAction(kind="error", detail={"reason": "no active sub-pipeline"})

    if sp.is_last_phase:
        # 子 pipeline 当前已是最后一 phase（phase 本身完成后才会进入此分支）
        # 回到主 pipeline：dispatch 主 pipeline 的下一个 phase
        track = sp.parent_track
        parent_phase = sp.parent_phase

        if parent_phase == "verify":
            # v2.3: 子 pipeline 完成后（verify.completed 触发）→ dispatch gate
            # 不再 dispatch "另一个" verify：verify 已通过，循环结束。
            t = state.tracks.get(track)
            if t:
                t = _update_phase(t, "verify", status="completed",
                                  summary=state.tracks[track].phases["verify"].summary,
                                  report_path=state.tracks[track].phases["verify"].report_path)
                state = state.replace(tracks={**state.tracks, track: t})
            new_state = state.replace(
                current_sub_pipeline=None,
                current_track=track,
                current_phase="gate",
            )
            gate_phase = state.tracks[track].phases.get("gate", PhaseState())
            gate_attempt = gate_phase.attempt + 1
            return new_state, _dispatch_action(track, "gate", attempt=gate_attempt)

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
        idx = SUB_PHASES.index(current)
        if idx + 1 < len(SUB_PHASES):
            return SUB_PHASES[idx + 1]
        return None
    except ValueError:
        return None


def verify_attempt(state: PipelineState, track: str) -> int:
    """获取 verify phase 的当前 attempt 计数。"""
    t = state.tracks.get(track)
    if t is None:
        return 0
    return t.phases.get("verify", PhaseState()).attempt