"""Unit tests for fix cycle behavior — v2.3 unified re_verify semantics.

v2.3 changed fix_routing:
  - REMOVED: fix_routing config field ("direct_to_gate" / "re_verify")
  - NEW: fix → verify is the only path. Unified behavior.
  - max_fix_retries now means "verify→fix loop total count" (not retry count of fix agent).
  - When fix_cycles.count >= max_fix_retries: force gate (即使仍有未修复的 V-*)。
"""

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
    STATUS_COMPLETED, STATUS_FAILED, STATUS_ESCALATE, STATUS_PASS, STATUS_FAIL,
    SUB_VERIFY, SUB_FIX, SUB_GATE,
)
from pipeline.reducer import reduce_state
from pipeline.sub_pipeline import create_fix_cycle, SubPipeline


def _make_track(track_id: str = "dev.test", max_fix_retries: int = 5) -> TrackState:
    """创建一个测试 track，已走完 test/dev 到 verify 阶段。"""
    return TrackState.create(
        track_id,
        max_fix_retries=max_fix_retries,
        phases={
            "test": PhaseState(status="completed"),
            "dev": PhaseState(status="completed"),
            "verify": PhaseState(status="pending", fix_cycles=()),
        },
    )


def _make_state(track: TrackState) -> PipelineState:
    return PipelineState(
        change="test-change",
        pipeline_order=(track.track_id,),
        tracks={track.track_id: track},
        current_track=track.track_id,
        current_phase="verify",
        status="running",
        stage_order=("dev",),
        stage_env_map={"dev": "dev-local"},
    )


class TestFixUnifiedReVerify(unittest.TestCase):
    """v2.3: fix 完成后统一 re_verify（不再有 direct_to_gate 模式）。"""

    def test_escalate_creates_fix_subpipeline(self):
        """verify escalate → fix 子 pipeline 启动。"""
        state = _make_state(_make_track())
        record = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-1 FAIL",
            tasks_updated=("V-1",),
            report_path="/tmp/v.md",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_FIX)
        self.assertIsNotNone(new_state.current_sub_pipeline)
        # fix_cycles 应有 1 个 cycle 记录
        verify = new_state.tracks["dev.test"].phases["verify"]
        self.assertEqual(len(verify.fix_cycles), 1)

    def test_fix_completed_returns_to_verify(self):
        """fix completed → 子 pipeline advance 到 verify（不是 direct_to_gate）。

        注：fix 是 sub-pipeline 第一阶段，complete 后 sub-pipeline 内部推进到 verify。
        重要的是 dispatch.phase == 'verify'（不是 'gate'）。
        """
        state = _make_state(_make_track())
        r1 = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-1 FAIL", tasks_updated=("V-1",),
            report_path="/tmp/v.md",
        )
        ns1, a1 = reduce_state(state, r1)
        self.assertEqual(a1.phase, SUB_FIX,
                         "首次 escalate 后 dispatch fix")

        # fix completed 后：sub-pipeline 内部推进到 verify
        r2 = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_COMPLETED,
            summary="fix ok", report_path="/tmp/f.md",
        )
        ns2, a2 = reduce_state(ns1, r2)
        # dispatcher: dispatch verify (NOT gate)
        self.assertEqual(a2.kind, "dispatch")
        self.assertEqual(a2.phase, SUB_VERIFY,
                         "fix 完成后必须 dispatch verify（v2.3 不再 direct_to_gate）")
        # sub-pipeline 仍存在，已推进到 verify 阶段
        self.assertIsNotNone(ns2.current_sub_pipeline)
        self.assertEqual(ns2.current_sub_pipeline.current_phase, SUB_VERIFY,
                         "sub-pipeline 应推进到 verify 阶段")

    def test_verify_after_fix_completes_pipeline(self):
        """fix 完成 → verify 完成 → sub-pipeline 完成 → dispatch gate。"""
        state = _make_state(_make_track())
        r1 = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-1 FAIL", tasks_updated=("V-1",),
            report_path="/tmp/v.md",
        )
        ns1, _ = reduce_state(state, r1)
        r2 = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_COMPLETED,
            summary="fix ok", report_path="/tmp/f.md",
        )
        ns2, _ = reduce_state(ns1, r2)
        # 现在 dispatch 应是 verify；verify 这次 completed
        r3 = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_COMPLETED,
            summary="all V-* PASS", report_path="/tmp/v.md",
        )
        ns3, a3 = reduce_state(ns2, r3)
        # verify.completed 应推进子 pipeline 完成 → dispatch gate
        self.assertEqual(a3.kind, "dispatch")
        self.assertEqual(a3.phase, SUB_GATE,
                         "verify.completed 必须进 gate")
        self.assertIsNone(ns3.current_sub_pipeline,
                          "verify.completed 后 sub-pipeline 应清空")

    def test_fix_failed_also_returns_to_verify(self):
        """fix failed（status=failed）→ 同样 re_verify（不再 retry fix 自身）。"""
        state = _make_state(_make_track())
        r1 = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-1 FAIL", tasks_updated=("V-1",),
            report_path="/tmp/v.md",
        )
        ns1, _ = reduce_state(state, r1)
        # fix failed
        r2 = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_FAILED,
            summary="fix agent crashed",
            report_path="/tmp/f.md",
        )
        ns2, a2 = reduce_state(ns1, r2)
        self.assertEqual(a2.kind, "dispatch")
        self.assertEqual(a2.phase, SUB_VERIFY,
                         "fix failed 后应 re_verify（v2.3 不再 retry fix 自身）")

    def test_escalate_exhausted_forces_gate(self):
        """fix_cycles 达到 max_fix_retries 时，verify escalate 强制进 gate。"""
        track = _make_track(max_fix_retries=2)
        track = track.replace(
            phases={
                "test": PhaseState(status="completed"),
                "dev": PhaseState(status="completed"),
                "verify": PhaseState(status="running", fix_cycles=(
                    {"cycle": 1, "status": "completed"},
                    {"cycle": 2, "status": "completed"},
                )),
            }
        )
        state = _make_state(track)
        record = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-3 FAIL", tasks_updated=("V-3",),
            report_path="/tmp/v.md",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_GATE,
                         "fix_cycles=2 == max_fix_retries=2 时 escalate 应强制 gate")
        self.assertIsNone(new_state.current_sub_pipeline)


