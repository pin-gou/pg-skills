"""集成测试：完整 TDVG 流程（test→dev→verify→gate→pass）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.event_log import EventLog
from pipeline.snapshot import load_snapshot, save_snapshot
from pipeline.state import PipelineState, TrackState, PhaseState
from pipeline.events import PipelineRecord, PipelineAction, FINAL_GATE_TRACK
from pipeline.reducer import reduce_state, PHASE_AGENTS
from pipeline.detect import next_pending
from pipeline.orchestrator import Orchestrator


def _setup_initial_state(tmp_root: str, change: str = "test-change") -> Orchestrator:
    """设置初始 state 并跳过 bootstrap。"""
    state = PipelineState(
        change=change,
        pipeline_order=("dev.backend", "dev.frontend"),
        status="running",
        tracks={
            "dev.backend": TrackState.create("dev.backend", modules=("backend",)),
            "dev.frontend": TrackState.create("dev.frontend", modules=("frontend",)),
        },
    )
    save_snapshot(tmp_root, state)
    orch = Orchestrator(change)
    orch.change_root = tmp_root
    orch.state = state
    # 调用 next() 设置 current_track/current_phase 为第一个 dispatch
    orch.next()
    return orch


class TestIntegrationTdvg(unittest.TestCase):
    """完整 TDVG：test→dev→verify→gate→pass。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        self.orch = _setup_initial_state(self.tmp, "test-change")

    def test_full_tdvg(self):
        """test→dev→verify→gate→pass→track completed。"""
        track = "dev.backend"

        # Step 1: test completed → dev
        r1 = self.orch.record("completed", summary="10 tests PASS")
        self.assertEqual(r1["action"], "dispatch")
        self.assertEqual(r1["sub"], "dev")

        # Step 2: dev completed → verify
        r2 = self.orch.record("completed", summary="impl done")
        self.assertEqual(r2["action"], "dispatch")
        self.assertEqual(r2["sub"], "verify")

        # Step 3: verify completed → gate
        r3 = self.orch.record("completed", summary="all V-* PASS")
        self.assertEqual(r3["action"], "dispatch")
        self.assertEqual(r3["sub"], "gate")

        # Step 4: gate pass → track completed → advance to next track
        r4 = self.orch.record("pass", summary="all G-* PASS")
        self.assertEqual(r4["action"], "dispatch")  # advance 内部调用 next() 返回下一个 dispatch
        self.assertEqual(r4["item"], "dev.frontend")

        # Verify track is completed
        self.assertTrue(self.orch.state.is_track_completed(track))

        # Verify event log has all entries
        events = self.orch.event_log.replay()
        types = [e["type"] for e in events]
        self.assertIn("record_received", types)
        self.assertIn("dispatch_started", types)

    def test_two_tracks_then_final_gate(self):
        """两个 track 都完成 → final-gate。"""
        # 完成 dev.backend
        self.orch.record("completed")  # test
        self.orch.record("completed")  # dev
        self.orch.record("completed")  # verify
        r1 = self.orch.record("pass")  # gate → advance 到 dev.frontend
        self.assertEqual(r1["action"], "dispatch")
        self.assertEqual(r1["item"], "dev.frontend")

        # 完成 dev.frontend
        self.orch.record("completed")  # test
        self.orch.record("completed")  # dev
        self.orch.record("completed")  # verify
        r2 = self.orch.record("pass")  # gate → advance 到 final-gate

        self.assertEqual(r2["action"], "dispatch")
        self.assertEqual(r2["item"], FINAL_GATE_TRACK)

    def test_workflow_failed(self):
        """test 重试耗尽 → workflow_failed，需要 4 次（max_retries=3, 计数从0开始）。"""
        self.orch.record("failed", issues="error 1")
        self.orch.record("failed", issues="error 2")
        self.orch.record("failed", issues="error 3")
        # 第 4 次失败 → exhausted
        r = self.orch.record("failed", issues="error 4")
        self.assertEqual(r["action"], "workflow_failed")
        self.assertTrue(r.get("fatal", False))


class TestIntegrationFixCycle(unittest.TestCase):
    """verify escalate → fix → re-verify → gate。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        self.orch = _setup_initial_state(self.tmp, "test-change")

    def test_verify_escalate_then_fix(self):
        """verify escalate → 创建子 pipeline。"""
        self.orch.record("completed")  # test
        self.orch.record("completed")  # dev

        # verify escalate
        r = self.orch.record("escalate", summary="3 tests FAIL")
        self.assertEqual(r["action"], "dispatch")
        # 应该有子 pipeline
        self.assertIsNotNone(self.orch.state.current_sub_pipeline)

    def test_verify_escalate_fix_complete(self):
        """verify escalate → fix → re-verify → gate。"""
        self.orch.record("completed")  # test
        self.orch.record("completed")  # dev

        # verify escalate
        self.orch.record("escalate", summary="3 tests FAIL")
        # 当前 dispatch 是 fix
        self.assertEqual(self.orch.state.current_phase, "fix")

        # fix completed → 回到 verify
        r = self.orch.record("completed", summary="fixed all")
        # 子 pipeline 完成 → advance 到 verify
        self.assertEqual(r["action"], "dispatch")


class TestIntegrationGateFail(unittest.TestCase):
    """gate fail → fix-gate → gate。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        self.orch = _setup_initial_state(self.tmp, "test-change")

    def test_gate_fail_then_fix_gate(self):
        """gate fail → 创建 fix-gate 子 pipeline。"""
        self.orch.record("completed")  # test
        self.orch.record("completed")  # dev
        self.orch.record("completed")  # verify

        # gate fail
        r = self.orch.record("fail", summary="G-1 not met")
        self.assertEqual(r["action"], "dispatch")
        self.assertIsNotNone(self.orch.state.current_sub_pipeline)


class TestOrchestratorProgress(unittest.TestCase):
    """progress 命令。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        self.orch = _setup_initial_state(self.tmp, "test-change")

    def test_progress(self):
        p = self.orch.progress()
        self.assertEqual(p["change"], "test-change")
        self.assertEqual(p["status"], "running")
        self.assertIn("tracks", p)
        self.assertIn("event_count", p)
        self.assertGreaterEqual(p["event_count"], 0)

    def test_progress_after_records(self):
        self.orch.record("completed")
        p = self.orch.progress()
        self.assertGreaterEqual(p["event_count"], 1)


if __name__ == "__main__":
    unittest.main()