"""Reducer 纯函数测试。

cover 所有 (phase, status) match 分支，包括：
- 线性 phase：test / dev / simple
- verify：completed / escalate / failed
- fix：completed / failed
- fix-gate：completed / failed
- gate：pass / fail（含耗尽）
- final-gate：completed / fail
- 子 pipeline 推进
- 无效转换（default → error_action）
"""

from __future__ import annotations

import dataclasses
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.events import PipelineRecord, PipelineAction, FINAL_GATE_TRACK
from pipeline.reducer import (
    reduce_state,
    _handle_linear_phase,
    _handle_final_gate,
    _handle_fix,
    _handle_fix_gate,
    _handle_gate,
    _handle_sub_pipeline_record,
    _handle_sub_verify,
    _handle_sub_gate,
    _handle_verify,
    _handle_code_view,
    _handle_fix_code_view,
    _handle_sub_code_view,
    _sub_pipeline_advance,
    PHASE_AGENTS,
)
from pipeline.state import (
    PipelineState,
    TrackState,
    PhaseState,
    SUB_PHASES,
    FIX_SUB,
    FIX_GATE_SUB,
    SIMPLE_SUB,
)
from pipeline.sub_pipeline import (
    SubPipeline,
    create_fix_cycle,
    create_gate_fix_cycle,
    create_code_view_cycle,
    CODE_VIEW_CYCLE,
    CODE_VIEW_CYCLE_PHASES,
)


def _make_track(track_id: str, status: str = "pending") -> TrackState:
    # v2.3: fix_routing 已废弃，默认行为就是 fix→verify
    return TrackState.create(track_id, status=status)


def _make_phase_state(status: str = "pending", attempt: int = 0) -> PhaseState:
    return PhaseState(status=status, attempt=attempt)


class TestReduceStateBasic(unittest.TestCase):
    """reducer 整体入口测试。"""

    def test_reduce_with_dict_event(self):
        """从 event log 回放时，支持 dict 格式的 event。"""
        state = PipelineState(change="x")
        event = {"type": "pipeline_started", "data": {"change": "x"}}
        new_state, action = reduce_state(state, event)
        self.assertEqual(action.kind, "noop")

    def test_reduce_with_pipeline_record(self):
        """正常 PipelineRecord 输入。"""
        state = PipelineState(
            change="x",
            pipeline_order=("dev.backend",),
            tracks={"dev.backend": _make_track("dev.backend")},
        )
        record = PipelineRecord(
            track="dev.backend", phase="test", status="completed"
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "dev")


class TestLinearPhase(unittest.TestCase):
    """test / dev / simple 的 reducer 行为。"""

    def setUp(self):
        self.track = _make_track("dev.backend")

    def test_test_completed_advances_to_dev(self):
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": self.track},
        )
        record = PipelineRecord(track="dev.backend", phase="test", status="completed")
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "dev")

    def test_dev_completed_advances_to_code_view(self):
        """v2.6: dev 完成 → dispatch code-view（不是 verify）。"""
        t = _make_track("dev.backend")
        t = t.replace(phases={"test": _make_phase_state("completed")})
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(track="dev.backend", phase="dev", status="completed")
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "code-view")

    def test_code_view_completed_advances_to_verify(self):
        """v2.6: code-view 完成 → dispatch verify。"""
        t = _make_track("dev.backend")
        t = t.replace(phases={
            "test": _make_phase_state("completed"),
            "dev": _make_phase_state("completed"),
        })
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="completed",
            summary="cv_score: 85, p0_failures: []",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "verify")

    def test_test_failed_retries(self):
        t = self.track
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="test", status="failed", summary="unit test fail",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "test")
        self.assertEqual(action.attempt, 1)

    def test_test_failed_exhausted(self):
        t = self.track
        t = t.replace(phases={"test": _make_phase_state("pending", attempt=3)},
                       max_fail_retries=3)
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="test", status="failed",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "workflow_failed")

    def test_simple_completed(self):
        t = TrackState.create("proto-compile")
        state = PipelineState(
            change="x", pipeline_order=("proto-compile",),
            tracks={"proto-compile": t},
        )
        record = PipelineRecord(track="proto-compile", phase="simple", status="completed")
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "advance")

    def test_invalid_phase_returns_error(self):
        """未知 phase 返回 error action。"""
        state = PipelineState(change="x")
        record = PipelineRecord(track="x", phase="unknown_phase", status="completed")
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "error")