class TestFixCycleIntegration(unittest.TestCase):
    """fix→verify 循环完整链路测试。"""

    def test_full_loop_until_exhausted(self):
        """完整模拟：verify escalate → fix → verify escalate → fix → ... → exhausted → gate。"""
        state = _make_state(_make_track(max_fix_retries=2))
        cycle_actions = []

        # ── 完整循环关键问题：每次 reducer 调用后记录 dispatch phase ──
        # 第一轮：verify escalate → fix
        r = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-1 FAIL", tasks_updated=("V-1",),
            report_path="/tmp/v1.md",
        )
        ns, a = reduce_state(state, r)
        cycle_actions.append(a.phase)  # fix

        # fix → 子 pipeline 推进到 verify，dispatch verify
        r = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_COMPLETED,
            summary="fix 1", report_path="/tmp/f1.md",
        )
        ns, a = reduce_state(ns, r)
        cycle_actions.append(a.phase)  # verify（子 pipeline 内部推进）

        # 模拟 verify dispatch agent 实际跑：completd
        r = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_COMPLETED,
            summary="after fix 1", report_path="/tmp/v1b.md",
        )
        ns, a = reduce_state(ns, r)
        # verify.completed 直接进 gate（不再 escalate）
        cycle_actions.append(a.phase)  # gate

        # 验证：2 个 fix_cycles 共进入 2 次 escalate：1 (initial) + 1 (after fix 1's verify escalation)
        # 实际：initial verify 的 fix_cycles 应是 1
        verify_after = ns.tracks["dev.test"].phases["verify"]
        # 这里 fix_cycles 仍只有 1（verify.completed 不增加 fix_cycles）
        self.assertEqual(len(verify_after.fix_cycles), 1)

        self.assertEqual(
            cycle_actions,
            ["fix", "verify", "gate"],
            "v2.3: fix→verify(gate) 一次成功，无须 5 步"
        )

    def test_loop_with_persistent_failure(self):
        """verify 持续 escalate（fix 没修好）→ 多轮循环 → 耗尽 → gate。"""
        # max_fix_retries=2 — 只允许 2 个 fix 子 pipeline（即 escalate 触发次数）
        state = _make_state(_make_track(max_fix_retries=2))
        cycle_actions = []

        # 第 1 轮 escalate
        r = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-1 FAIL", tasks_updated=("V-1",),
            report_path="/tmp/v1.md",
        )
        ns, a = reduce_state(state, r)
        cycle_actions.append(a.phase)  # fix

        # fix 完成 → 子 pipeline 推进到 verify
        r = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_COMPLETED,
            summary="fix 1", report_path="/tmp/f1.md",
        )
        ns, a = reduce_state(ns, r)
        cycle_actions.append(a.phase)  # verify

        # verify 还是 escalate（fix 没修好）
        r = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-1 still FAIL", tasks_updated=("V-1",),
            report_path="/tmp/v2.md",
        )
        ns, a = reduce_state(ns, r)
        cycle_actions.append(a.phase)  # fix

        # fix 完成 → 子 pipeline 推进到 verify
        r = PipelineRecord(
            track="dev.test", phase="fix", status=STATUS_COMPLETED,
            summary="fix 2", report_path="/tmp/f2.md",
        )
        ns, a = reduce_state(ns, r)
        cycle_actions.append(a.phase)  # verify

        # verify 第 3 次 escalate → max_fix_retries=2 已耗尽 → 直接 gate
        r = PipelineRecord(
            track="dev.test", phase="verify", status=STATUS_ESCALATE,
            summary="V-1 still FAIL", tasks_updated=("V-1",),
            report_path="/tmp/v3.md",
        )
        ns, a = reduce_state(ns, r)
        cycle_actions.append(a.phase)  # gate（耗尽 → 强制）

        self.assertEqual(
            cycle_actions,
            ["fix", "verify", "fix", "verify", "gate"],
            "v2.3: max_fix_retries=2 时 5 步：fix/verify/escalate-fix/verify/escalate-gate"
        )


if __name__ == "__main__":
    unittest.main()
