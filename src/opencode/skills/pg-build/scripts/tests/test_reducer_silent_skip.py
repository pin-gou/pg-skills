"""v3.4: reducer._handle_linear_phase 通用 silent-skip 单测。

- review / verify / gate 任一被关闭，自动标记 completed 跳过
- summary 写明 disabled 原因
- 多重关闭：test → dev → final-gate 路径（无 review/verify/gate）
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.events import FINAL_GATE_TRACK, PipelineRecord, STATUS_COMPLETED
from pipeline.reducer import _handle_linear_phase
from pipeline.state import (
    FIX_REVIEW_SUB,
    PipelineState,
    TrackState,
    PhaseState,
)


def _make_state(track_id: str, **enable_flags) -> tuple[PipelineState, TrackState]:
    """构造最小可用 state 与 track（track phase 都是 pending）。"""
    phases = {
        p: PhaseState(status="pending")
        for p in ("test", "dev", "review", "verify", "gate")
    }
    t = TrackState.create(
        track_id,
        status="pending",
        phases=phases,
        **enable_flags,
    )
    state = PipelineState(
        change="x",
        pipeline_order=(track_id,),
        tracks={track_id: t},
        status="running",
        current_track="",
        current_phase="",
    )
    return state, t


class TestSilentSkip(unittest.TestCase):
    """test/dev completed → 下一 phase 若禁用，silent-skip。"""

    def test_verify_disabled_skipped_after_review(self):
        """verify_enabled=false：review 完成时 next_phase=verify → silent-skip verify → dispatch gate。"""
        state, t = _make_state("dev.backend", verify_enabled=False)
        # dev 已完成（前置状态） + 触发 review 完成 record
        new_phases = {**t.phases, "dev": PhaseState(status="completed", summary="d")}
        state = state.replace(
            tracks={**state.tracks, "dev.backend": t.replace(phases=new_phases)},
        )
        record = PipelineRecord(
            track="dev.backend", phase="review", status=STATUS_COMPLETED,
            summary="review done",
        )
        new_state, action = _handle_linear_phase(state, record)
        new_track = new_state.tracks["dev.backend"]
        # verify 被 silent-skip 标 completed
        self.assertEqual(new_track.phases["verify"].status, "completed")
        self.assertIn("verify disabled", new_track.phases["verify"].summary)
        # 推进到 gate
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "gate")

    def test_all_three_disabled_after_dev(self):
        """三关闭：当前 next_phase 是被关闭的 review/verify/gate → 全部标 completed → advance。"""
        state, _ = _make_state(
            "dev.backend",
            code_review_enabled=False,
            verify_enabled=False,
            gate_enabled=False,
        )
        # 设 current_phase="dev" 与 dev 已 completed record 一致
        record = PipelineRecord(
            track="dev.backend", phase="dev", status=STATUS_COMPLETED,
            summary="dev done",
        )
        new_state, action = _handle_linear_phase(state, record)
        new_track = new_state.tracks["dev.backend"]
        # 三 phase 都被标 completed（review 是首个 next_phase 且 disabled）
        for ph in ("review", "verify", "gate"):
            self.assertEqual(new_track.phases[ph].status, "completed",
                             msg=f"{ph} should be auto-completed")
            self.assertIn(f"{ph} disabled", new_track.phases[ph].summary)
        # _next_phase("gate") 是 None → 返回 advance（让 detect 推到 final-gate）
        self.assertEqual(action.kind, "advance")

    def test_summary_mentions_manifest(self):
        """silent-skip 的 summary 应清楚说明是 manifest 缺失导致。"""
        state, t = _make_state("dev.backend", verify_enabled=False)
        # dev 已完成 + 触发 review 完成 record
        new_phases = {**t.phases, "dev": PhaseState(status="completed", summary="d")}
        state = state.replace(
            tracks={**state.tracks, "dev.backend": t.replace(phases=new_phases)},
        )
        record = PipelineRecord(
            track="dev.backend", phase="review", status=STATUS_COMPLETED,
            summary="review done",
        )
        new_state, _ = _handle_linear_phase(state, record)
        new_track = new_state.tracks["dev.backend"]
        self.assertIn("phase_prompts.verify", new_track.phases["verify"].summary)

    def test_test_dev_never_auto_skipped(self):
        """test / dev 即使显式 disabled（不应发生但防御），也保留原语义。

        注意：_phase_enabled 永远对 test/dev 返回 True；这里测试构造
        code_review_enabled=False 但 _handle_linear_phase 不会 skip test/dev。
        """
        state, t = _make_state("dev.backend", code_review_enabled=False)
        # 把 review 标记为 completed（模拟 rerun 场景），再触发 test completed
        new_phases = {**t.phases, "review": PhaseState(status="completed", summary="x")}
        state = state.replace(
            tracks={
                **state.tracks,
                "dev.backend": t.replace(phases=new_phases),
            },
        )
        record = PipelineRecord(
            track="dev.backend", phase="test", status=STATUS_COMPLETED,
            summary="test done",
        )
        new_state, action = _handle_linear_phase(state, record)
        # test 后下一 phase 是 dev
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "dev")


class TestPhaseEnabledHelper(unittest.TestCase):
    """直接验证 _phase_enabled helper。"""

    def test_test_dev_always_enabled(self):
        from pipeline.reducer import _phase_enabled
        t = TrackState.create("dev.backend")
        self.assertTrue(_phase_enabled(t, "test"))
        self.assertTrue(_phase_enabled(t, "dev"))

    def test_three_independent(self):
        from pipeline.reducer import _phase_enabled
        t = TrackState.create(
            "dev.backend",
            code_review_enabled=False,
            verify_enabled=True,
            gate_enabled=False,
        )
        self.assertFalse(_phase_enabled(t, "review"))
        self.assertTrue(_phase_enabled(t, "verify"))
        self.assertFalse(_phase_enabled(t, "gate"))

    def test_unknown_phase_returns_true(self):
        """未知 phase 视为启用（防御未来扩展）。"""
        from pipeline.reducer import _phase_enabled
        t = TrackState.create("dev.backend")
        self.assertTrue(_phase_enabled(t, "simple"))
        self.assertTrue(_phase_enabled(t, "fix"))


if __name__ == "__main__":
    unittest.main()