class TestVerifyPhase(unittest.TestCase):
    """verify 的所有分支。"""

    def test_verify_completed_advances_to_gate(self):
        t = _make_track("dev.backend")
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="verify", status="completed",
            report_path="001-verify.md",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "gate")

    def test_verify_escalate_creates_fix_cycle(self):
        verify = _make_phase_state("running", attempt=1)
        t = _make_track("dev.backend")
        t = t.replace(phases={"test": _make_phase_state("completed"),
                               "dev": _make_phase_state("completed"),
                               "verify": verify})
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="verify", status="escalate",
            summary="3 tests FAIL",
            tasks_updated=("V-1", "V-2"),  # v2.2
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "fix")
        self.assertIsNotNone(new_state.current_sub_pipeline)

    def test_verify_escalate_exhausted_forces_gate(self):
        """fix 循环 >= max_fix_retries → 强制 gate（v2.3：max_fix_retries 由 track 配置决定）。"""
        max_fix = 3  # 显式设置 max_fix_retries=3
        fix_cycles = tuple({"cycle": i+1, "status": "completed"} for i in range(max_fix))
        verify = PhaseState(status="running", attempt=3, fix_cycles=fix_cycles)
        t = TrackState.create("dev.backend", max_fix_retries=max_fix)
        t = t.replace(phases={"verify": verify,
                               "test": _make_phase_state("completed"),
                               "dev": _make_phase_state("completed")})
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="verify", status="escalate",
            tasks_updated=("V-1",),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "gate",
                         "fix_cycles=3 == max_fix_retries=3 时 escalate 应强制 gate")


class TestFixPhase(unittest.TestCase):
    """fix 子 pipeline phase。"""

    def test_fix_completed_back_to_verify(self):
        t = _make_track("dev.backend")
        sp = create_fix_cycle("dev.backend", 1)
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
            current_sub_pipeline=sp,
        )
        record = PipelineRecord(
            track="dev.backend", phase="fix", status="completed",
        )
        new_state, action = reduce_state(state, record)
        # fix 完成后子 pipeline 应该推进到下一 phase（verify）
        self.assertEqual(action.kind, "dispatch")
        self.assertIn(action.phase, ("verify",))


class TestGatePhase(unittest.TestCase):
    """gate 的所有分支。"""

    def test_gate_pass_completes_track(self):
        t = _make_track("dev.backend")
        state = PipelineState(
            change="x", pipeline_order=("dev.backend", "dev.frontend"),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="gate", status="pass",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "advance")
        self.assertTrue(new_state.is_track_completed("dev.backend"))

    def test_gate_fail_creates_gate_fix(self):
        gate = _make_phase_state("running", attempt=1)
        t = _make_track("dev.backend")
        t = t.replace(phases={"test": _make_phase_state("completed"),
                               "dev": _make_phase_state("completed"),
                               "verify": _make_phase_state("completed"),
                               "gate": gate})
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="gate", status="fail",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "fix-gate")
        self.assertIsNotNone(new_state.current_sub_pipeline)

def test_gate_fail_exhausted_accepts_gaps(self):
        """gate-fix 循环耗尽 → track 完成，接受 gap。"""
        gate_cycles = tuple({"cycle": i+1, "status": "fail"} for i in range(2))
        gate = PhaseState(status="running", gate_cycles=gate_cycles)
        t = _make_track("dev.backend")
        t = t.replace(phases={"gate": gate,
                                "test": _make_phase_state("completed"),
                                "dev": _make_phase_state("completed"),
                                "verify": _make_phase_state("completed")},
                       max_gate_fix_retries=2)
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="gate", status="fail",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "advance")
        self.assertTrue(new_state.is_track_completed("dev.backend"))


