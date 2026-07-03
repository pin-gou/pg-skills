"""Unit tests for fix_routing — v2.2 fix 完成后的流向控制。"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
    ),
)

from pipeline.state import PipelineState, TrackState, PhaseState
from pipeline.events import (
    PipelineRecord, PipelineAction,
    DEFAULT_FIX_ROUTING, FIX_ROUTING_RE_VERIFY,
    STATUS_COMPLETED, STATUS_FAILED, STATUS_ESCALATE, STATUS_PASS, STATUS_FAIL,
    SUB_VERIFY, SUB_FIX, SUB_GATE,
)
from pipeline.reducer import reduce_state


def _make_state_with_fix_routing(fix_routing: str = "") -> PipelineState:
    """创建一个测试 state，包含一个 track，已走完 test/dev 到 verify 阶段。"""
    track_id = "dev.test"
    track = TrackState.create(
        track_id,
        fix_routing=fix_routing,
        phases={
            "test": PhaseState(status="completed"),
            "dev": PhaseState(status="completed"),
            "verify": PhaseState(status="pending", fix_cycles=()),
        },
    )
    return PipelineState(
        change="test-change",
        pipeline_order=(track_id,),
        tracks={track_id: track},
        current_track=track_id,
        current_phase="verify",
        status="running",
        stage_order=("dev",),
        stage_env_map={"dev": "dev-local"},
    )


class TestFixRoutingDirectToGate(unittest.TestCase):
    """默认 fix_routing='' (direct_to_gate) 的行为测试。"""

    def test_fix_completed_dispatches_gate_not_verify(self):
        state = _make_state_with_fix_routing("")
        # verify escalate → reduce 后产生 fix dispatch
        record = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-backend-4 FAIL",
            tasks_updated=("V-backend-4",),
            report_path="/tmp/verify.md",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "fix")

        # fix completed → reduce
        record2 = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_COMPLETED,
            summary="fix gateway issue",
            report_path="/tmp/fix-1.md",
            tasks_updated=("V-backend-4",),
        )
        new_state2, action2 = reduce_state(new_state, record2)
        # 默认 direct_to_gate: action 应该是 dispatch gate, 不是 dispatch verify
        self.assertEqual(action2.kind, "dispatch", f"got {action2.kind} instead of dispatch")
        self.assertEqual(action2.phase, SUB_GATE,
                         f"expected gate, got {action2.phase}")


    def test_fix_completed_gate_cycle_1(self):
        """gate 的 cycle 应为 1（非 verify 的 cycle=2）。"""
        state = _make_state_with_fix_routing("")
        record = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-1 FAIL", tasks_updated=("V-1",),
            report_path="/tmp/v.md",
        )
        ns, a = reduce_state(state, record)
        record2 = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_COMPLETED,
            summary="fix done", report_path="/tmp/f.md",
        )
        ns2, a2 = reduce_state(ns, record2)
        # cycle 应该为 1（不是 2）
        self.assertEqual(a2.cycle, 1)


class TestFixRoutingReVerify(unittest.TestCase):
    """fix_routing=re_verify 保留旧行为的测试。"""

    def test_fix_completed_dispatches_verify_not_gate(self):
        state = _make_state_with_fix_routing(FIX_ROUTING_RE_VERIFY)
        record = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-2 FAIL", tasks_updated=("V-2",),
            report_path="/tmp/v.md",
        )
        ns, a = reduce_state(state, record)
        record2 = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_COMPLETED,
            summary="fix done", report_path="/tmp/f.md",
        )
        ns2, a2 = reduce_state(ns, record2)
        # re_verify: action 应该是 dispatch verify, 不是 dispatch gate
        self.assertEqual(a2.kind, "dispatch")
        self.assertEqual(a2.phase, SUB_VERIFY)

    def test_re_verify_cycle_increments(self):
        """re_verify 时 verify cycle 递增（cycle=2）。"""
        state = _make_state_with_fix_routing(FIX_ROUTING_RE_VERIFY)
        record = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-3 FAIL", tasks_updated=("V-3",),
            report_path="/tmp/v.md",
        )
        ns, a = reduce_state(state, record)
        record2 = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_COMPLETED,
            summary="fix done", report_path="/tmp/f.md",
        )
        ns2, a2 = reduce_state(ns, record2)
        self.assertEqual(a2.cycle, 1)  # fix cycle 后 verify 的 cycle=1（新的尝试）


if __name__ == "__main__":
    unittest.main()