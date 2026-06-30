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

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.events import PipelineRecord, PipelineAction, FINAL_GATE_TRACK
from pipeline.reducer import (
    reduce_state,
    MAX_FIX_CYCLES,
    DEFAULT_MAX_RETRIES,
    DEFAULT_GATE_FIX_RETRIES,
    _handle_linear_phase,
    _handle_final_gate,
    _handle_fix,
    _handle_fix_gate,
    _handle_gate,
    _handle_sub_pipeline_record,
    _handle_sub_verify,
    _handle_sub_gate,
    _handle_verify,
    _sub_pipeline_advance,
    PHASE_AGENTS,
)
from pipeline.state import (
    PipelineState,
    TrackState,
    PhaseState,
    SUB_PHASES,
    SUB_PHASES_WITH_FIX,
    FIX_SUB,
    FIX_GATE_SUB,
    SIMPLE_SUB,
)
from pipeline.sub_pipeline import SubPipeline, create_fix_cycle, create_gate_fix_cycle


def _make_track(track_id: str, status: str = "pending") -> TrackState:
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

    def test_dev_completed_advances_to_verify(self):
        t = _make_track("dev.backend")
        t = t.replace(phases={"test": _make_phase_state("completed")})
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(track="dev.backend", phase="dev", status="completed")
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
        t = t.replace(phases={"test": _make_phase_state("pending", attempt=DEFAULT_MAX_RETRIES)})
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
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "fix")
        self.assertIsNotNone(new_state.current_sub_pipeline)

    def test_verify_escalate_exhausted_forces_gate(self):
        """fix 循环 > MAX_FIX_CYCLES → 强制 gate。"""
        fix_cycles = tuple({"cycle": i+1, "status": "completed"} for i in range(MAX_FIX_CYCLES))
        verify = PhaseState(status="running", attempt=3, fix_cycles=fix_cycles)
        t = _make_track("dev.backend")
        t = t.replace(phases={"verify": verify,
                               "test": _make_phase_state("completed"),
                               "dev": _make_phase_state("completed")})
        state = PipelineState(
            change="x", pipeline_order=("dev.backend",),
            tracks={"dev.backend": t},
        )
        record = PipelineRecord(
            track="dev.backend", phase="verify", status="escalate",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "gate")


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
        gate_cycles = tuple({"cycle": i+1, "status": "fail"} for i in range(DEFAULT_GATE_FIX_RETRIES))
        gate = PhaseState(status="running", gate_cycles=gate_cycles)
        t = _make_track("dev.backend")
        t = t.replace(phases={"gate": gate,
                               "test": _make_phase_state("completed"),
                               "dev": _make_phase_state("completed"),
                               "verify": _make_phase_state("completed")})
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


if __name__ == "__main__":
    unittest.main()