class TestFinalGate(unittest.TestCase):
    """final-gate 处理。"""

    def test_final_gate_pass(self):
        state = PipelineState(change="x")
        record = PipelineRecord(
            track=FINAL_GATE_TRACK, phase="gate", status="pass",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "done")
        self.assertEqual(new_state.status, "completed")

    def test_final_gate_completed_eq_pass(self):
        state = PipelineState(change="x")
        record = PipelineRecord(
            track=FINAL_GATE_TRACK, phase="gate", status="completed",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "done")

    def test_final_gate_fail(self):
        state = PipelineState(change="x")
        record = PipelineRecord(
            track=FINAL_GATE_TRACK, phase="gate", status="fail",
            summary="missing cross-track dependency",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "workflow_failed")


class TestSubPipelineAdvance(unittest.TestCase):
    """子 pipeline 推进逻辑。"""

    def test_fix_cycle_advance_to_verify(self):
        """fix → 子 pipeline advance → verify。"""
        sp = create_fix_cycle("dev.backend", 1)
        self.assertEqual(sp.current_phase, "fix")
        # 模拟 fix 完成后子 pipeline 推进
        next_sp = sp.advance()
        self.assertEqual(next_sp.current_phase, "verify")
        self.assertEqual(next_sp.status, "running")

    def test_sub_pipeline_completed(self):
        """verify 是最后一个 phase → advance 完成后标记 completed。"""
        sp = create_fix_cycle("dev.backend", 1)
        sp = sp.advance()  # → verify
        sp = sp.advance()  # → completed
        self.assertEqual(sp.status, "completed")

    def test_gate_fix_cycle_has_3_phases(self):
        sp = create_gate_fix_cycle("dev.backend", 1)
        self.assertEqual(sp.phases, ("fix-gate", "verify", "gate"))
        self.assertEqual(sp.current_phase, "fix-gate")


