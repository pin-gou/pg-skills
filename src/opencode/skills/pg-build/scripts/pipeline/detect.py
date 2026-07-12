"""Detect — 纯函数 next_pending。

根据当前 PipelineState 决定下一个要 dispatch 的 action。
不依赖外部配置（'pipeline_order' 在 state 中已固化）。
"""

from __future__ import annotations

from pipeline.state import (
    PipelineState,
    SUB_PHASES,
    FIX_SUB,
    FIX_GATE_SUB,
    SIMPLE_SUB,
)
from pipeline.events import (
    FINAL_GATE_TRACK,
    PipelineAction,
)


def next_pending(state: PipelineState) -> PipelineAction:
    """决定下一个 dispatch。

    纯函数：输入 state → 输出 PipelineAction（kind=dispatch 或 terminal）。

    规则：
      1. pipeline 已完成或已失败 → terminal
      2. 有活跃子 pipeline → 走子 pipeline
      3. 走 pipeline_order，按 SUB_PHASES 顺序推进
      4. 所有 track 完成 → final-gate
    """
    # Terminal
    if state.status == "completed":
        return PipelineAction(kind="done", detail={"status": "completed"})
    if state.status == "failed":
        return PipelineAction(
            kind="workflow_failed",
            detail={"reason": state.failed_reason or "unknown"},
        )

    # 子 pipeline 活跃 → dispatch 子 pipeline 的当前 phase
    if state.current_sub_pipeline is not None:
        sp = state.current_sub_pipeline
        return PipelineAction(
            kind="dispatch",
            track=sp.parent_track,
            phase=sp.current_phase,
            cycle=sp.cycle,
            agent=sp.current_phase,
        )

    # 首次初始化尚未设置 pipeline_order
    if not state.pipeline_order:
        return PipelineAction(kind="bootstrap")

    # 走 pipeline_order
    # 先找第一个未完成的 track，确定它所属的 stage
    first_pending_track = None
    for track_id in state.pipeline_order:
        if state.is_track_completed(track_id):
            continue
        first_pending_track = track_id
        break

    if first_pending_track is None:
        # 所有 track 完成 → final-gate
        return PipelineAction(
            kind="dispatch",
            track=FINAL_GATE_TRACK,
            phase="gate",
            agent="pg-build/gate",
        )

    # 如果 stage_order 为空（向后兼容：无 stage 元数据的旧 state），跳过 stage 边界检测
    # final-gate 是特殊 track，不属于任何 stage，不做 stage 边界检测
    if state.stage_order and first_pending_track is not None and first_pending_track != FINAL_GATE_TRACK:
        next_stage = PipelineState.extract_stage(first_pending_track)

        # 检测 stage 边界：需要 clean 当前 stage 的环境
        if state.current_stage and state.current_stage != next_stage:
            if state.current_stage in state.stage_prepared:
                _env_name = state.stage_env_map.get(state.current_stage, "")
                return PipelineAction(
                    kind="env_switch",
                    track=first_pending_track,
                    phase="clean_env",
                    detail={
                        "stage": state.current_stage,
                        "env_name": _env_name,
                        "hook_timeout_seconds": state.stage_env_timeout.get(_env_name, 600),
                        "next_stage": next_stage,
                        "next_env_name": state.stage_env_map.get(next_stage, ""),
                    },
                )

        # 检测 stage 边界：需要 prepare 新 stage 的环境
        if next_stage and next_stage not in state.stage_prepared:
            _env_name = state.stage_env_map.get(next_stage, "")
            return PipelineAction(
                kind="env_switch",
                track=first_pending_track,
                phase="prepare_env",
                detail={
                    "stage": next_stage,
                    "env_name": _env_name,
                    "hook_timeout_seconds": state.stage_env_timeout.get(_env_name, 600),
                },
            )

    # 正常 track 内的 dispatch 逻辑
    for track_id in state.pipeline_order:
        if state.is_track_completed(track_id):
            continue

        track = state.tracks.get(track_id)
        if track is None:
            continue

        # Simple track 直接路由到 "simple" phase（跳过 TDVG）
        if state.track_types.get(track_id) == "simple":
            return PipelineAction(
                kind="dispatch",
                track=track_id,
                phase="simple",
                cycle=1,
            )

        # 确定当前 phase
        current_phase = state.current_phase if state.current_track == track_id else ""
        if current_phase:
            idx = _phase_index(current_phase)
            if idx >= 0:
                # 从下一 phase 开始找
                for i in range(idx, len(SUB_PHASES)):
                    phase = SUB_PHASES[i]
                    # v3.x: review 派生 disabled → 视为 completed 跳过
                    # v3.4: verify / gate 同样按 *_enabled 派生跳过
                    if phase == "review" and not track.code_review_enabled:
                        continue
                    if phase == "verify" and not track.verify_enabled:
                        continue
                    if phase == "gate" and not track.gate_enabled:
                        continue
                    ph = track.phases.get(phase)
                    if ph is not None and ph.status == "completed":
                        continue
                    if ph is None or ph.status in ("pending", ""):
                        return PipelineAction(
                            kind="dispatch",
                            track=track_id,
                            phase=phase,
                            cycle=1,
                        )
                    if ph.status == "running":
                        return PipelineAction(
                            kind="dispatch",
                            track=track_id,
                            phase=phase,
                            cycle=1,
                            attempt=ph.attempt,
                        )

                # 所有 phase 已完成 → 跳到下一 track
                continue

        # 全新 track → 从 test 开始
        return PipelineAction(
            kind="dispatch",
            track=track_id,
            phase=SUB_PHASES[0],
            cycle=1,
        )

    # 所有 track 完成 → final-gate
    return PipelineAction(
        kind="dispatch",
        track=FINAL_GATE_TRACK,
        phase="gate",
        agent="pg-build/gate",
    )


def _phase_index(phase: str) -> int:
    try:
        return SUB_PHASES.index(phase)
    except ValueError:
        return -1