class TestDetect(unittest.TestCase):
    """next_pending 逻辑测试。"""

    def test_completed_state(self):
        from pipeline.detect import next_pending
        state = PipelineState(change="x", status="completed")
        action = next_pending(state)
        self.assertEqual(action.kind, "done")

    def test_failed_state(self):
        from pipeline.detect import next_pending
        state = PipelineState(change="x", status="failed", failed_reason="oops")
        action = next_pending(state)
        self.assertEqual(action.kind, "workflow_failed")

    def test_bootstrap_empty(self):
        from pipeline.detect import next_pending
        state = PipelineState(change="x")
        action = next_pending(state)
        self.assertEqual(action.kind, "bootstrap")

    def test_active_sub_pipeline(self):
        from pipeline.detect import next_pending
        sp = create_fix_cycle("dev.backend", 1)
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": _make_track("dev.backend")},
            current_sub_pipeline=sp,
        )
        action = next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "fix")

    def test_first_track_first_phase(self):
        from pipeline.detect import next_pending
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": _make_track("dev.backend")},
        )
        action = next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "test")

    def test_skips_completed_tracks(self):
        from pipeline.detect import next_pending
        state = PipelineState(
            change="x",
            pipeline_order=("dev.backend", "dev.frontend"),
            tracks={
                "dev.backend": TrackState.create("dev.backend", status="completed"),
                "dev.frontend": _make_track("dev.frontend"),
            },
        )
        action = next_pending(state)
        self.assertEqual(action.track, "dev.frontend")

    def test_all_completed_enters_final_gate(self):
        from pipeline.detect import next_pending
        state = PipelineState(
            change="x",
            pipeline_order=("dev.backend",),
            tracks={"dev.backend": TrackState.create("dev.backend", status="completed")},
            status="running",
        )
        action = next_pending(state)
        self.assertEqual(action.track, FINAL_GATE_TRACK)
        self.assertEqual(action.kind, "dispatch")

    def test_simple_track_routed_to_simple_phase(self):
        """Simple track 应路由到 simple phase。"""
        from pipeline.detect import next_pending
        state = PipelineState(
            change="x",
            pipeline_order=("proto-gen", "dev.backend"),
            track_types={"proto-gen": "simple"},
            status="running",
            tracks={
                "proto-gen": TrackState.create("proto-gen"),
                "dev.backend": TrackState.create("dev.backend"),
            },
        )
        action = next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "simple",
                         "track_types 标记为 simple 的 track 应 dispatch phase=simple")
        self.assertEqual(action.track, "proto-gen")

    def test_simple_track_skipped_after_completion(self):
        """Simple track 完成后转到下一个标准 track。"""
        from pipeline.detect import next_pending
        t = TrackState.create("proto-gen", status="completed")
        ph = t.phases.copy()
        from pipeline.state import PhaseState
        ph["simple"] = PhaseState(status="completed")
        t = t.replace(phases=ph)
        state = PipelineState(
            change="x",
            pipeline_order=("proto-gen", "dev.backend"),
            track_types={"proto-gen": "simple"},
            status="running",
            tracks={
                "proto-gen": t,
                "dev.backend": TrackState.create("dev.backend"),
            },
        )
        action = next_pending(state)
        self.assertEqual(action.track, "dev.backend",
                         "simple track 完成后应跳到下一 track")
        self.assertEqual(action.phase, "test")

    # ============================================================
    # v2.1 新增：问题 2 修复回归测试
    # ============================================================

    def test_fix_status_failed_does_not_crash(self):
        """[v2.3 回归] _handle_fix 在 STATUS_FAILED 分支不应崩溃，
        且应 re_verify（不再 retry fix 自身）。
        """
        from pipeline.events import STATUS_FAILED
        from pipeline.sub_pipeline import create_fix_cycle
        fix_phase = PhaseState(status="running", attempt=1)
        verify_phase = PhaseState(status="running", fix_cycles=(
            {"cycle": 1, "status": "running"},
        ))
        track = TrackState.create("dev.backend", max_fix_retries=3)
        ph = dict(track.phases)
        ph["fix"] = fix_phase
        ph["verify"] = verify_phase
        track = track.replace(phases=ph)
        sp = create_fix_cycle("dev.backend", 1)
        state = PipelineState(
            change="x",
            pipeline_order=("dev.backend",),
            current_track="dev.backend",
            current_phase="fix",
            current_sub_pipeline=sp,
            status="running",
            tracks={"dev.backend": track},
        )
        record = PipelineRecord(
            track="dev.backend", phase="fix", status=STATUS_FAILED,
            summary="fix failed due to cross-track issue",
        )
        new_state, action = reduce_state(state, record)
        self.assertNotEqual(action.kind, "error",
                            "fix STATUS_FAILED 应被 reducer 接受，不应返回 error")
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.track, "dev.backend")
        # v2.3: fix failed 也走 re_verify → 推进到 verify
        self.assertEqual(action.phase, "verify",
                         "v2.3: fix failed 也要 re_verify，不是 retry fix 自身")

    def test_fix_gate_status_failed_does_not_crash(self):
        """[v2.1 回归] _handle_fix_gate 在 STATUS_FAILED 分支不应崩溃。"""
        from pipeline.events import STATUS_FAILED
        fix_gate_phase = PhaseState(status="running", attempt=1)
        gate_phase = PhaseState(status="running")
        track = TrackState.create("dev.backend", max_fix_retries=3)
        ph = dict(track.phases)
        ph["fix-gate"] = fix_gate_phase
        ph["gate"] = gate_phase
        track = track.replace(phases=ph)
        state = PipelineState(
            change="x",
            pipeline_order=("dev.backend",),
            current_track="dev.backend",
            current_phase="fix-gate",
            status="running",
            tracks={"dev.backend": track},
        )
        record = PipelineRecord(
            track="dev.backend", phase="fix-gate", status=STATUS_FAILED,
            summary="fix-gate failed",
        )
        new_state, action = reduce_state(state, record)
        self.assertNotEqual(action.kind, "error")
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.attempt, 2)

    def test_gate_fail_exhausted_accepts_gap(self):
        """[v2.1] gate 循环耗尽后接受 gap 到 track.accepted_gaps。"""
        from pipeline.events import STATUS_FAIL
        gate_phase = PhaseState(
            status="running",
            gate_cycles=(
                {"cycle": 1, "status": "fail"},
                {"cycle": 2, "status": "fail"},
            ),
        )
        track = TrackState.create("dev.backend", max_gate_fix_retries=2)
        ph = dict(track.phases)
        ph["gate"] = gate_phase
        track = track.replace(phases=ph)
        state = PipelineState(
            change="x",
            pipeline_order=("dev.backend",),
            current_track="dev.backend",
            current_phase="gate",
            status="running",
            tracks={"dev.backend": track},
        )
        record = PipelineRecord(
            track="dev.backend", phase="gate", status=STATUS_FAIL,
            summary="", issues="G-1,scope creep",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "advance")
        new_track = new_state.tracks["dev.backend"]
        self.assertEqual(new_track.status, "completed")
        self.assertEqual(len(new_track.accepted_gaps), 1)
        gap = new_track.accepted_gaps[0]
        self.assertEqual(gap["phase"], "gate")
        self.assertEqual(gap["cycles_attempted"], 2)
        self.assertEqual(gap["max_cycles"], 2)
        self.assertIn("scope creep", gap["issues"])
        self.assertIn("accepted_at", gap)

    def test_fix_status_failed_returns_to_verify(self):
        """v2.3: fix failed 不再 retry fix 自身，而是 re_verify。
        不再有 `accept_gap` 协议（fix 内部 retry 已删除）。"""
        from pipeline.events import STATUS_FAILED
        from pipeline.sub_pipeline import create_fix_cycle
        fix_phase = PhaseState(status="running", attempt=3)
        verify_phase = PhaseState(status="running", fix_cycles=(
            {"cycle": 1, "status": "completed"},
        ))
        track = TrackState.create("dev.backend", max_fix_retries=3)
        ph = dict(track.phases)
        ph["fix"] = fix_phase
        ph["verify"] = verify_phase
        track = track.replace(phases=ph)
        sp = create_fix_cycle("dev.backend", 1)
        state = PipelineState(
            change="x",
            pipeline_order=("dev.backend",),
            current_track="dev.backend",
            current_phase="fix",
            current_sub_pipeline=sp,
            status="running",
            tracks={"dev.backend": track},
        )
        record = PipelineRecord(
            track="dev.backend", phase="fix", status=STATUS_FAILED,
            summary="fix exhausted",
            issues="G-1,G-2",
        )
        new_state, action = reduce_state(state, record)
        # v2.3: fix 内部不再 retry / 不再 accept_gap，转向 re_verify
        self.assertNotEqual(action.kind, "advance",
                            "v2.3: fix 不再有 accept_gap 协议")
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "verify",
                         "v2.3: fix failed → re_verify，max_fix_retries 由 verify→fix 循环控制")


# ============================================================
# v2.6: code-view phase + fix-code-view cycle + sub-code-view
# ============================================================

class TestCodeViewPhase(unittest.TestCase):
    """code-view phase: completed / escalate / failed。"""

    def setUp(self):
        self.track = _make_track("dev.backend")

    def test_code_view_completed_advances_to_verify(self):
        t = self.track.replace(phases={
            "test": _make_phase_state("completed"),
            "dev": _make_phase_state("completed"),
        })
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="completed",
            summary="cv_score: 90, p0_failures: []",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "verify")

    def test_code_view_escalate_creates_sub_pipeline(self):
        t = self.track.replace(phases={
            "test": _make_phase_state("completed"),
            "dev": _make_phase_state("completed"),
        })
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
            current_track="dev.backend", current_phase="code-view",
        )
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="escalate",
            summary="CV-1 fail",
            tasks_updated=("CV-1",),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "fix-code-view")
        sp = new_state.current_sub_pipeline
        self.assertIsNotNone(sp)
        self.assertEqual(sp.kind, CODE_VIEW_CYCLE)
        self.assertEqual(sp.parent_phase, "code-view")

    def test_code_view_escalate_without_tasks_updated_returns_error(self):
        """v2.6: escalate 必填 tasks_updated (CV-* IDs)。"""
        t = self.track
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
            current_track="dev.backend", current_phase="code-view",
        )
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="escalate",
            summary="CV-1 fail",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "error")
        self.assertIn("tasks_updated", action.detail.get("reason", ""))

    def test_code_view_escalate_exhausted_force_verify(self):
        """v2.6: code_view_fix_cycles 达 max_code_view_fix_retries → force verify。"""
        t = self.track.replace(
            phases={
                "code-view": PhaseState(
                    status="pending",
                    code_view_fix_cycles=(
                        {"cycle": 1, "status": "completed"},
                        {"cycle": 2, "status": "completed"},
                        {"cycle": 3, "status": "completed"},
                    ),
                ),
            },
            max_code_view_fix_retries=3,
        )
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
            current_track="dev.backend", current_phase="code-view",
        )
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="escalate",
            summary="CV-1 still fail",
            tasks_updated=("CV-1",),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "verify")
        # summary 应记录 exhausted
        cv_phase = new_state.tracks["dev.backend"].phases["code-view"]
        self.assertIn("exhausted", cv_phase.summary)

    def test_code_view_failed_retries(self):
        """v2.6: code-view 阶段 failed → attempt++ 重试，耗尽 workflow_failed。"""
        t = self.track.replace(
            max_fail_retries=2,
            phases={"code-view": _make_phase_state("pending", attempt=2)},
        )
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
            current_track="dev.backend", current_phase="code-view",
        )
        # 第 3 次失败（attempt 已 2，再失败 → 3 > 2 → workflow_failed）
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="failed",
            summary="code-view error",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "workflow_failed")

    def test_code_view_failed_retries_within_limit(self):
        """v2.6: 失败但在 max_fail_retries 内 → dispatch 重试。"""
        t = self.track.replace(
            max_fail_retries=3,
            phases={"code-view": _make_phase_state("pending", attempt=1)},
        )
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
            current_track="dev.backend", current_phase="code-view",
        )
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="failed",
            summary="transient",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "code-view")
        self.assertEqual(action.attempt, 2)


class TestFixCodeViewPhase(unittest.TestCase):
    """fix-code-view 子 pipeline 中的 fix 阶段。"""

    def _make_sub_state(self) -> tuple[PipelineState, SubPipeline]:
        sp = create_code_view_cycle("dev.backend", 1)
        track = _make_track("dev.backend").replace(
            phases={
                "code-view": PhaseState(
                    status="pending",
                    code_view_fix_cycles=({"cycle": 1, "status": "pending"},),
                ),
            },
        )
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": track},
            current_track="dev.backend",
            current_phase="fix-code-view",
            current_sub_pipeline=sp,
        )
        return state, sp

    def test_fix_code_view_completed_advances_to_code_view(self):
        """fix-code-view completed → 子 pipeline 推进到 code-view。"""
        state, _ = self._make_sub_state()
        record = PipelineRecord(
            track="dev.backend", phase="fix-code-view", status="completed",
            summary="fixed CV-1",
            report_path="/tmp/fix-cv.md",
            tasks_updated=("CV-1",),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "code-view")
        # fix_cycle 应标记为 completed
        cv = new_state.tracks["dev.backend"].phases["code-view"]
        self.assertEqual(cv.code_view_fix_cycles[-1]["status"], "completed")

    def test_fix_code_view_failed_advances_to_code_view(self):
        """v2.6: fix-code-view 失败不重试自身，进入 code-view 让其判定。"""
        state, _ = self._make_sub_state()
        record = PipelineRecord(
            track="dev.backend", phase="fix-code-view", status="failed",
            summary="could not fix",
            report_path="/tmp/fix-cv.md",
            tasks_updated=("CV-1",),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "code-view")
        cv = new_state.tracks["dev.backend"].phases["code-view"]
        self.assertEqual(cv.code_view_fix_cycles[-1]["status"], "failed")


class TestSubCodeViewPhase(unittest.TestCase):
    """code-view-cycle 子 pipeline 中的 code-view 重审阶段。"""

    def _make_sub_state(self) -> tuple[PipelineState, SubPipeline]:
        sp = create_code_view_cycle("dev.backend", 1)
        # advance to index 1 (code-view)
        sp = sp.advance()
        track = _make_track("dev.backend").replace(
            phases={
                "code-view": PhaseState(
                    status="pending",
                    code_view_fix_cycles=({"cycle": 1, "status": "completed"},),
                ),
            },
        )
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": track},
            current_track="dev.backend",
            current_phase="code-view",
            current_sub_pipeline=sp,
        )
        return state, sp

    def test_sub_code_view_completed_returns_to_main_pipeline(self):
        """子 pipeline 中 code-view completed → 主 pipeline dispatch verify。"""
        state, _ = self._make_sub_state()
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="completed",
            summary="cv_score: 95",
            report_path="/tmp/cv.md",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "verify")
        self.assertIsNone(new_state.current_sub_pipeline)

    def test_sub_code_view_escalate_creates_new_fix_cycle(self):
        """子 pipeline 中 code-view escalate → 回到 fix-code-view，新一轮。"""
        # simulate sub pipeline 已有 1 个 fix_cycle，再 escalate 时再开第 2 轮
        track = _make_track("dev.backend").replace(
            phases={
                "code-view": PhaseState(
                    status="pending",
                    code_view_fix_cycles=(
                        {"cycle": 1, "status": "completed"},
                    ),
                ),
            },
            max_code_view_fix_retries=3,
        )
        sp = create_code_view_cycle("dev.backend", 1).advance()  # index=1
        # current_index > 0 to trigger escalate→fix path
        sp = dataclasses.replace(sp, current_index=1)
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": track},
            current_track="dev.backend",
            current_phase="code-view",
            current_sub_pipeline=sp,
        )
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="escalate",
            summary="CV-1 still fail",
            tasks_updated=("CV-1",),
            report_path="/tmp/cv.md",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "fix-code-view")
        # 新一轮已记录
        cv = new_state.tracks["dev.backend"].phases["code-view"]
        self.assertEqual(len(cv.code_view_fix_cycles), 2)

    def test_sub_code_view_escalate_exhausted_force_verify(self):
        """子 pipeline 中再次 escalate 超 max → force verify。"""
        track = _make_track("dev.backend").replace(
            phases={
                "code-view": PhaseState(
                    status="pending",
                    code_view_fix_cycles=(
                        {"cycle": 1, "status": "completed"},
                        {"cycle": 2, "status": "completed"},
                        {"cycle": 3, "status": "completed"},
                    ),
                ),
            },
            max_code_view_fix_retries=3,
        )
        sp = create_code_view_cycle("dev.backend", 3)
        sp = dataclasses.replace(sp, current_index=1)
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": track},
            current_track="dev.backend",
            current_phase="code-view",
            current_sub_pipeline=sp,
        )
        record = PipelineRecord(
            track="dev.backend", phase="code-view", status="escalate",
            summary="CV-1 still fail",
            tasks_updated=("CV-1",),
            report_path="/tmp/cv.md",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "verify")
        self.assertIsNone(new_state.current_sub_pipeline)
        cv = new_state.tracks["dev.backend"].phases["code-view"]
        self.assertIn("exhausted", cv.summary)


class TestSubPipelineRouting(unittest.TestCase):
    """_handle_sub_pipeline_record 路由到 _handle_sub_code_view / _handle_fix_code_view。"""

    def test_routes_fix_code_view_to_handler(self):
        sp = create_code_view_cycle("dev.backend", 1)
        track = _make_track("dev.backend").replace(phases={
            "code-view": PhaseState(
                status="pending",
                code_view_fix_cycles=({"cycle": 1, "status": "pending"},),
            ),
        })
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": track},
            current_track="dev.backend",
            current_phase="fix-code-view",
            current_sub_pipeline=sp,
        )
        record = PipelineRecord(
            track="dev.backend", phase="fix-code-view", status="completed",
            summary="fixed", report_path="/tmp/fix.md",
            tasks_updated=("CV-1",),
        )
        new_state, action = reduce_state(state, record)
        # 推进到 sub pipeline 下一 phase（code-view）
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "code-view")


if __name__ == "__main__":
    unittest.main()